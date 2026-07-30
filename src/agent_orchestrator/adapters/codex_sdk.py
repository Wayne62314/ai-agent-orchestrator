"""Synchronous control adapter for the official ``openai-codex`` SDK."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import AdapterUnavailableError, ValidationError
from .base import ExecutionAdapter, RunHandle, RunRequest, RunResult, RunStatus


@dataclass(slots=True)
class _LiveTurn:
    sdk_turn: Any
    handle: RunHandle


class CodexSdkExecutionAdapter(ExecutionAdapter):
    """Start, resume, collect, and interrupt local Codex turns."""

    def __init__(
        self,
        *,
        required_version: str | None = "0.144.4",
        codex_binary: str | Path | None = None,
        client: Any | None = None,
    ):
        self.required_version = required_version
        self.codex_binary = (
            str(Path(codex_binary).expanduser().resolve())
            if codex_binary is not None
            else None
        )
        self._client = client
        self._owns_client = client is None
        self._live: dict[str, _LiveTurn] = {}
        self._results: dict[str, RunResult] = {}
        self._lock = threading.RLock()

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("openai_codex") is not None

    def validate_environment(self) -> None:
        if self._client is not None:
            return
        if not self.is_available():
            raise AdapterUnavailableError(
                "The optional openai-codex package is not installed."
            )
        if self.required_version is not None:
            installed = importlib.metadata.version("openai-codex")
            if installed != self.required_version:
                raise AdapterUnavailableError(
                    f"Expected openai-codex {self.required_version}, found {installed}."
                )
        if self.codex_binary is not None and not Path(self.codex_binary).is_file():
            raise AdapterUnavailableError(
                f"Codex runtime was not found at {self.codex_binary}."
            )

    def start(self, request: RunRequest) -> RunHandle:
        client = self._ensure_client()
        sandbox = self._sandbox(request.sandbox)
        try:
            if request.thread_id:
                thread = client.thread_resume(
                    request.thread_id,
                    cwd=request.workspace_path,
                    sandbox=sandbox,
                )
            else:
                thread = client.thread_start(
                    cwd=request.workspace_path,
                    sandbox=sandbox,
                )
            sdk_turn = thread.turn(
                request.prompt,
                sandbox=sandbox,
                output_schema=(
                    dict(request.output_schema)
                    if request.output_schema is not None
                    else None
                ),
            )
        except Exception as exc:
            raise AdapterUnavailableError(
                f"Codex failed to start a turn: {type(exc).__name__}: {exc}"
            ) from exc
        handle = RunHandle(
            run_id=request.run_id,
            provider_run_id=str(sdk_turn.id),
            thread_id=str(thread.id),
            status=RunStatus.RUNNING,
        )
        with self._lock:
            if request.run_id in self._live or request.run_id in self._results:
                raise ValidationError(f"Run {request.run_id!r} already exists.")
            self._live[request.run_id] = _LiveTurn(
                sdk_turn=sdk_turn,
                handle=handle,
            )
        return handle

    def inspect(self, handle: RunHandle) -> RunHandle:
        with self._lock:
            if handle.run_id in self._results:
                result = self._results[handle.run_id]
                return RunHandle(
                    run_id=result.run_id,
                    provider_run_id=result.provider_run_id,
                    thread_id=result.thread_id,
                    status=result.status,
                )
            return self._require_live(handle.run_id).handle

    def interrupt(self, handle: RunHandle) -> RunResult:
        with self._lock:
            if handle.run_id in self._results:
                return self._results[handle.run_id]
        self.request_interrupt(handle)
        return self.collect(handle)

    def request_interrupt(self, handle: RunHandle) -> None:
        with self._lock:
            if handle.run_id in self._results:
                return
            live = self._require_live(handle.run_id)
        try:
            live.sdk_turn.interrupt()
        except Exception as exc:
            if "no active turn to interrupt" in str(exc).lower():
                return
            raise AdapterUnavailableError(
                f"Codex failed to interrupt the turn: {type(exc).__name__}: {exc}"
            ) from exc

    def collect(self, handle: RunHandle) -> RunResult:
        with self._lock:
            if handle.run_id in self._results:
                return self._results[handle.run_id]
            live = self._require_live(handle.run_id)
        try:
            sdk_result = live.sdk_turn.run()
            status = self._status(str(sdk_result.status.value))
            error = getattr(sdk_result, "error", None)
            usage = getattr(sdk_result, "usage", None)
            result = RunResult(
                run_id=handle.run_id,
                provider_run_id=live.handle.provider_run_id,
                thread_id=live.handle.thread_id,
                status=status,
                final_response=getattr(sdk_result, "final_response", None),
                error_code=(
                    str(getattr(error, "error_code", None))
                    if error is not None
                    else None
                ),
                error_message=(
                    str(getattr(error, "message", None))
                    if error is not None
                    else None
                ),
                usage=(
                    usage.model_dump(mode="json", by_alias=True)
                    if usage is not None and hasattr(usage, "model_dump")
                    else {}
                ),
            )
        except Exception as exc:
            return self._record_adapter_failure(handle, exc)
        with self._lock:
            self._results[handle.run_id] = result
            self._live.pop(handle.run_id, None)
        return result

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            self._live.clear()
        if self._owns_client and client is not None:
            client.close()

    def session_client(self) -> Any:
        """Return the shared initialized SDK client for account operations."""
        return self._ensure_client()

    def __enter__(self) -> "CodexSdkExecutionAdapter":
        self._ensure_client()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

    def _ensure_client(self) -> Any:
        with self._lock:
            if self._client is not None:
                return self._client
            self.validate_environment()
            module = importlib.import_module("openai_codex")
            config = module.CodexConfig(codex_bin=self.codex_binary)
            self._client = module.Codex(config)
            return self._client

    def _sandbox(self, value: str) -> Any:
        normalized = value.strip().lower()
        if not self.is_available() and self._client is not None:
            if normalized not in {
                "read-only",
                "workspace-write",
                "full-access",
                "danger-full-access",
            }:
                raise ValidationError(f"Unsupported Codex sandbox: {value!r}.")
            return normalized
        module = importlib.import_module("openai_codex")
        mapping = {
            "read-only": module.Sandbox.read_only,
            "workspace-write": module.Sandbox.workspace_write,
            "full-access": module.Sandbox.full_access,
            "danger-full-access": module.Sandbox.full_access,
        }
        try:
            return mapping[normalized]
        except KeyError as exc:
            raise ValidationError(f"Unsupported Codex sandbox: {value!r}.") from exc

    def _require_live(self, run_id: str) -> _LiveTurn:
        try:
            return self._live[run_id]
        except KeyError as exc:
            raise ValidationError(f"No live Codex turn exists for {run_id!r}.") from exc

    def _record_adapter_failure(
        self,
        handle: RunHandle,
        exc: Exception,
    ) -> RunResult:
        result = RunResult(
            run_id=handle.run_id,
            provider_run_id=handle.provider_run_id,
            thread_id=handle.thread_id,
            status=RunStatus.FAILED,
            error_code="ADAPTER_ERROR",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        with self._lock:
            self._results[handle.run_id] = result
            self._live.pop(handle.run_id, None)
        return result

    @staticmethod
    def _status(value: str) -> RunStatus:
        return {
            "completed": RunStatus.COMPLETED,
            "interrupted": RunStatus.INTERRUPTED,
            "failed": RunStatus.FAILED,
            "inProgress": RunStatus.RUNNING,
        }.get(value, RunStatus.FAILED)
