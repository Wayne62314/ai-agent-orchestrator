"""Strict JSONL RPC and redacted desktop read models."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .errors import NotFoundError, OrchestratorError, ValidationError
from .models import Task, TaskState
from .schema import MIGRATIONS
from .security import SensitiveDataRedactor
from .store import SQLiteStore

PROTOCOL = "aiao.desktop.v1"
MAX_MESSAGE_BYTES = 1_048_576
MAX_PAGE_SIZE = 100


class DesktopQueryService:
    """Build stable, bounded UI read models without exposing persistence rows."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        redactor: SensitiveDataRedactor | None = None,
    ):
        self.store = store
        self.redactor = redactor or SensitiveDataRedactor()

    def system_status(self) -> dict[str, Any]:
        active = self.store.get_active_task()
        return {
            "protocol": PROTOCOL,
            "appVersion": __version__,
            "schemaVersion": len(MIGRATIONS),
            "healthy": True,
            "activeTaskId": active.task_id if active is not None else None,
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
                "worktree": worktree,
                "acceptancePolicy": dict(task.acceptance_policy),
                "permissionsPolicy": dict(task.permissions_policy),
            }
        )


class DesktopRpcApplication:
    """Method whitelist that can later receive mutation application services."""

    def __init__(self, queries: DesktopQueryService):
        self.queries = queries
        self._methods: dict[
            str,
            Callable[[Mapping[str, Any]], Mapping[str, Any]],
        ] = {
            "system/initialize": lambda _params: self._initialize(),
            "system/status": lambda _params: self.queries.system_status(),
            "task/list": self._task_list,
            "task/read": self._task_read,
            "approval/list": self._approval_list,
        }

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
        return {
            **self.queries.system_status(),
            "tasks": self.queries.list_tasks(limit=20),
            "approvals": self.queries.list_approvals(limit=20),
        }

    def _task_list(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.queries.list_tasks(limit=int(params.get("limit", 50)))

    def _task_read(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.queries.read_task(_required_text(params, "taskId"))

    def _approval_list(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.queries.list_approvals(limit=int(params.get("limit", 50)))


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
    parser.add_argument("--db", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    store = SQLiteStore(arguments.db)
    store.initialize()
    server = DesktopRpcServer(
        DesktopRpcApplication(DesktopQueryService(store)),
        input_stream=sys.stdin,
        output_stream=sys.stdout,
    )
    return server.serve()


def _required_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationError(f"{key} must be a non-empty string.")
    return item.strip()


def _bounded_limit(value: int) -> int:
    if isinstance(value, bool) or value < 1 or value > MAX_PAGE_SIZE:
        raise ValidationError(
            f"Desktop page size must be between 1 and {MAX_PAGE_SIZE}."
        )
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


if __name__ == "__main__":
    raise SystemExit(main())
