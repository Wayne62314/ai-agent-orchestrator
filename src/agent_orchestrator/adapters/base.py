"""Provider-neutral adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable


class RunStatus(StrEnum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunStatus.COMPLETED,
            RunStatus.INTERRUPTED,
            RunStatus.FAILED,
        }


@dataclass(frozen=True, slots=True)
class RunRequest:
    task_id: str
    run_id: str
    workspace_path: str
    prompt: str
    sandbox: str = "read-only"
    thread_id: str | None = None
    output_schema: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunHandle:
    run_id: str
    provider_run_id: str
    thread_id: str | None
    status: RunStatus


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    provider_run_id: str
    thread_id: str | None
    status: RunStatus
    final_response: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RateLimitSnapshot:
    provider: str
    limit_id: str
    used_percent: float | None
    resets_at: str | None
    reached_type: str | None = None
    observed_at: str = ""
    raw_reference: str | None = None


@runtime_checkable
class ExecutionAdapter(Protocol):
    def start(self, request: RunRequest) -> RunHandle:
        """Start or resume one provider run."""

    def inspect(self, handle: RunHandle) -> RunHandle:
        """Return the most recently observed status."""

    def interrupt(self, handle: RunHandle) -> RunResult:
        """Request interruption of an active run."""

    def collect(self, handle: RunHandle) -> RunResult:
        """Collect the terminal result for a run."""


@runtime_checkable
class RateLimitProvider(Protocol):
    def read(self) -> tuple[RateLimitSnapshot, ...]:
        """Read the current limit buckets."""

