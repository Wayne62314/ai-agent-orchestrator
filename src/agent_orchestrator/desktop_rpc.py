"""Strict JSONL RPC and redacted desktop read models."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .adapters.base import RunStatus
from .adapters.codex_sdk import CodexSdkExecutionAdapter
from .authorization import ApprovalService, SideEffectCoordinator
from .checkpoint import CheckpointService
from .errors import NotFoundError, OrchestratorError, ValidationError
from .execution import ExecutionCoordinator
from .maintenance import (
    backup_database,
    backup_to_dict,
    create_diagnostic_bundle,
    list_backups,
    resolve_backup,
    restore_database,
)
from .models import Task, TaskState
from .schema import MIGRATIONS
from .security import SensitiveDataRedactor
from .service import OrchestratorService
from .store import SQLiteStore
from .task_lifecycle import TaskLifecycleService
from .verification import VerificationCoordinator, VerificationPolicy
from .worktrees import WorktreeService

PROTOCOL = "aiao.desktop.v1"
MAX_MESSAGE_BYTES = 1_048_576


def _configure_utf8_standard_streams(*streams: TextIO) -> None:
    """Keep the JSONL protocol independent of the Windows system code page."""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


MAX_PAGE_SIZE = 100


class DesktopQueryService:
    """Build stable, bounded UI read models without exposing persistence rows."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        redactor: SensitiveDataRedactor | None = None,
        account_reader: Callable[[], Mapping[str, Any]] | None = None,
        background_reader: Callable[[], Mapping[str, Any]] | None = None,
        maintenance_reader: Callable[[], Mapping[str, Any]] | None = None,
    ):
        self.store = store
        self.redactor = redactor or SensitiveDataRedactor()
        self.account_reader = account_reader
        self.background_reader = background_reader
        self.maintenance_reader = maintenance_reader

    def system_status(self) -> dict[str, Any]:
        active = self.store.get_active_task()
        background = (
            dict(self.background_reader())
            if self.background_reader is not None
            else {"running": False, "trackedTaskId": None}
        )
        return {
            "protocol": PROTOCOL,
            "appVersion": __version__,
            "schemaVersion": len(MIGRATIONS),
            "healthy": True,
            "activeTaskId": active.task_id if active is not None else None,
            "background": background,
            "capabilities": {
                "account": True,
                "approvals": True,
                "backups": True,
                "checkpoints": True,
                "taskLifecycle": True,
                "worktrees": True,
            },
        }

    def list_tasks(self, *, limit: int = 50) -> dict[str, Any]:
        bounded = _bounded_limit(limit)
        tasks = self.store.list_tasks(limit=bounded)
        return {
            "items": [self._task_summary(task) for task in tasks],
            "nextCursor": None,
        }

    def read_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        summary = self._task_summary(task)
        summary["activities"] = [
            {
                "sequence": item.sequence,
                "kind": item.kind,
                "createdAt": item.created_at,
                "summary": self.redactor.redact(dict(item.payload)),
            }
            for item in self.store.list_audit(task_id=task_id, limit=100)
        ]
        summary["verification"] = [
            {
                "attempt": item.attempt,
                "name": item.check_name,
                "required": item.required,
                "status": item.status,
                "durationMs": item.duration_ms,
                "summary": self.redactor.redact_text(item.summary),
            }
            for item in self.store.list_verifications(task_id)
        ]
        return summary

    def read_task_detail(
        self,
        task_id: str,
        *,
        section: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        self.store.get_task(task_id)
        bounded = _bounded_limit(limit)
        before = _decode_detail_cursor(cursor, section)
        if section == "activities":
            records, next_value = self.store.list_audit_page(
                task_id=task_id,
                limit=bounded,
                before_sequence=before,
            )
            items = [
                {
                    "id": f"audit-{item.sequence}",
                    "sequence": item.sequence,
                    "runId": item.run_id,
                    "title": _activity_title(item.kind),
                    "kind": item.kind,
                    "detail": json.dumps(
                        self.redactor.redact(dict(item.payload)),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "createdAt": item.created_at,
                    "tone": _activity_tone(item.kind),
                }
                for item in records
            ]
        elif section == "runs":
            records, next_value = self.store.list_runs_page(
                task_id,
                limit=bounded,
                before_attempt=before,
            )
            items = [
                self.redactor.redact(
                    {
                        "id": item.run_id,
                        "attempt": item.attempt,
                        "engine": item.engine,
                        "state": item.state.value,
                        "startedAt": item.started_at,
                        "heartbeatAt": item.heartbeat_at,
                        "endedAt": item.ended_at,
                        "exitReason": item.exit_reason,
                        "resultSummary": item.result_summary,
                        "inputCheckpointId": item.input_checkpoint_id,
                    }
                )
                for item in records
            ]
        elif section == "checkpoints":
            records, next_value = self.store.list_checkpoints_page(
                task_id,
                limit=bounded,
                before_sequence=before,
            )
            items = [
                self.redactor.redact(
                    {
                        "id": item.checkpoint_id,
                        "sequence": item.sequence,
                        "runId": item.run_id,
                        "status": item.status,
                        "schemaVersion": item.schema_version,
                        "workspaceRevision": item.workspace_revision,
                        "payloadHash": item.payload_hash,
                        "createdAt": item.created_at,
                        "error": item.error,
                    }
                )
                for item in records
            ]
        elif section == "verifications":
            records, next_value = self.store.list_verifications_page(
                task_id,
                limit=bounded,
                before_rowid=before,
            )
            items = [
                self.redactor.redact(
                    {
                        "id": item.verification_id,
                        "runId": item.run_id,
                        "attempt": item.attempt,
                        "name": item.check_name,
                        "required": item.required,
                        "status": item.status,
                        "command": list(item.command),
                        "exitCode": item.exit_code,
                        "timedOut": item.timed_out,
                        "outputTruncated": item.output_truncated,
                        "durationMs": item.duration_ms,
                        "summary": item.summary,
                        "startedAt": item.started_at,
                        "endedAt": item.ended_at,
                    }
                )
                for item in records
            ]
        elif section == "report":
            if cursor is not None:
                raise ValidationError("The report section does not use a cursor.")
            return self._delivery_report(task_id)
        else:
            raise ValidationError(f"Unsupported task detail section: {section!r}.")
        return {
            "section": section,
            "items": items,
            "nextCursor": _encode_detail_cursor(section, next_value),
        }

    def _delivery_report(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        records = self.store.list_verifications(task_id)
        attempts: list[dict[str, Any]] = []
        for attempt in sorted({item.attempt for item in records}, reverse=True):
            checks = [item for item in records if item.attempt == attempt]
            attempts.append(
                {
                    "attempt": attempt,
                    "passed": sum(item.status == "PASSED" for item in checks),
                    "total": len(checks),
                    "requiredPassed": all(
                        item.status == "PASSED"
                        for item in checks
                        if item.required
                    ),
                }
            )
        return self.redactor.redact(
            {
                "section": "report",
                "taskId": task.task_id,
                "title": task.title,
                "objective": task.objective,
                "state": task.state.value,
                "auditChainValid": self.store.verify_audit_chain(task_id),
                "attempts": attempts,
                "outcome": (
                    "全部必选验收检查已通过。"
                    if task.state == TaskState.SUCCEEDED
                    else (
                        "任务已取消，工作区和已有证据仍然保留。"
                        if task.state == TaskState.CANCELLED
                        else "任务尚未形成最终交付结论。"
                    )
                ),
                "final": task.state.is_terminal,
            }
        )

    def list_approvals(self, *, limit: int = 50) -> dict[str, Any]:
        bounded = _bounded_limit(limit)
        items: list[dict[str, Any]] = []
        for task in self.store.list_tasks(limit=MAX_PAGE_SIZE):
            for approval in self.store.list_approvals(
                task.task_id,
                status="REQUESTED",
            ):
                items.append(
                    {
                        "id": approval.approval_id,
                        "taskId": task.task_id,
                        "taskTitle": task.title,
                        "action": approval.action_type,
                        "risk": approval.risk_summary,
                        "expiresIn": f"有效至 {approval.expires_at}",
                        "rollback": approval.rollback_plan,
                        "hash": approval.action_hash,
                        "requestedAt": approval.requested_at,
                        "expiresAt": approval.expires_at,
                    }
                )
                if len(items) >= bounded:
                    return {"items": items, "nextCursor": None}
        return {"items": items, "nextCursor": None}

    def _task_summary(self, task: Task) -> dict[str, Any]:
        worktree: dict[str, Any] | None
        try:
            record = self.store.get_worktree(task.task_id)
            worktree = {
                "branch": record.branch_name,
                "path": record.worktree_path,
                "repository": record.repository_path,
                "state": record.state.value,
            }
        except NotFoundError:
            worktree = None
        verifications = self.store.list_verifications(task.task_id)
        latest_attempt = max(
            (item.attempt for item in verifications),
            default=None,
        )
        latest_verifications = [
            item for item in verifications if item.attempt == latest_attempt
        ]
        raw_checks = task.acceptance_policy.get(
            "checks",
            task.acceptance_policy.get("commands", []),
        )
        verification_total = (
            len(raw_checks) if isinstance(raw_checks, list) else 0
        )
        verification_passed = sum(
            item.status == "PASSED" for item in latest_verifications
        )
        try:
            checkpoint = self.store.latest_checkpoint(task.task_id)
            checkpoint_label = f"Checkpoint #{checkpoint.sequence} 已安全保存"
        except NotFoundError:
            checkpoint_label = "尚未创建 Checkpoint"
        repository = (
            worktree["repository"] if worktree is not None else task.workspace_path
        )
        branch = worktree["branch"] if worktree is not None else "尚未创建"
        return self.redactor.redact(
            {
                "id": task.task_id,
                "title": task.title,
                "objective": task.objective,
                "state": task.state.value,
                "stateLabel": _state_label(task.state),
                "version": task.version,
                "updatedAt": task.updated_at,
                "workspacePath": task.workspace_path,
                "repository": repository,
                "branch": branch,
                "progress": _state_progress(task.state),
                "nextAction": _next_action(task.state),
                "checkpointLabel": checkpoint_label,
                "verificationPassed": verification_passed,
                "verificationTotal": verification_total,
                "worktree": worktree,
                "acceptancePolicy": dict(task.acceptance_policy),
                "permissionsPolicy": dict(task.permissions_policy),
            }
        )

    def initialize_snapshot(self) -> dict[str, Any]:
        tasks = self.store.list_tasks(limit=20)
        active = self.store.get_active_task()
        active_task = (
            self._task_summary(active)
            if active is not None
            else next(
                (
                    self._task_summary(task)
                    for task in tasks
                    if not task.state.is_terminal
                ),
                None,
            )
        )
        activities: list[dict[str, Any]] = []
        if active_task is not None:
            for entry in self.store.list_audit(
                task_id=str(active_task["id"]),
                limit=8,
            ):
                activities.append(
                    {
                        "id": f"audit-{entry.sequence}",
                        "title": _activity_title(entry.kind),
                        "detail": json.dumps(
                            self.redactor.redact(dict(entry.payload)),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "time": entry.created_at,
                        "tone": _activity_tone(entry.kind),
                    }
                )
        status = self.system_status()
        task_page = {
            "items": [self._task_summary(task) for task in tasks],
            "nextCursor": None,
        }
        approvals = self.list_approvals(limit=20)
        account = (
            dict(self.account_reader())
            if self.account_reader is not None
            else {
                "signedIn": False,
                "accountType": None,
                "email": None,
                "planType": None,
                "requiresOpenaiAuth": True,
            }
        )
        maintenance = (
            dict(self.maintenance_reader())
            if self.maintenance_reader is not None
            else {
                "backups": [],
                "latestBackup": None,
                "restoreAvailable": False,
                "backupRetention": 30,
            }
        )
        return {
            **status,
            "account": account,
            "activeTask": active_task,
            "recentTasks": task_page["items"],
            "activities": activities,
            "approvals": approvals["items"],
            "backupLabel": (
                str(maintenance["latestBackup"]["createdAt"])
                if maintenance.get("latestBackup")
                else "尚无桌面备份"
            ),
            "maintenance": maintenance,
            "tasks": task_page,
        }


@dataclass(slots=True)
class _DesktopLoginState:
    login_id: str
    login_type: str
    handle: Any
    status: str = "PENDING"
    error: str | None = None


class DesktopAccountService:
    """Non-blocking desktop login flow over the official Codex SDK."""

    def __init__(self, client: Any):
        self.client = client
        self._attempts: dict[str, _DesktopLoginState] = {}
        self._lock = threading.RLock()

    def read_account(self) -> Mapping[str, Any]:
        response = self.client.account(refresh_token=False)
        value = self._model_mapping(response)
        account_value = value.get("account")
        account = account_value if isinstance(account_value, Mapping) else None
        return {
            "signedIn": account is not None,
            "accountType": self._optional_text(account, "type"),
            "email": self._optional_text(account, "email"),
            "planType": self._optional_text(account, "planType", "plan_type"),
            "requiresOpenaiAuth": bool(
                value.get(
                    "requiresOpenaiAuth",
                    value.get("requires_openai_auth", True),
                )
            ),
        }

    def start_login(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        login_type = _required_text(params, "type")
        if login_type == "apiKey":
            api_key = _required_text(params, "apiKey")
            self.client.login_api_key(api_key)
            return {
                "loginType": login_type,
                "loginId": None,
                "status": "SUCCEEDED",
                "account": self.read_account(),
            }
        if login_type == "chatgpt":
            handle = self.client.login_chatgpt()
            result = {
                "loginType": login_type,
                "loginId": str(handle.login_id),
                "authorizationUrl": str(handle.auth_url),
                "status": "PENDING",
            }
        elif login_type == "chatgptDeviceCode":
            handle = self.client.login_chatgpt_device_code()
            result = {
                "loginType": login_type,
                "loginId": str(handle.login_id),
                "verificationUrl": str(handle.verification_url),
                "userCode": str(handle.user_code),
                "status": "PENDING",
            }
        else:
            raise ValidationError("Unsupported Codex login type.")
        state = _DesktopLoginState(
            login_id=str(handle.login_id),
            login_type=login_type,
            handle=handle,
        )
        with self._lock:
            self._attempts[state.login_id] = state
        threading.Thread(
            target=self._wait_for_login,
            args=(state,),
            name=f"aiao-login-{state.login_id[:12]}",
            daemon=True,
        ).start()
        return result

    def login_status(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        login_id = _required_text(params, "loginId")
        with self._lock:
            state = self._attempts.get(login_id)
            if state is None:
                raise ValidationError("The Codex login attempt is not active.")
            status = state.status
            error = state.error
            login_type = state.login_type
        result: dict[str, Any] = {
            "loginId": login_id,
            "loginType": login_type,
            "status": status,
        }
        if error:
            result["error"] = SensitiveDataRedactor().redact_text(error)
        if status == "SUCCEEDED":
            result["account"] = self.read_account()
        return result

    def cancel_login(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        login_id = _required_text(params, "loginId")
        with self._lock:
            state = self._attempts.get(login_id)
        if state is None:
            return {"loginId": login_id, "status": "CANCELLED"}
        state.handle.cancel()
        with self._lock:
            state.status = "CANCELLED"
        return {"loginId": login_id, "status": "CANCELLED"}

    def logout(self) -> Mapping[str, Any]:
        self.client.logout()
        return self.read_account()

    def _wait_for_login(self, state: _DesktopLoginState) -> None:
        try:
            result = state.handle.wait()
            value = self._model_mapping(result)
            success = bool(value.get("success", False))
            error = value.get("error")
            with self._lock:
                if state.status == "CANCELLED":
                    return
                state.status = "SUCCEEDED" if success else "FAILED"
                state.error = None if success else str(error or "Codex login failed.")
        except BaseException as exc:
            with self._lock:
                if state.status != "CANCELLED":
                    state.status = "FAILED"
                    state.error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _model_mapping(value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json", by_alias=True)
            return dumped if isinstance(dumped, Mapping) else {}
        return {}

    @staticmethod
    def _optional_text(
        value: Mapping[str, Any] | None,
        *keys: str,
    ) -> str | None:
        if value is None:
            return None
        for key in keys:
            item = value.get(key)
            if item is not None:
                return str(item)
        return None


@dataclass(slots=True)
class _TrackedDesktopRun:
    task_id: str
    sandbox: str
    intent: str = "complete"
    settling: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    error: str | None = None
    heartbeat_error: str | None = None
    collector_thread: threading.Thread | None = None
    heartbeat_thread: threading.Thread | None = None


class DesktopRunCoordinator:
    """Own exactly one background settlement path for each desktop Run."""

    def __init__(
        self,
        lifecycle: TaskLifecycleService,
        *,
        heartbeat_seconds: float = 30,
        recovery_seconds: float = 15,
        control_timeout_seconds: float = 25,
    ):
        if heartbeat_seconds <= 0 or recovery_seconds <= 0:
            raise ValidationError("Background intervals must be positive.")
        self.lifecycle = lifecycle
        self.heartbeat_seconds = heartbeat_seconds
        self.recovery_seconds = recovery_seconds
        self.control_timeout_seconds = control_timeout_seconds
        self._tracked: dict[str, _TrackedDesktopRun] = {}
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._recovery_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._recovery_thread is not None:
            return
        self._recovery_thread = threading.Thread(
            target=self._recovery_loop,
            name="aiao-desktop-recovery",
            daemon=True,
        )
        self._recovery_thread.start()

    def track(self, task_id: str, *, sandbox: str) -> None:
        with self._lock:
            existing = self._tracked.get(task_id)
            if existing is not None and not existing.done.is_set():
                return
            tracked = _TrackedDesktopRun(task_id=task_id, sandbox=sandbox)
            self._tracked[task_id] = tracked
        tracked.collector_thread = threading.Thread(
            target=self._collect,
            args=(tracked,),
            name=f"aiao-collect-{task_id[:16]}",
            daemon=True,
        )
        tracked.heartbeat_thread = threading.Thread(
            target=self._heartbeat,
            args=(tracked,),
            name=f"aiao-heartbeat-{task_id[:16]}",
            daemon=True,
        )
        tracked.collector_thread.start()
        tracked.heartbeat_thread.start()

    def pause(self, task_id: str) -> None:
        self._request_control(task_id, "pause")

    def cancel(self, task_id: str) -> None:
        self._request_control(task_id, "cancel")

    def status(self) -> Mapping[str, Any]:
        with self._lock:
            active = next(
                (
                    item
                    for item in self._tracked.values()
                    if not item.done.is_set()
                ),
                None,
            )
        return {
            "running": active is not None,
            "trackedTaskId": active.task_id if active is not None else None,
            "heartbeatError": (
                SensitiveDataRedactor().redact_text(active.heartbeat_error)
                if active is not None and active.heartbeat_error
                else None
            ),
        }

    def close(self) -> None:
        self._closed.set()
        thread = self._recovery_thread
        if thread is not None:
            thread.join(timeout=2)
        with self._lock:
            tracked_runs = tuple(self._tracked.values())
        for tracked in tracked_runs:
            if tracked.done.is_set():
                for worker in (
                    tracked.collector_thread,
                    tracked.heartbeat_thread,
                ):
                    if worker is not None:
                        worker.join(timeout=2)

    def _request_control(self, task_id: str, intent: str) -> None:
        with self._lock:
            tracked = self._tracked.get(task_id)
        if tracked is None:
            raise ValidationError(
                "The running task is not attached to the desktop coordinator."
            )
        with tracked.lock:
            if tracked.done.is_set() or tracked.settling:
                raise ValidationError(
                    "The Run already completed; refresh before retrying."
                )
            if tracked.intent != "complete":
                if tracked.intent != intent:
                    raise ValidationError(
                        f"The Run is already being {tracked.intent}d."
                    )
            else:
                tracked.intent = intent
        try:
            self.lifecycle.request_interrupt(task_id)
        except BaseException:
            with tracked.lock:
                if not tracked.settling:
                    tracked.intent = "complete"
            raise
        if not tracked.done.wait(timeout=self.control_timeout_seconds):
            raise ValidationError(
                "Codex did not reach a safe interruption boundary in time."
            )
        if tracked.error:
            raise ValidationError(tracked.error)

    def _collect(self, tracked: _TrackedDesktopRun) -> None:
        try:
            result = self.lifecycle.await_result(tracked.task_id)
            with tracked.lock:
                tracked.settling = True
                intent = (
                    "complete"
                    if result.status == RunStatus.COMPLETED
                    else tracked.intent
                )
            self.lifecycle.settle_result(
                tracked.task_id,
                result,
                intent=intent,
                sandbox=tracked.sandbox,
            )
        except BaseException as exc:
            tracked.error = SensitiveDataRedactor().redact_text(
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            tracked.done.set()

    def _heartbeat(self, tracked: _TrackedDesktopRun) -> None:
        while not tracked.done.wait(self.heartbeat_seconds):
            try:
                self.lifecycle.heartbeat(tracked.task_id)
                tracked.heartbeat_error = None
            except BaseException as exc:
                tracked.heartbeat_error = (
                    f"{type(exc).__name__}: {exc}"
                )

    def _recovery_loop(self) -> None:
        while not self._closed.is_set():
            try:
                self.lifecycle.recover_expired()
            except BaseException:
                pass
            self._closed.wait(self.recovery_seconds)


class DesktopCommandService:
    """State-changing desktop operations routed through application services."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        queries: DesktopQueryService,
        lifecycle: TaskLifecycleService,
        approvals: ApprovalService,
        worktrees: WorktreeService | None = None,
        background: DesktopRunCoordinator | None = None,
    ):
        self.store = store
        self.queries = queries
        self.lifecycle = lifecycle
        self.approvals = approvals
        self.worktrees = worktrees or lifecycle.worktrees
        self.background = background

    def inspect_repository(
        self,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        inspection = self.worktrees.inspect_repository(
            _required_text(params, "path")
        )
        return {
            "repository": inspection.repository_path,
            "branch": inspection.branch_name,
            "headRevision": inspection.head_revision,
            "dirty": inspection.is_dirty,
            "dirtyPaths": list(inspection.dirty_paths[:100]),
            "dirtyPathCount": len(inspection.dirty_paths),
        }

    def create_task(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        input_value = _required_mapping(params, "input")
        idempotency_key = _required_text(params, "idempotencyKey")
        if params.get("expectedVersion", 0) != 0:
            raise ValidationError("A new task must use expectedVersion 0.")
        title = _required_text(input_value, "title")
        objective = _required_text(input_value, "objective")
        repository = _required_text(input_value, "repository")
        permission = _required_text(input_value, "permission")
        if permission not in {"read-only", "workspace-write"}:
            raise ValidationError("Task permission must be read-only or workspace-write.")
        checks_value = input_value.get("checks")
        if (
            not isinstance(checks_value, list)
            or not checks_value
            or len(checks_value) > 20
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 2048
                for item in checks_value
            )
        ):
            raise ValidationError("Task checks must contain 1 to 20 commands.")
        max_repairs = input_value.get("maxRepairs", 2)
        if (
            isinstance(max_repairs, bool)
            or not isinstance(max_repairs, int)
            or max_repairs < 0
            or max_repairs > 20
        ):
            raise ValidationError("maxRepairs must be between 0 and 20.")
        acceptance_policy = {
            "checks": [item.strip() for item in checks_value],
            "max_repair_attempts": max_repairs,
        }
        VerificationPolicy.parse(acceptance_policy)
        task_id = (
            "task_"
            + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
        )
        try:
            existing = self.store.get_task(task_id)
        except NotFoundError:
            existing = None
        if existing is not None:
            if existing.title != title or existing.objective != objective:
                raise ValidationError(
                    "This idempotency key was already used for another task."
                )
            return self.queries._task_summary(existing)
        self.lifecycle.create(
            repository_path=repository,
            title=title,
            objective=objective,
            permissions_policy={
                "codex_sandbox": permission,
                "git": {"worktree": {"create": "allow"}},
                "filesystem": {"delete": {"worktree": "ask"}},
            },
            acceptance_policy=acceptance_policy,
            task_id=task_id,
        )
        return self.queries.read_task(task_id)

    def start_task(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        task = self._task_for_action(params, achieved=TaskState.RUNNING)
        if task.state != TaskState.RUNNING:
            self.lifecycle.start(
                task.task_id,
                sandbox=_task_sandbox(task),
            )
            if self.background is not None:
                self.background.track(
                    task.task_id,
                    sandbox=_task_sandbox(task),
                )
        return self.queries.read_task(task.task_id)

    def pause_task(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        task = self._task_for_action(params, achieved=TaskState.PAUSED)
        if task.state != TaskState.PAUSED:
            if self.background is not None:
                self.background.pause(task.task_id)
            else:
                self.lifecycle.pause(task.task_id)
        return self.queries.read_task(task.task_id)

    def resume_task(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        task = self._task_for_action(params, achieved=TaskState.RUNNING)
        if task.state != TaskState.RUNNING:
            self.lifecycle.resume(
                task.task_id,
                sandbox=_task_sandbox(task),
            )
            if self.background is not None:
                self.background.track(
                    task.task_id,
                    sandbox=_task_sandbox(task),
                )
        return self.queries.read_task(task.task_id)

    def cancel_task(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        task = self._task_for_action(params, achieved=TaskState.CANCELLED)
        if task.state != TaskState.CANCELLED:
            if self.background is not None and task.state == TaskState.RUNNING:
                self.background.cancel(task.task_id)
            else:
                self.lifecycle.cancel(task.task_id)
        return self.queries.read_task(task.task_id)

    def decide_approval(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        approval_id = _required_text(params, "approvalId")
        expected_hash = _required_text(params, "expectedActionHash")
        approved = params.get("approved")
        if not isinstance(approved, bool):
            raise ValidationError("approved must be a boolean.")
        approval = self.store.get_approval(approval_id)
        if approval.action_hash != expected_hash:
            raise ValidationError(
                "Approval action hash does not match the displayed action."
            )
        target_status = "APPROVED" if approved else "DENIED"
        if approval.status == target_status:
            return {"decided": True, "status": target_status}
        if approval.status != "REQUESTED":
            raise ValidationError(
                f"Approval is already {approval.status.lower()}."
            )
        decided = self.approvals.decide(
            approval_id,
            approved=approved,
            expected_action_hash=expected_hash,
            decided_by="desktop-user",
        )
        return {"decided": True, "status": decided.status}

    def _task_for_action(
        self,
        params: Mapping[str, Any],
        *,
        achieved: TaskState,
    ) -> Task:
        task = self.store.get_task(_required_text(params, "taskId"))
        if task.state == achieved:
            return task
        expected_version = params.get("expectedVersion")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
        ):
            raise ValidationError("expectedVersion must be an integer.")
        if task.version != expected_version:
            raise ValidationError(
                f"Task version changed from {expected_version} to {task.version}; "
                "refresh before retrying."
            )
        _required_text(params, "idempotencyKey")
        return task


class DesktopMaintenanceService:
    """Product-owned maintenance actions constrained to the app data root."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        data_root: Path,
        background_reader: Callable[[], Mapping[str, Any]] | None = None,
        backup_retention: int = 30,
    ):
        self.store = store
        self.data_root = data_root.expanduser().resolve()
        self.backup_root = self.data_root / "backups"
        self.diagnostic_root = self.data_root / "diagnostics"
        self.background_reader = background_reader
        self.backup_retention = backup_retention
        self._lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        backups = [
            backup_to_dict(item) for item in list_backups(self.backup_root)
        ]
        return {
            "backups": backups,
            "latestBackup": backups[0] if backups else None,
            "restoreAvailable": bool(backups),
            "backupRetention": self.backup_retention,
        }

    def create_backup(self, _params: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            created = backup_database(
                self.store.database_path,
                self.backup_root,
                keep=self.backup_retention,
            )
            self.store.append_audit(
                task_id=None,
                run_id=None,
                kind="DESKTOP_BACKUP_CREATED",
                payload={"backup_id": created.name},
            )
            current = self.read()
        return {
            **current,
            "createdBackupId": created.name,
        }

    def restore_backup(self, params: Mapping[str, Any]) -> dict[str, Any]:
        if _required_text(params, "confirmation") != "RESTORE_BACKUP":
            raise ValidationError(
                "Restore requires the exact RESTORE_BACKUP confirmation."
            )
        backup_id = _required_text(params, "backupId")
        active = self.store.get_active_task()
        background = (
            dict(self.background_reader())
            if self.background_reader is not None
            else {"running": False}
        )
        if active is not None or background.get("running"):
            raise ValidationError(
                "Pause or finish the active task before restoring a backup."
            )
        with self._lock:
            backup = resolve_backup(self.backup_root, backup_id)
            safety = restore_database(
                backup,
                self.store.database_path,
                pid_file=self.data_root / "restore-external.pid",
                confirm_replace=True,
            )
            self.store.initialize()
            self.store.append_audit(
                task_id=None,
                run_id=None,
                kind="DESKTOP_BACKUP_RESTORED",
                payload={
                    "backup_id": backup.name,
                    "safety_backup_created": safety is not None,
                },
            )
            current = self.read()
        return {
            **current,
            "restoredBackupId": backup.name,
            "safetyBackupCreated": safety is not None,
            "restartRecommended": True,
        }

    def export_diagnostics(self, _params: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            path = create_diagnostic_bundle(
                self.store,
                self.diagnostic_root,
                app_version=__version__,
                schema_version=len(MIGRATIONS),
            )
            self.store.append_audit(
                task_id=None,
                run_id=None,
                kind="DESKTOP_DIAGNOSTICS_EXPORTED",
                payload={"file_name": path.name},
            )
        return {
            "exported": True,
            "fileName": path.name,
            "path": str(path),
            "containsSensitiveData": False,
        }


class DesktopRpcApplication:
    """Method whitelist that can later receive mutation application services."""

    def __init__(
        self,
        queries: DesktopQueryService,
        commands: DesktopCommandService | None = None,
        accounts: DesktopAccountService | None = None,
        maintenance: DesktopMaintenanceService | None = None,
    ):
        self.queries = queries
        self._methods: dict[
            str,
            Callable[[Mapping[str, Any]], Mapping[str, Any]],
        ] = {
            "system/initialize": lambda _params: self._initialize(),
            "system/status": lambda _params: self.queries.system_status(),
            "task/list": self._task_list,
            "task/read": self._task_read,
            "task/detail": self._task_detail,
            "approval/list": self._approval_list,
        }
        if commands is not None:
            self._methods.update(
                {
                    "task/create": commands.create_task,
                    "task/start": commands.start_task,
                    "task/pause": commands.pause_task,
                    "task/resume": commands.resume_task,
                    "task/cancel": commands.cancel_task,
                    "approval/decide": commands.decide_approval,
                    "repository/inspect": commands.inspect_repository,
                }
            )
        if accounts is not None:
            self._methods.update(
                {
                    "account/read": lambda _params: accounts.read_account(),
                    "account/login/start": accounts.start_login,
                    "account/login/status": accounts.login_status,
                    "account/login/cancel": accounts.cancel_login,
                    "account/logout": lambda _params: accounts.logout(),
                }
            )
        if maintenance is not None:
            self._methods.update(
                {
                    "maintenance/read": lambda _params: maintenance.read(),
                    "maintenance/backup": maintenance.create_backup,
                    "maintenance/restore": maintenance.restore_backup,
                    "maintenance/diagnostics": maintenance.export_diagnostics,
                }
            )

    def dispatch(
        self,
        method: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        try:
            handler = self._methods[method]
        except KeyError as exc:
            raise RpcRequestError(
                "METHOD_NOT_FOUND",
                f"Desktop method {method!r} is not allowed.",
            ) from exc
        return handler(params)

    def _initialize(self) -> Mapping[str, Any]:
        return self.queries.initialize_snapshot()

    def _task_list(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.queries.list_tasks(limit=_request_limit(params, default=50))

    def _task_read(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.queries.read_task(_required_text(params, "taskId"))

    def _task_detail(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_cursor = params.get("cursor")
        if raw_cursor is not None and not isinstance(raw_cursor, str):
            raise ValidationError("cursor must be a string.")
        return self.queries.read_task_detail(
            _required_text(params, "taskId"),
            section=_required_text(params, "section"),
            limit=_request_limit(params, default=20),
            cursor=raw_cursor,
        )

    def _approval_list(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.queries.list_approvals(
            limit=_request_limit(params, default=50)
        )


class RpcRequestError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DesktopRpcServer:
    """One-request-per-line protocol with bounded, versioned messages."""

    def __init__(
        self,
        application: DesktopRpcApplication,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
    ):
        self.application = application
        self.input_stream = input_stream
        self.output_stream = output_stream

    def serve(self) -> int:
        for raw_line in self.input_stream:
            response = self.handle_line(raw_line)
            self.output_stream.write(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            self.output_stream.flush()
        return 0

    def handle_line(self, raw_line: str) -> dict[str, Any]:
        request_id: str | int | None = None
        try:
            if len(raw_line.encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise RpcRequestError(
                    "MESSAGE_TOO_LARGE",
                    "Desktop RPC messages must be at most 1 MiB.",
                )
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise RpcRequestError(
                    "INVALID_JSON",
                    "Desktop RPC message is not valid JSON.",
                ) from exc
            if not isinstance(value, dict):
                raise RpcRequestError(
                    "INVALID_REQUEST",
                    "Desktop RPC request must be an object.",
                )
            request_id = value.get("id")
            if not isinstance(request_id, (str, int)) or isinstance(
                request_id,
                bool,
            ):
                raise RpcRequestError(
                    "INVALID_REQUEST",
                    "Desktop RPC request needs a string or integer id.",
                )
            if value.get("protocol") != PROTOCOL:
                raise RpcRequestError(
                    "PROTOCOL_MISMATCH",
                    f"Expected protocol {PROTOCOL}.",
                )
            method = value.get("method")
            if not isinstance(method, str) or not method:
                raise RpcRequestError(
                    "INVALID_REQUEST",
                    "Desktop RPC method must be a non-empty string.",
                )
            params = value.get("params", {})
            if not isinstance(params, dict):
                raise RpcRequestError(
                    "INVALID_REQUEST",
                    "Desktop RPC params must be an object.",
                )
            result = self.application.dispatch(method, params)
            return {
                "protocol": PROTOCOL,
                "id": request_id,
                "result": result,
            }
        except RpcRequestError as exc:
            return self._error(request_id, exc.code, str(exc))
        except (OrchestratorError, ValueError, TypeError) as exc:
            return self._error(
                request_id,
                "REQUEST_REJECTED",
                str(exc),
            )
        except BaseException:
            return self._error(
                request_id,
                "INTERNAL_ERROR",
                "The local sidecar could not complete the request.",
            )

    @staticmethod
    def _error(
        request_id: str | int | None,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "id": request_id,
            "error": {
                "code": code,
                "message": SensitiveDataRedactor().redact_text(message),
            },
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Agent Orchestrator desktop RPC")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--owner", default="desktop-sidecar")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="validate the packaged SDK and Codex runtime, then exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_standard_streams(sys.stdin, sys.stdout, sys.stderr)
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.self_check:
        status = CodexSdkExecutionAdapter().runtime_status()
        print(
            json.dumps(
                {
                    "healthy": True,
                    "applicationVersion": __version__,
                    "codexSdkVersion": status["sdkVersion"],
                    "codexRuntime": {
                        "version": status["runtimeVersion"],
                        "file": status["runtimeFile"],
                    },
                },
                separators=(",", ":"),
            )
        )
        return 0
    if arguments.db is None:
        parser.error("--db is required unless --self-check is used")
    data_root = (
        arguments.data_root.expanduser().resolve()
        if arguments.data_root is not None
        else arguments.db.expanduser().resolve().parent
    )
    data_root.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(arguments.db.expanduser().resolve())
    service = OrchestratorService(store)
    service.initialize()
    approvals = ApprovalService(store=store, service=service)
    side_effects = SideEffectCoordinator(
        store=store,
        approvals=approvals,
    )
    adapter = CodexSdkExecutionAdapter()
    accounts = DesktopAccountService(adapter.session_client())
    lifecycle = TaskLifecycleService(
        store=store,
        service=service,
        execution=ExecutionCoordinator(
            store=store,
            service=service,
            adapter=adapter,
            owner=arguments.owner,
        ),
        worktrees=WorktreeService(
            store=store,
            side_effects=side_effects,
            managed_root=data_root / "worktrees",
        ),
        checkpoints=CheckpointService(
            store,
            data_root / "checkpoints",
        ),
        verifier=VerificationCoordinator(
            store=store,
            service=service,
        ),
        owner=arguments.owner,
    )
    background = DesktopRunCoordinator(lifecycle)
    background.start()
    maintenance = DesktopMaintenanceService(
        store=store,
        data_root=data_root,
        background_reader=background.status,
    )
    queries = DesktopQueryService(
        store,
        account_reader=accounts.read_account,
        background_reader=background.status,
        maintenance_reader=maintenance.read,
    )
    server = DesktopRpcServer(
        DesktopRpcApplication(
            queries,
            DesktopCommandService(
                store=store,
                queries=queries,
                lifecycle=lifecycle,
                approvals=approvals,
                background=background,
            ),
            accounts,
            maintenance,
        ),
        input_stream=sys.stdin,
        output_stream=sys.stdout,
    )
    try:
        return server.serve()
    finally:
        background.close()
        adapter.close()


def _required_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationError(f"{key} must be a non-empty string.")
    return item.strip()


def _required_mapping(
    value: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValidationError(f"{key} must be an object.")
    return item


def _bounded_limit(value: int) -> int:
    if isinstance(value, bool) or value < 1 or value > MAX_PAGE_SIZE:
        raise ValidationError(
            f"Desktop page size must be between 1 and {MAX_PAGE_SIZE}."
        )
    return value


def _request_limit(params: Mapping[str, Any], *, default: int) -> int:
    value = params.get("limit", default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("limit must be an integer.")
    return _bounded_limit(value)


def _encode_detail_cursor(section: str, value: int | None) -> str | None:
    return f"{section}:{value}" if value is not None else None


def _decode_detail_cursor(cursor: str | None, section: str) -> int | None:
    if cursor is None:
        return None
    prefix, separator, raw_value = cursor.partition(":")
    if separator != ":" or prefix != section:
        raise ValidationError("The detail cursor does not match this section.")
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValidationError("The detail cursor is invalid.") from exc
    if value < 1:
        raise ValidationError("The detail cursor is invalid.")
    return value


def _state_label(state: TaskState) -> str:
    return {
        TaskState.DRAFT: "设置任务",
        TaskState.READY: "可以开始",
        TaskState.RUNNING: "Codex 正在工作",
        TaskState.PAUSED: "已安全暂停",
        TaskState.WAITING_FOR_SIGNAL: "等待恢复条件",
        TaskState.WAITING_FOR_APPROVAL: "等待你的批准",
        TaskState.VERIFYING: "正在自动验收",
        TaskState.NEEDS_ATTENTION: "需要处理",
        TaskState.SUCCEEDED: "已完成",
        TaskState.CANCELLED: "已取消",
    }[state]


def _state_progress(state: TaskState) -> int:
    return {
        TaskState.DRAFT: 2,
        TaskState.READY: 8,
        TaskState.RUNNING: 55,
        TaskState.PAUSED: 55,
        TaskState.WAITING_FOR_SIGNAL: 58,
        TaskState.WAITING_FOR_APPROVAL: 60,
        TaskState.VERIFYING: 82,
        TaskState.NEEDS_ATTENTION: 82,
        TaskState.SUCCEEDED: 100,
        TaskState.CANCELLED: 100,
    }[state]


def _next_action(state: TaskState) -> str:
    return {
        TaskState.DRAFT: "完成任务设置",
        TaskState.READY: "确认后开始 Codex 任务",
        TaskState.RUNNING: "等待 Codex 完成本轮工作",
        TaskState.PAUSED: "从最近 Checkpoint 恢复",
        TaskState.WAITING_FOR_SIGNAL: "等待可信恢复信号",
        TaskState.WAITING_FOR_APPROVAL: "处理待审批的高风险动作",
        TaskState.VERIFYING: "等待自动验收完成",
        TaskState.NEEDS_ATTENTION: "查看失败证据并决定下一步",
        TaskState.SUCCEEDED: "查看交付报告",
        TaskState.CANCELLED: "检查保留的任务 Worktree",
    }[state]


def _task_sandbox(task: Task) -> str:
    value = task.permissions_policy.get("codex_sandbox", "read-only")
    return (
        value
        if isinstance(value, str)
        and value in {"read-only", "workspace-write"}
        else "read-only"
    )


def _activity_title(kind: str) -> str:
    return {
        "EVENT_APPLIED": "任务状态已更新",
        "RUN_CREATED": "Codex Run 已创建",
        "RUN_FINISHED": "Codex Run 已结束",
        "CHECKPOINT_READY": "Checkpoint 已安全保存",
        "VERIFICATION_RECORDED": "验收结果已记录",
        "APPROVAL_REQUESTED": "需要你的批准",
    }.get(kind, kind.replace("_", " ").title())


def _activity_tone(kind: str) -> str:
    if "FAILED" in kind or "REJECTED" in kind:
        return "waiting"
    if "VERIFICATION" in kind or "CHECKPOINT" in kind:
        return "success"
    if "RUN" in kind or "EVENT" in kind:
        return "active"
    return "neutral"


if __name__ == "__main__":
    raise SystemExit(main())
