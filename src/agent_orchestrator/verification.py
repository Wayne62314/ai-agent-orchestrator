"""Policy-driven acceptance checks and a bounded repair loop."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .delivery import DeliveryReportBuilder
from .errors import ValidationError
from .execution import ExecutionCoordinator
from .models import Event, EventResult, EventType, Task, TaskState, VerificationRecord
from .security import SensitiveDataRedactor
from .service import OrchestratorService
from .store import SQLiteStore, utc_now


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    name: str
    command: tuple[str, ...]
    required: bool = True
    timeout_seconds: int = 300
    max_output_chars: int = 12_000


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    checks: tuple[VerificationCheck, ...]
    max_repair_attempts: int = 2
    ai_review_required: bool = False
    manual_confirmation_required: bool = False

    @classmethod
    def parse(
        cls,
        acceptance_policy: Mapping[str, Any],
        retry_policy: Mapping[str, Any] | None = None,
    ) -> VerificationPolicy:
        raw_checks = acceptance_policy.get("checks")
        if raw_checks is None:
            raw_checks = acceptance_policy.get("commands", [])
        if not isinstance(raw_checks, list):
            raise ValidationError("Acceptance policy checks must be a list.")

        default_timeout = _bounded_int(
            acceptance_policy.get("timeout_seconds", 300),
            name="timeout_seconds",
            minimum=1,
            maximum=3600,
        )
        default_output = _bounded_int(
            acceptance_policy.get("max_output_chars", 12_000),
            name="max_output_chars",
            minimum=256,
            maximum=1_000_000,
        )
        checks: list[VerificationCheck] = []
        names: set[str] = set()
        for index, raw in enumerate(raw_checks, start=1):
            if isinstance(raw, str):
                command = tuple(shlex.split(raw, posix=os.name != "nt"))
                raw = {"name": f"check-{index}", "command": command}
            if not isinstance(raw, Mapping):
                raise ValidationError(f"Check {index} must be an object or string.")
            name = str(raw.get("name", f"check-{index}")).strip()
            if not name or name in names:
                raise ValidationError("Verification check names must be unique.")
            names.add(name)
            command_value = raw.get("command")
            if isinstance(command_value, str):
                command = tuple(
                    shlex.split(command_value, posix=os.name != "nt")
                )
            elif isinstance(command_value, Sequence) and not isinstance(
                command_value, (bytes, bytearray)
            ):
                command = tuple(str(part) for part in command_value)
            else:
                raise ValidationError(f"Check {name!r} needs a command.")
            if not command or any(not part or "\x00" in part for part in command):
                raise ValidationError(f"Check {name!r} has an invalid command.")
            _reject_secret_bearing_command(name, command)
            checks.append(
                VerificationCheck(
                    name=name,
                    command=command,
                    required=bool(raw.get("required", True)),
                    timeout_seconds=_bounded_int(
                        raw.get("timeout_seconds", default_timeout),
                        name=f"{name}.timeout_seconds",
                        minimum=1,
                        maximum=3600,
                    ),
                    max_output_chars=_bounded_int(
                        raw.get("max_output_chars", default_output),
                        name=f"{name}.max_output_chars",
                        minimum=256,
                        maximum=1_000_000,
                    ),
                )
            )

        retries = retry_policy or {}
        max_repairs = acceptance_policy.get(
            "max_repair_attempts",
            retries.get("max_verification_repairs", retries.get("max_attempts", 2)),
        )
        return cls(
            checks=tuple(checks),
            max_repair_attempts=_bounded_int(
                max_repairs,
                name="max_repair_attempts",
                minimum=0,
                maximum=20,
            ),
            ai_review_required=_policy_flag(
                acceptance_policy.get("ai_review", False),
                name="ai_review",
                default=False,
            ),
            manual_confirmation_required=_policy_flag(
                acceptance_policy.get("manual_confirmation", False),
                name="manual_confirmation",
                default=False,
            ),
        )


@dataclass(frozen=True, slots=True)
class CheckExecution:
    status: str
    exit_code: int | None
    timed_out: bool
    output_truncated: bool
    duration_ms: int
    summary: str
    log_path: str
    started_at: str
    ended_at: str


@dataclass(frozen=True, slots=True)
class VerificationSuite:
    task_id: str
    attempt: int
    records: tuple[VerificationRecord, ...]
    transition: EventResult
    report_path: str | None = None

    @property
    def required_passed(self) -> bool:
        return all(
            record.status == "PASSED"
            for record in self.records
            if record.required
        )

    @property
    def failed(self) -> tuple[VerificationRecord, ...]:
        return tuple(record for record in self.records if record.status != "PASSED")


class ConstrainedCommandExecutor:
    """Runs argument arrays without a shell and preserves a complete log."""

    def __init__(
        self,
        *,
        log_root: str | Path | None = None,
        redactor: SensitiveDataRedactor | None = None,
    ):
        self.log_root = Path(log_root).resolve() if log_root else None
        self.redactor = redactor or SensitiveDataRedactor()

    def run(
        self,
        *,
        task_id: str,
        attempt: int,
        check: VerificationCheck,
        workspace_path: str | Path,
    ) -> CheckExecution:
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.is_dir():
            raise ValidationError(f"Workspace {workspace} is not a directory.")
        root = self.log_root or workspace / ".orchestrator" / "logs"
        log_directory = root / task_id / f"attempt-{attempt}"
        log_directory.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in check.name
        ).strip("-") or "check"
        log_path = log_directory / f"{safe_name}-{uuid.uuid4().hex[:8]}.log"

        started_at = utc_now()
        started = time.monotonic()
        exit_code: int | None = None
        timed_out = False
        stdout = ""
        stderr = ""
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                check.command,
                cwd=workspace,
                env=_safe_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    if os.name == "nt"
                    else 0
                ),
            )
            try:
                stdout, stderr = process.communicate(
                    timeout=check.timeout_seconds
                )
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_tree(process)
                stdout, stderr = process.communicate()
        except OSError as exc:
            stderr = f"{type(exc).__name__}: {exc}"

        ended_at = utc_now()
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        status = "PASSED" if exit_code == 0 and not timed_out else "FAILED"
        full_output = self.redactor.redact_text(_format_log(
            check=check,
            status=status,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
        ))
        temporary = log_path.with_suffix(".tmp")
        temporary.write_text(full_output, encoding="utf-8")
        os.replace(temporary, log_path)
        summary, truncated = _truncate(full_output, check.max_output_chars)
        return CheckExecution(
            status=status,
            exit_code=exit_code,
            timed_out=timed_out,
            output_truncated=truncated,
            duration_ms=duration_ms,
            summary=summary,
            log_path=str(log_path),
            started_at=started_at,
            ended_at=ended_at,
        )


class VerificationCoordinator:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        service: OrchestratorService,
        executor: ConstrainedCommandExecutor | None = None,
        reports: DeliveryReportBuilder | None = None,
    ):
        self.store = store
        self.service = service
        self.executor = executor or ConstrainedCommandExecutor()
        self.reports = reports or DeliveryReportBuilder(store)

    def verify(
        self,
        task_id: str,
        *,
        run_id: str | None = None,
    ) -> VerificationSuite:
        task = self.store.get_task(task_id)
        if task.state != TaskState.VERIFYING:
            raise ValidationError(
                f"Task {task_id!r} must be VERIFYING, found {task.state.value}."
            )
        policy = VerificationPolicy.parse(
            task.acceptance_policy,
            task.retry_policy,
        )
        attempt = (
            self.store.get_run(run_id).attempt
            if run_id is not None
            else self.store.next_verification_attempt(task_id)
        )
        records: list[VerificationRecord] = []
        for check in policy.checks:
            result = self.executor.run(
                task_id=task_id,
                attempt=attempt,
                check=check,
                workspace_path=task.workspace_path,
            )
            records.append(
                self.store.record_verification(
                    verification_id=f"verification_{uuid.uuid4().hex}",
                    task_id=task_id,
                    run_id=run_id,
                    attempt=attempt,
                    check_name=check.name,
                    required=check.required,
                    status=result.status,
                    command=check.command,
                    exit_code=result.exit_code,
                    timed_out=result.timed_out,
                    output_truncated=result.output_truncated,
                    duration_ms=result.duration_ms,
                    summary=_persistence_summary(result),
                    log_path=result.log_path,
                    started_at=result.started_at,
                    ended_at=result.ended_at,
                )
            )

        required_passed = all(
            record.status == "PASSED" for record in records if record.required
        )
        ai_review = _latest_ai_review(self.store, task_id, run_id)
        ai_review_passed = (
            not policy.ai_review_required
            or ai_review is not None
            and ai_review.payload.get("status") == "PASSED"
        )
        required_passed = required_passed and ai_review_passed
        if required_passed:
            if policy.manual_confirmation_required:
                event_type = EventType.MANUAL_CONFIRMATION_REQUIRED
                suffix = "manual-confirmation"
            else:
                event_type = EventType.CHECKS_PASSED
                suffix = "passed"
        elif attempt <= policy.max_repair_attempts:
            event_type = EventType.CHECKS_FAILED_RETRYABLE
            suffix = "retryable"
        else:
            event_type = EventType.CHECKS_FAILED_FINAL
            suffix = "final"
        current = self.store.get_task(task_id)
        transition = self.service.process_event(
            Event(
                event_id=f"evt_{uuid.uuid4().hex}",
                task_id=task_id,
                event_type=event_type,
                source="verification-coordinator",
                dedupe_key=f"{task_id}:verification:{attempt}:{suffix}",
                payload={
                    "attempt": attempt,
                    "ai_review_passed": ai_review_passed,
                    "manual_confirmation_required": (
                        policy.manual_confirmation_required
                    ),
                    "failed_checks": [
                        record.check_name
                        for record in records
                        if record.status != "PASSED"
                    ],
                },
                occurred_at=utc_now(),
                expected_version=current.version,
            )
        )
        report_path = None
        if transition.current_state in {
            TaskState.SUCCEEDED,
            TaskState.NEEDS_ATTENTION,
        }:
            report_path = str(self.reports.write(task_id))
        return VerificationSuite(
            task_id=task_id,
            attempt=attempt,
            records=tuple(records),
            transition=transition,
            report_path=report_path,
        )


def _policy_flag(value: Any, *, name: str, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        enabled = value.get("required", value.get("enabled", default))
        if isinstance(enabled, bool):
            return enabled
    raise ValidationError(f"{name} must be a boolean or policy object.")


def _latest_ai_review(
    store: SQLiteStore,
    task_id: str,
    run_id: str | None,
):
    matches = [
        entry
        for entry in store.list_audit(task_id=task_id, limit=5000)
        if entry.kind == "AI_VERIFICATION_RECORDED"
        and (run_id is None or entry.run_id == run_id)
    ]
    return matches[-1] if matches else None


RepairAction = Callable[[Task, VerificationSuite], None]


class LimitedRepairLoop:
    """Repeats verification only after a repair action returns the task to VERIFYING."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        verifier: VerificationCoordinator,
        repair_action: RepairAction,
    ):
        self.store = store
        self.verifier = verifier
        self.repair_action = repair_action

    def run(self, task_id: str, *, run_id: str | None = None) -> VerificationSuite:
        while True:
            suite = self.verifier.verify(task_id, run_id=run_id)
            state = suite.transition.current_state
            if state in {TaskState.SUCCEEDED, TaskState.NEEDS_ATTENTION}:
                return suite
            if state != TaskState.READY:
                raise ValidationError(
                    f"Unexpected verification transition to {state.value}."
                )
            self.repair_action(self.store.get_task(task_id), suite)
            repaired = self.store.get_task(task_id)
            if repaired.state != TaskState.VERIFYING:
                raise ValidationError(
                    "Repair action must complete one run and return the task "
                    f"to VERIFYING; found {repaired.state.value}."
                )


class ExecutionRepairAction:
    """Uses the normal durable execution path for one automatic repair turn."""

    def __init__(
        self,
        coordinator: ExecutionCoordinator,
        *,
        sandbox: str = "workspace-write",
        thread_id: str | None = None,
    ):
        self.coordinator = coordinator
        self.sandbox = sandbox
        self.thread_id = thread_id

    def __call__(self, task: Task, suite: VerificationSuite) -> None:
        started = self.coordinator.start(
            task_id=task.task_id,
            prompt=build_repair_prompt(task, suite),
            sandbox=self.sandbox,
            thread_id=self.thread_id,
        )
        finished = self.coordinator.collect(started)
        if finished.transition.current_state != TaskState.VERIFYING:
            raise ValidationError(
                "Repair execution did not complete into VERIFYING; found "
                f"{finished.transition.current_state.value}."
            )
        if finished.record.thread_id:
            self.thread_id = finished.record.thread_id


def build_repair_prompt(task: Task, suite: VerificationSuite) -> str:
    failures = "\n".join(
        f"- {record.check_name}: {record.status}; log={record.log_path}"
        for record in suite.failed
    )
    return (
        f"Repair task: {task.title}\n"
        f"Objective: {task.objective}\n"
        f"Verification attempt {suite.attempt} failed:\n{failures}\n"
        "Make the smallest in-scope change that addresses these failures. "
        "Do not weaken or remove acceptance checks."
    )


def _safe_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
    }
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in allowed
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _bounded_int(value: Any, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{name} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValidationError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def _reject_secret_bearing_command(name: str, command: Sequence[str]) -> None:
    rendered = " ".join(command).casefold()
    markers = (
        "--token",
        "access_token",
        "api-key",
        "api_key",
        "apikey",
        "authorization:",
        "bearer ",
        "password=",
        "secret=",
    )
    if any(marker in rendered for marker in markers):
        raise ValidationError(
            f"Check {name!r} appears to contain a credential. "
            "Acceptance commands must use credential-free inputs."
        )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def _persistence_summary(result: CheckExecution) -> str:
    """Persist evidence metadata, never raw command output."""

    return (
        f"status={result.status}; exit_code={result.exit_code}; "
        f"timed_out={str(result.timed_out).lower()}; "
        f"output_truncated={str(result.output_truncated).lower()}; "
        f"duration_ms={result.duration_ms}; log_path={result.log_path}"
    )


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    marker = "\n... output truncated; see full log ...\n"
    remaining = limit - len(marker)
    head = remaining // 2
    tail = remaining - head
    return value[:head] + marker + value[-tail:], True


def _format_log(
    *,
    check: VerificationCheck,
    status: str,
    exit_code: int | None,
    timed_out: bool,
    duration_ms: int,
    stdout: str,
    stderr: str,
) -> str:
    command = subprocess.list2cmdline(check.command)
    return (
        f"check: {check.name}\n"
        f"command: {command}\n"
        f"status: {status}\n"
        f"exit_code: {exit_code}\n"
        f"timed_out: {str(timed_out).lower()}\n"
        f"duration_ms: {duration_ms}\n"
        "\n[stdout]\n"
        f"{stdout}\n"
        "\n[stderr]\n"
        f"{stderr}\n"
    )
