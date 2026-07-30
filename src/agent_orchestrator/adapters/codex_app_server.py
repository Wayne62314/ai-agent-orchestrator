"""Execution adapter for the stable Codex App Server stdio surface."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import AdapterUnavailableError, ValidationError
from .app_server_limits import StdioJsonRpcClient
from .base import ExecutionAdapter, RunHandle, RunRequest, RunResult, RunStatus


@dataclass(slots=True)
class _AppServerTurn:
    handle: RunHandle
    response_parts: list[str] = field(default_factory=list)


class CodexAppServerExecutionAdapter(ExecutionAdapter):
    """Runs Codex threads and turns through a local App Server process."""

    def __init__(
        self,
        codex_binary: str | Path | None = None,
        *,
        client: Any | None = None,
        turn_timeout_seconds: float = 86_400,
    ):
        if client is None and codex_binary is None:
            raise ValueError("codex_binary or client is required.")
        self.client = client or StdioJsonRpcClient(codex_binary)  # type: ignore[arg-type]
        self._owns_client = client is None
        self.turn_timeout_seconds = turn_timeout_seconds
        self._notifications = self.client.subscribe_notifications()
        self._live: dict[str, _AppServerTurn] = {}
        self._results: dict[str, RunResult] = {}
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._request_thread: threading.Thread | None = None

    def validate_environment(self) -> None:
        binary = getattr(self.client, "codex_binary", None)
        if binary is not None and not Path(binary).is_file():
            raise AdapterUnavailableError(
                f"Codex runtime was not found at {Path(binary)}."
            )

    def start(self, request: RunRequest) -> RunHandle:
        self.validate_environment()
        self._ensure_started()
        if request.thread_id:
            thread_result = self.client.call(
                "thread/resume",
                {
                    "threadId": request.thread_id,
                    "cwd": request.workspace_path,
                },
            )
        else:
            thread_result = self.client.call(
                "thread/start",
                {
                    "approvalPolicy": "never",
                    "cwd": request.workspace_path,
                    "sandbox": self._legacy_sandbox(request.sandbox),
                    "serviceName": "ai_agent_orchestrator",
                },
            )
        thread = thread_result.get("thread")
        if not isinstance(thread, dict) or not thread.get("id"):
            raise AdapterUnavailableError(
                "Codex App Server did not return a thread id."
            )
        thread_id = str(thread["id"])
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": request.prompt}],
            "cwd": request.workspace_path,
            "approvalPolicy": "never",
            "sandboxPolicy": self._sandbox_policy(
                request.sandbox,
                request.workspace_path,
                request.metadata,
            ),
        }
        if request.output_schema is not None:
            turn_params["outputSchema"] = dict(request.output_schema)
        turn_result = self.client.call("turn/start", turn_params)
        turn = turn_result.get("turn")
        if not isinstance(turn, dict) or not turn.get("id"):
            raise AdapterUnavailableError(
                "Codex App Server did not return a turn id."
            )
        handle = RunHandle(
            run_id=request.run_id,
            provider_run_id=str(turn["id"]),
            thread_id=thread_id,
            status=RunStatus.RUNNING,
        )
        with self._lock:
            if request.run_id in self._live or request.run_id in self._results:
                raise ValidationError(f"Run {request.run_id!r} already exists.")
            self._live[request.run_id] = _AppServerTurn(handle=handle)
        return handle

    def inspect(self, handle: RunHandle) -> RunHandle:
        with self._lock:
            result = self._results.get(handle.run_id)
            if result is not None:
                return RunHandle(
                    run_id=result.run_id,
                    provider_run_id=result.provider_run_id,
                    thread_id=result.thread_id,
                    status=result.status,
                )
            return self._require_live(handle.run_id).handle

    def interrupt(self, handle: RunHandle) -> RunResult:
        with self._lock:
            result = self._results.get(handle.run_id)
            if result is not None:
                return result
            live = self._require_live(handle.run_id)
        self.client.call(
            "turn/interrupt",
            {
                "threadId": live.handle.thread_id,
                "turnId": live.handle.provider_run_id,
            },
        )
        return self.collect(handle)

    def collect(self, handle: RunHandle) -> RunResult:
        with self._lock:
            existing = self._results.get(handle.run_id)
            if existing is not None:
                return existing
            live = self._require_live(handle.run_id)
        deadline = time.monotonic() + self.turn_timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result = RunResult(
                    run_id=handle.run_id,
                    provider_run_id=live.handle.provider_run_id,
                    thread_id=live.handle.thread_id,
                    status=RunStatus.FAILED,
                    error_code="TURN_TIMEOUT",
                    error_message="Timed out waiting for Codex turn completion.",
                )
                return self._store_result(result)
            try:
                notification = self._notifications.get(
                    timeout=min(remaining, 30),
                )
            except queue.Empty:
                continue
            method = notification.get("method")
            params = notification.get("params")
            if not isinstance(params, dict):
                continue
            if not self._matches_turn(params, live.handle):
                continue
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    live.response_parts.append(delta)
                continue
            if method != "turn/completed":
                continue
            turn = params.get("turn")
            if not isinstance(turn, dict):
                continue
            status = self._status(str(turn.get("status") or "failed"))
            error = turn.get("error")
            error = error if isinstance(error, dict) else {}
            codex_info = error.get("codexErrorInfo")
            codex_info = codex_info if isinstance(codex_info, dict) else {}
            result = RunResult(
                run_id=handle.run_id,
                provider_run_id=live.handle.provider_run_id,
                thread_id=live.handle.thread_id,
                status=status,
                final_response="".join(live.response_parts) or None,
                error_code=(
                    str(codex_info.get("type") or turn.get("status"))
                    if status == RunStatus.FAILED
                    else None
                ),
                error_message=(
                    str(error.get("message"))
                    if error.get("message") is not None
                    else None
                ),
            )
            return self._store_result(result)

    def close(self) -> None:
        self._closed.set()
        if hasattr(self.client, "unsubscribe_notifications"):
            self.client.unsubscribe_notifications(self._notifications)
        if self._owns_client and hasattr(self.client, "close"):
            self.client.close()
        thread = self._request_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)

    def __enter__(self) -> "CodexAppServerExecutionAdapter":
        self._ensure_started()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

    def _ensure_started(self) -> None:
        if hasattr(self.client, "start"):
            self.client.start()
        if self._request_thread is None:
            self._request_thread = threading.Thread(
                target=self._decline_server_requests,
                name="codex-app-server-request-guard",
                daemon=True,
            )
            self._request_thread.start()

    def _decline_server_requests(self) -> None:
        while not self._closed.is_set():
            try:
                request = self.client.next_server_request(timeout=0.25)
            except TimeoutError:
                continue
            request_id = request.get("id")
            method = request.get("method")
            if not isinstance(request_id, int):
                continue
            if method in {
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            }:
                self.client.respond(
                    request_id,
                    result={"decision": "decline"},
                )
            elif method == "item/permissions/requestApproval":
                self.client.respond(
                    request_id,
                    result={"permissions": []},
                )
            else:
                self.client.respond(
                    request_id,
                    error={
                        "code": -32601,
                        "message": "Unsupported server request.",
                    },
                )

    def _store_result(self, result: RunResult) -> RunResult:
        with self._lock:
            self._results[result.run_id] = result
            self._live.pop(result.run_id, None)
        return result

    def _require_live(self, run_id: str) -> _AppServerTurn:
        try:
            return self._live[run_id]
        except KeyError as exc:
            raise ValidationError(
                f"No live Codex App Server turn exists for {run_id!r}."
            ) from exc

    @staticmethod
    def _matches_turn(
        params: dict[str, Any],
        handle: RunHandle,
    ) -> bool:
        turn = params.get("turn")
        if isinstance(turn, dict) and turn.get("id") is not None:
            return str(turn["id"]) == handle.provider_run_id
        turn_id = params.get("turnId")
        return turn_id is None or str(turn_id) == handle.provider_run_id

    @staticmethod
    def _legacy_sandbox(value: str) -> str:
        if value not in {"read-only", "workspace-write"}:
            raise ValidationError(
                "The product App Server adapter only supports read-only "
                "or workspace-write."
            )
        return value

    @staticmethod
    def _sandbox_policy(
        value: str,
        workspace_path: str,
        metadata: Any,
    ) -> dict[str, Any]:
        if value == "read-only":
            return {
                "type": "readOnly",
                "access": {"type": "fullAccess"},
            }
        if value != "workspace-write":
            raise ValidationError(
                "The product App Server adapter only supports read-only "
                "or workspace-write."
            )
        network_access = False
        if isinstance(metadata, dict):
            network_access = bool(metadata.get("network_access", False))
        return {
            "type": "workspaceWrite",
            "writableRoots": [str(Path(workspace_path).resolve())],
            "readOnlyAccess": {"type": "fullAccess"},
            "networkAccess": network_access,
        }

    @staticmethod
    def _status(value: str) -> RunStatus:
        return {
            "completed": RunStatus.COMPLETED,
            "interrupted": RunStatus.INTERRUPTED,
            "failed": RunStatus.FAILED,
            "inProgress": RunStatus.RUNNING,
        }.get(value, RunStatus.FAILED)
