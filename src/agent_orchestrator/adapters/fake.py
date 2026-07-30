"""Deterministic in-memory adapter for state-machine and integration tests."""

from __future__ import annotations

from dataclasses import replace

from ..errors import NotFoundError, ValidationError
from .base import ExecutionAdapter, RunHandle, RunRequest, RunResult, RunStatus


class FakeExecutionAdapter(ExecutionAdapter):
    """A provider-free adapter with explicit test-controlled completion."""

    def __init__(self) -> None:
        self._handles: dict[str, RunHandle] = {}
        self._results: dict[str, RunResult] = {}

    def start(self, request: RunRequest) -> RunHandle:
        if request.run_id in self._handles:
            raise ValidationError(f"Run {request.run_id!r} already exists.")
        handle = RunHandle(
            run_id=request.run_id,
            provider_run_id=f"fake_{request.run_id}",
            thread_id=request.thread_id or f"fake_thread_{request.task_id}",
            status=RunStatus.RUNNING,
        )
        self._handles[request.run_id] = handle
        return handle

    def inspect(self, handle: RunHandle) -> RunHandle:
        return self._require(handle.run_id)

    def interrupt(self, handle: RunHandle) -> RunResult:
        current = self._require(handle.run_id)
        if current.status.is_terminal:
            return self.collect(current)
        interrupted = replace(current, status=RunStatus.INTERRUPTED)
        result = RunResult(
            run_id=current.run_id,
            provider_run_id=current.provider_run_id,
            thread_id=current.thread_id,
            status=RunStatus.INTERRUPTED,
        )
        self._handles[current.run_id] = interrupted
        self._results[current.run_id] = result
        return result

    def collect(self, handle: RunHandle) -> RunResult:
        current = self._require(handle.run_id)
        if not current.status.is_terminal:
            raise ValidationError(f"Run {handle.run_id!r} is not terminal.")
        return self._results[current.run_id]

    def complete(
        self,
        run_id: str,
        *,
        final_response: str = "",
        usage: dict[str, int] | None = None,
    ) -> RunResult:
        current = self._require(run_id)
        if current.status.is_terminal:
            raise ValidationError(f"Run {run_id!r} is already terminal.")
        completed = replace(current, status=RunStatus.COMPLETED)
        result = RunResult(
            run_id=run_id,
            provider_run_id=current.provider_run_id,
            thread_id=current.thread_id,
            status=RunStatus.COMPLETED,
            final_response=final_response,
            usage=usage or {},
        )
        self._handles[run_id] = completed
        self._results[run_id] = result
        return result

    def fail(
        self,
        run_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> RunResult:
        current = self._require(run_id)
        if current.status.is_terminal:
            raise ValidationError(f"Run {run_id!r} is already terminal.")
        failed = replace(current, status=RunStatus.FAILED)
        result = RunResult(
            run_id=run_id,
            provider_run_id=current.provider_run_id,
            thread_id=current.thread_id,
            status=RunStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )
        self._handles[run_id] = failed
        self._results[run_id] = result
        return result

    def _require(self, run_id: str) -> RunHandle:
        try:
            return self._handles[run_id]
        except KeyError as exc:
            raise NotFoundError(f"Run {run_id!r} was not found.") from exc

