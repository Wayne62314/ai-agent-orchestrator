"""Command-line control surface for the durable orchestration core."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .adapters.fake import FakeExecutionAdapter
from .authorization import (
    ActionRequest,
    ApprovalService,
    SideEffectCoordinator,
)
from .checkpoint import CheckpointService
from .delivery import DeliveryReportBuilder
from .errors import OrchestratorError, ValidationError
from .execution import ExecutionCoordinator
from .external_events import TrustedEventService
from .models import (
    Event,
    EventType,
    ExternalEventKind,
    SideEffectStatus,
    SignalWaitStatus,
    TaskState,
)
from .resume import ResumePackageBuilder
from .service import OrchestratorService
from .state_machine import allowed_events
from .store import SQLiteStore, utc_now
from .verification import VerificationCoordinator
from .webhook_server import run_webhook_server
from .worker import RecoveryWorker
from .workspace import WorkspaceInspector, WorkspaceSnapshot


def _default_database() -> Path:
    configured = os.environ.get("ORCHESTRATOR_DB")
    if configured:
        return Path(configured)
    return Path.cwd() / ".orchestrator" / "state.db"


def _json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("Expected a JSON object.")
    return value


def _json_array(raw: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(value, list):
        raise argparse.ArgumentTypeError("Expected a JSON array.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-orchestrator",
        description="Inspect and drive the persistent stage-one orchestration core.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=_default_database(),
        help="SQLite state database path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="Initialize or migrate the database.")

    task = commands.add_parser("task", help="Create and inspect tasks.")
    task_commands = task.add_subparsers(dest="task_command", required=True)

    create = task_commands.add_parser("create", help="Create a draft task.")
    create.add_argument("--title", required=True)
    create.add_argument("--objective", required=True)
    create.add_argument("--workspace", type=Path, required=True)
    create.add_argument("--permissions", type=_json_object, default={})
    create.add_argument("--acceptance", type=_json_object, default={})
    create.add_argument("--retry", type=_json_object, default={"max_attempts": 3})
    create.add_argument("--ready", action="store_true")

    show = task_commands.add_parser("show", help="Show one task.")
    show.add_argument("task_id")

    listing = task_commands.add_parser("list", help="List recent tasks.")
    listing.add_argument(
        "--state",
        action="append",
        choices=[state.value for state in TaskState],
    )
    listing.add_argument("--limit", type=int, default=100)

    event = commands.add_parser("event", help="Ingest an idempotent task event.")
    event_commands = event.add_subparsers(dest="event_command", required=True)
    emit = event_commands.add_parser("emit", help="Apply one event.")
    emit.add_argument("task_id")
    emit.add_argument("event_type", choices=[item.value for item in EventType])
    emit.add_argument("--source", default="cli")
    emit.add_argument("--event-id")
    emit.add_argument("--dedupe-key", required=True)
    emit.add_argument("--payload", type=_json_object, default={})
    emit.add_argument("--expected-version", type=int)

    allowed = event_commands.add_parser(
        "allowed",
        help="Show legal events for the task's current state.",
    )
    allowed.add_argument("task_id")

    wait = commands.add_parser(
        "wait",
        help="Register and inspect authenticated external-signal waits.",
    )
    wait_commands = wait.add_subparsers(dest="wait_command", required=True)
    wait_register = wait_commands.add_parser(
        "register",
        help="Move a running task into a durable signal wait.",
    )
    wait_register.add_argument("task_id")
    wait_register.add_argument("--provider", required=True)
    wait_register.add_argument(
        "--kind",
        choices=[item.value for item in ExternalEventKind],
        required=True,
    )
    wait_register.add_argument("--subject", required=True)
    wait_register.add_argument("--condition", type=_json_object, required=True)
    wait_register.add_argument("--timeout-seconds", type=int, required=True)
    wait_list = wait_commands.add_parser("list", help="List a task's waits.")
    wait_list.add_argument("task_id")
    wait_list.add_argument(
        "--status",
        choices=[item.value for item in SignalWaitStatus],
    )
    wait_expire = wait_commands.add_parser(
        "expire",
        help="Escalate waits whose durable deadline has passed.",
    )
    wait_expire.add_argument("--observed-at")

    external_event = commands.add_parser(
        "external-event",
        help="Inspect authenticated external event receipts.",
    )
    external_event_commands = external_event.add_subparsers(
        dest="external_event_command",
        required=True,
    )
    external_event_list = external_event_commands.add_parser(
        "list",
        help="List redacted external event receipts for a task.",
    )
    external_event_list.add_argument("task_id")

    audit = commands.add_parser("audit", help="Inspect audit history.")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_commands.add_parser("list", help="List task audit entries.")
    audit_list.add_argument("task_id")
    audit_list.add_argument("--limit", type=int, default=200)
    audit_verify = audit_commands.add_parser(
        "verify",
        help="Verify the task's hash chain.",
    )
    audit_verify.add_argument("task_id")

    run = commands.add_parser("run", help="Inspect and recover durable runs.")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    run_show = run_commands.add_parser("show", help="Show one run.")
    run_show.add_argument("run_id")
    run_commands.add_parser("expired", help="List active runs with expired leases.")
    run_commands.add_parser(
        "recover-expired",
        help="Abandon expired runs and move running tasks to attention.",
    )

    checkpoint = commands.add_parser(
        "checkpoint",
        help="Create and verify task checkpoints.",
    )
    checkpoint_commands = checkpoint.add_subparsers(
        dest="checkpoint_command",
        required=True,
    )
    checkpoint_create = checkpoint_commands.add_parser(
        "create",
        help="Capture current workspace and create a checkpoint.",
    )
    checkpoint_create.add_argument("task_id")
    checkpoint_create.add_argument("--run-id")
    checkpoint_create.add_argument(
        "--root",
        type=Path,
        default=Path(".orchestrator/checkpoints"),
    )
    checkpoint_create.add_argument("--git", type=Path)
    checkpoint_create.add_argument("--progress", type=_json_object, required=True)
    checkpoint_create.add_argument("--decisions", type=_json_array, default=[])
    checkpoint_create.add_argument(
        "--current-block",
        type=_json_object,
        required=True,
    )
    checkpoint_create.add_argument(
        "--next-action",
        type=_json_object,
        required=True,
    )
    checkpoint_create.add_argument(
        "--verification",
        type=_json_object,
        default={},
    )
    checkpoint_create.add_argument(
        "--permissions",
        type=_json_object,
        default={},
    )
    checkpoint_create.add_argument("--relevant-file", action="append", default=[])

    checkpoint_latest = checkpoint_commands.add_parser(
        "latest",
        help="Show and optionally verify the latest checkpoint.",
    )
    checkpoint_latest.add_argument("task_id")
    checkpoint_latest.add_argument("--verify", action="store_true")

    resume = commands.add_parser("resume", help="Build a verified Resume Package.")
    resume_commands = resume.add_subparsers(dest="resume_command", required=True)
    resume_build = resume_commands.add_parser(
        "build",
        help="Compare workspace facts and render a Resume Package.",
    )
    resume_build.add_argument("task_id")
    resume_build.add_argument("--thread-id")
    resume_build.add_argument("--git", type=Path)

    verify = commands.add_parser(
        "verify",
        help="Run acceptance checks and inspect their evidence.",
    )
    verify_commands = verify.add_subparsers(
        dest="verify_command",
        required=True,
    )
    verify_run = verify_commands.add_parser(
        "run",
        help="Run one policy-defined verification attempt.",
    )
    verify_run.add_argument("task_id")
    verify_run.add_argument("--run-id")
    verify_list = verify_commands.add_parser(
        "list",
        help="List persisted verification results.",
    )
    verify_list.add_argument("task_id")
    verify_list.add_argument("--attempt", type=int)
    verify_report = verify_commands.add_parser(
        "report",
        help="Write an evidence-focused delivery report.",
    )
    verify_report.add_argument("task_id")

    approval = commands.add_parser(
        "approval",
        help="Request and decide hash-bound action approvals.",
    )
    approval_commands = approval.add_subparsers(
        dest="approval_command",
        required=True,
    )
    approval_request = approval_commands.add_parser(
        "request",
        help="Evaluate one proposed action and request approval when required.",
    )
    approval_request.add_argument("task_id")
    approval_request.add_argument("action_type")
    approval_request.add_argument("--logical-step", required=True)
    approval_request.add_argument("--parameters", type=_json_object, default={})
    approval_request.add_argument("--risk", required=True)
    approval_request.add_argument("--rollback", required=True)
    approval_request.add_argument("--ttl-seconds", type=int, default=900)
    approval_show = approval_commands.add_parser(
        "show",
        help="Show one approval and its bound action hash.",
    )
    approval_show.add_argument("approval_id")
    approval_list = approval_commands.add_parser(
        "list",
        help="List approvals for a task.",
    )
    approval_list.add_argument("task_id")
    approval_list.add_argument("--status")
    for decision_name in ("approve", "deny"):
        decision = approval_commands.add_parser(
            decision_name,
            help=f"{decision_name.title()} one exact action hash.",
        )
        decision.add_argument("approval_id")
        decision.add_argument("--action-hash", required=True)
        decision.add_argument("--by", required=True)

    effect = commands.add_parser(
        "effect",
        help="Inspect and reconcile the idempotent side-effect ledger.",
    )
    effect_commands = effect.add_subparsers(
        dest="effect_command",
        required=True,
    )
    effect_show = effect_commands.add_parser("show", help="Show one side effect.")
    effect_show.add_argument("effect_id")
    effect_list = effect_commands.add_parser(
        "list",
        help="List side effects for a task.",
    )
    effect_list.add_argument("task_id")
    effect_list.add_argument(
        "--status",
        choices=[status.value for status in SideEffectStatus],
    )
    recover_effects = effect_commands.add_parser(
        "recover-stale",
        help="Mark stale PENDING effects UNKNOWN after a restart.",
    )
    recover_effects.add_argument("--older-than-seconds", type=int, default=300)
    reconcile_effect = effect_commands.add_parser(
        "reconcile",
        help="Record an externally confirmed outcome for an UNKNOWN effect.",
    )
    reconcile_effect.add_argument("effect_id")
    reconcile_effect.add_argument(
        "--outcome",
        choices=["succeeded", "failed"],
        required=True,
    )
    reconcile_effect.add_argument("--external-result-id")

    serve = commands.add_parser(
        "serve",
        help="Run the authenticated GitHub webhook service and recovery worker.",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--worker-interval-seconds", type=float, default=5.0)
    serve.add_argument(
        "--github-secret-env",
        default="ORCHESTRATOR_GITHUB_WEBHOOK_SECRET",
        help="Environment variable containing the webhook secret.",
    )

    worker = commands.add_parser(
        "worker",
        help="Run one durable recovery scan.",
    )
    worker_commands = worker.add_subparsers(dest="worker_command", required=True)
    worker_tick = worker_commands.add_parser(
        "tick",
        help="Expire overdue waits and recover stale side-effect records.",
    )
    worker_tick.add_argument("--observed-at")
    worker_tick.add_argument("--stale-effect-seconds", type=int, default=300)

    return parser


def _serialize(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, TaskState | EventType):
        return value.value
    return value


def _print(value: Any, *, json_output: bool) -> None:
    serialized = _serialize(value)
    if json_output:
        print(json.dumps(serialized, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(serialized, list):
        if not serialized:
            print("No records.")
            return
        for item in serialized:
            if isinstance(item, dict):
                print(
                    "  ".join(
                        f"{key}={item[key]}"
                        for key in (
                            "task_id",
                            "state",
                            "version",
                            "title",
                        )
                        if key in item
                    )
                    or json.dumps(item, ensure_ascii=False, sort_keys=True)
                )
            else:
                print(item)
        return
    if isinstance(serialized, dict):
        for key, item in serialized.items():
            if isinstance(item, (dict, list)):
                rendered = json.dumps(item, ensure_ascii=False, sort_keys=True)
            else:
                rendered = item
            print(f"{key}: {rendered}")
        return
    print(serialized)


def run(arguments: argparse.Namespace) -> int:
    store = SQLiteStore(arguments.db)
    service = OrchestratorService(store)
    service.initialize()

    if arguments.command == "init":
        _print(
            {"database": str(store.database_path), "status": "initialized"},
            json_output=arguments.json,
        )
        return 0

    if arguments.command == "task":
        if arguments.task_command == "create":
            task = service.create_task(
                title=arguments.title,
                objective=arguments.objective,
                workspace_path=arguments.workspace,
                permissions_policy=arguments.permissions,
                acceptance_policy=arguments.acceptance,
                retry_policy=arguments.retry,
            )
            if arguments.ready:
                service.validate_task(task.task_id)
                task = store.get_task(task.task_id)
            _print(task, json_output=arguments.json)
            return 0
        if arguments.task_command == "show":
            _print(store.get_task(arguments.task_id), json_output=arguments.json)
            return 0
        if arguments.task_command == "list":
            states = (
                [TaskState(value) for value in arguments.state]
                if arguments.state
                else None
            )
            _print(
                store.list_tasks(states=states, limit=arguments.limit),
                json_output=arguments.json,
            )
            return 0

    if arguments.command == "event":
        if arguments.event_command == "emit":
            result = service.process_event(
                Event(
                    event_id=arguments.event_id or f"evt_{uuid.uuid4().hex}",
                    task_id=arguments.task_id,
                    event_type=EventType(arguments.event_type),
                    source=arguments.source,
                    dedupe_key=arguments.dedupe_key,
                    payload=arguments.payload,
                    occurred_at=utc_now(),
                    expected_version=arguments.expected_version,
                )
            )
            _print(result, json_output=arguments.json)
            return 0 if result.outcome == "APPLIED" or result.duplicate else 2
        if arguments.event_command == "allowed":
            task = store.get_task(arguments.task_id)
            _print(
                {
                    "task_id": task.task_id,
                    "state": task.state.value,
                    "allowed_events": [
                        item.value for item in allowed_events(task.state)
                    ],
                },
                json_output=arguments.json,
            )
            return 0

    if arguments.command == "wait":
        trusted_events = TrustedEventService(store=store, service=service)
        if arguments.wait_command == "register":
            _print(
                trusted_events.register_wait(
                    arguments.task_id,
                    provider=arguments.provider,
                    kind=ExternalEventKind(arguments.kind),
                    subject=arguments.subject,
                    condition=arguments.condition,
                    timeout_seconds=arguments.timeout_seconds,
                ),
                json_output=arguments.json,
            )
            return 0
        if arguments.wait_command == "list":
            _print(
                store.list_signal_waits(
                    arguments.task_id,
                    status=(
                        SignalWaitStatus(arguments.status)
                        if arguments.status
                        else None
                    ),
                ),
                json_output=arguments.json,
            )
            return 0
        if arguments.wait_command == "expire":
            _print(
                trusted_events.expire_waits(observed_at=arguments.observed_at),
                json_output=arguments.json,
            )
            return 0

    if (
        arguments.command == "external-event"
        and arguments.external_event_command == "list"
    ):
        _print(
            store.list_external_events(arguments.task_id),
            json_output=arguments.json,
        )
        return 0

    if arguments.command == "audit":
        if arguments.audit_command == "list":
            _print(
                store.list_audit(
                    task_id=arguments.task_id,
                    limit=arguments.limit,
                ),
                json_output=arguments.json,
            )
            return 0
        if arguments.audit_command == "verify":
            verified = store.verify_audit_chain(arguments.task_id)
            _print(
                {"task_id": arguments.task_id, "valid": verified},
                json_output=arguments.json,
            )
            return 0 if verified else 3

    if arguments.command == "run":
        if arguments.run_command == "show":
            _print(store.get_run(arguments.run_id), json_output=arguments.json)
            return 0
        if arguments.run_command == "expired":
            _print(store.list_expired_runs(), json_output=arguments.json)
            return 0
        if arguments.run_command == "recover-expired":
            coordinator = ExecutionCoordinator(
                store=store,
                service=service,
                adapter=FakeExecutionAdapter(),
                owner="cli-recovery",
            )
            _print(coordinator.recover_expired(), json_output=arguments.json)
            return 0

    if arguments.command == "checkpoint":
        if arguments.checkpoint_command == "create":
            task = store.get_task(arguments.task_id)
            snapshot = WorkspaceInspector(arguments.git).snapshot(
                task.workspace_path
            )
            checkpoint_service = CheckpointService(store, arguments.root)
            record = checkpoint_service.create(
                task=task,
                run_id=arguments.run_id,
                workspace=snapshot,
                progress=arguments.progress,
                decisions=arguments.decisions,
                current_block=arguments.current_block,
                next_action=arguments.next_action,
                verification=arguments.verification,
                permissions=arguments.permissions,
                relevant_files=arguments.relevant_file,
            )
            _print(record, json_output=arguments.json)
            return 0
        if arguments.checkpoint_command == "latest":
            record = store.latest_checkpoint(arguments.task_id)
            result: dict[str, Any] = {"checkpoint": _serialize(record)}
            if arguments.verify:
                checkpoint_service = CheckpointService(
                    store,
                    Path(record.payload_path).parent,
                )
                checkpoint_service.load(record)
                result["verified"] = True
            _print(result, json_output=arguments.json)
            return 0

    if arguments.command == "resume":
        if arguments.resume_command == "build":
            task = store.get_task(arguments.task_id)
            record = store.latest_checkpoint(task.task_id)
            checkpoint_service = CheckpointService(
                store,
                Path(record.payload_path).parent,
            )
            payload = checkpoint_service.load(record)
            workspace_value = payload.get("workspace")
            if not isinstance(workspace_value, dict):
                raise ValidationError("Checkpoint workspace must be an object.")
            baseline = WorkspaceSnapshot.from_dict(workspace_value)
            inspector = WorkspaceInspector(arguments.git)
            current = inspector.snapshot(task.workspace_path)
            relevant = workspace_value.get("relevant_files") or []
            if not isinstance(relevant, list):
                raise ValidationError("Checkpoint relevant_files must be an array.")
            drift = inspector.compare(
                baseline,
                current,
                relevant_files=tuple(str(item) for item in relevant),
            )
            package = ResumePackageBuilder().build(
                task=task,
                checkpoint=record,
                payload=payload,
                current_workspace=current,
                drift=drift,
                thread_id=arguments.thread_id,
            )
            _print(package, json_output=arguments.json)
            return 0

    if arguments.command == "verify":
        if arguments.verify_command == "run":
            suite = VerificationCoordinator(
                store=store,
                service=service,
            ).verify(arguments.task_id, run_id=arguments.run_id)
            _print(suite, json_output=arguments.json)
            return (
                0
                if suite.transition.current_state == TaskState.SUCCEEDED
                else 2
            )
        if arguments.verify_command == "list":
            _print(
                store.list_verifications(
                    arguments.task_id,
                    attempt=arguments.attempt,
                ),
                json_output=arguments.json,
            )
            return 0
        if arguments.verify_command == "report":
            path = DeliveryReportBuilder(store).write(arguments.task_id)
            _print(
                {"task_id": arguments.task_id, "report_path": str(path)},
                json_output=arguments.json,
            )
            return 0

    if arguments.command == "approval":
        approvals = ApprovalService(store=store, service=service)
        if arguments.approval_command == "request":
            result = approvals.request(
                arguments.task_id,
                ActionRequest(
                    action_type=arguments.action_type,
                    logical_step=arguments.logical_step,
                    parameters=arguments.parameters,
                    risk_summary=arguments.risk,
                    rollback_plan=arguments.rollback,
                ),
                ttl_seconds=arguments.ttl_seconds,
            )
            _print(result, json_output=arguments.json)
            return 0
        if arguments.approval_command == "show":
            _print(
                store.get_approval(arguments.approval_id),
                json_output=arguments.json,
            )
            return 0
        if arguments.approval_command == "list":
            _print(
                store.list_approvals(
                    arguments.task_id,
                    status=arguments.status,
                ),
                json_output=arguments.json,
            )
            return 0
        if arguments.approval_command in {"approve", "deny"}:
            record = approvals.decide(
                arguments.approval_id,
                approved=arguments.approval_command == "approve",
                expected_action_hash=arguments.action_hash,
                decided_by=arguments.by,
            )
            _print(record, json_output=arguments.json)
            return 0

    if arguments.command == "effect":
        effects = SideEffectCoordinator(
            store=store,
            approvals=ApprovalService(store=store, service=service),
        )
        if arguments.effect_command == "show":
            _print(
                store.get_side_effect(arguments.effect_id),
                json_output=arguments.json,
            )
            return 0

    if arguments.command == "worker" and arguments.worker_command == "tick":
        result = RecoveryWorker(
            store=store,
            service=service,
            stale_effect_seconds=arguments.stale_effect_seconds,
        ).tick(observed_at=arguments.observed_at)
        _print(result, json_output=arguments.json)
        return 0

    if arguments.command == "serve":
        secret_value = os.environ.get(arguments.github_secret_env)
        if not secret_value:
            raise ValidationError(
                f"Webhook secret environment variable "
                f"{arguments.github_secret_env!r} is not set."
            )
        if arguments.port < 0 or arguments.port > 65535:
            raise ValidationError("Webhook server port must be between 0 and 65535.")
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        run_webhook_server(
            database_path=arguments.db,
            host=arguments.host,
            port=arguments.port,
            secret=secret_value.encode("utf-8"),
            worker_interval_seconds=arguments.worker_interval_seconds,
        )
        return 0
        if arguments.effect_command == "list":
            _print(
                store.list_side_effects(
                    arguments.task_id,
                    status=(
                        SideEffectStatus(arguments.status)
                        if arguments.status
                        else None
                    ),
                ),
                json_output=arguments.json,
            )
            return 0
        if arguments.effect_command == "recover-stale":
            _print(
                effects.recover_stale(
                    older_than_seconds=arguments.older_than_seconds,
                ),
                json_output=arguments.json,
            )
            return 0
        if arguments.effect_command == "reconcile":
            _print(
                effects.reconcile(
                    arguments.effect_id,
                    succeeded=arguments.outcome == "succeeded",
                    external_result_id=arguments.external_result_id,
                ),
                json_output=arguments.json,
            )
            return 0

    raise ValidationError("Unsupported command.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return run(arguments)
    except OrchestratorError as exc:
        if arguments.json:
            print(
                json.dumps(
                    {"error": type(exc).__name__, "message": str(exc)},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
