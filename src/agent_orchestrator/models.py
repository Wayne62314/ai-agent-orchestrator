"""Domain models shared by storage, services, and adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskState(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_SIGNAL = "WAITING_FOR_SIGNAL"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    VERIFYING = "VERIFYING"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in {TaskState.SUCCEEDED, TaskState.CANCELLED}


class EventType(StrEnum):
    TASK_VALIDATED = "TASK_VALIDATED"
    RUN_REQUESTED = "RUN_REQUESTED"
    PHASE_COMPLETED = "PHASE_COMPLETED"
    SIGNAL_REQUIRED = "SIGNAL_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    SIGNAL_TIMEOUT = "SIGNAL_TIMEOUT"
    APPROVED = "APPROVED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    CHECKS_PASSED = "CHECKS_PASSED"
    CONTINUATION_REQUIRED = "CONTINUATION_REQUIRED"
    CHECKS_FAILED_RETRYABLE = "CHECKS_FAILED_RETRYABLE"
    CHECKS_FAILED_FINAL = "CHECKS_FAILED_FINAL"
    RUN_FAILED = "RUN_FAILED"
    ATTENTION_RESOLVED = "ATTENTION_RESOLVED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"


class RunState(StrEnum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunState.COMPLETED,
            RunState.INTERRUPTED,
            RunState.FAILED,
            RunState.ABANDONED,
        }


class SideEffectStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class ExternalEventKind(StrEnum):
    CI_COMPLETED = "ci.completed"
    PR_REVIEW = "pr.review"
    ISSUE_CHANGED = "issue.changed"
    SERVICE_HEALTH = "service.health"
    RATE_LIMIT = "rate_limit.updated"


class ExternalEventStatus(StrEnum):
    RECEIVED = "RECEIVED"
    CONSUMED = "CONSUMED"
    IGNORED = "IGNORED"
    REJECTED = "REJECTED"


class SignalWaitStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SATISFIED = "SATISFIED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    title: str
    objective: str
    workspace_path: str
    state: TaskState
    permissions_policy: Mapping[str, Any] = field(default_factory=dict)
    acceptance_policy: Mapping[str, Any] = field(default_factory=dict)
    retry_policy: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    version: int = 0


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    task_id: str
    event_type: EventType
    source: str
    dedupe_key: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: str = ""
    expected_version: int | None = None


@dataclass(frozen=True, slots=True)
class EventResult:
    event_id: str
    task_id: str
    outcome: str
    previous_state: TaskState
    current_state: TaskState
    task_version: int
    duplicate: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    sequence: int
    audit_id: str
    task_id: str | None
    run_id: str | None
    kind: str
    payload: Mapping[str, Any]
    created_at: str
    previous_hash: str
    entry_hash: str


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    task_id: str
    attempt: int
    engine: str
    state: RunState
    input_checkpoint_id: str | None
    started_at: str
    provider_run_id: str | None = None
    thread_id: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    ended_at: str | None = None
    exit_reason: str | None = None
    result_summary: str | None = None


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    checkpoint_id: str
    task_id: str
    run_id: str | None
    sequence: int
    schema_version: int
    workspace_revision: str | None
    payload_path: str
    payload_hash: str
    created_at: str
    status: str = "READY"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    verification_id: str
    task_id: str
    run_id: str | None
    attempt: int
    check_name: str
    required: bool
    status: str
    command: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    output_truncated: bool
    duration_ms: int
    summary: str
    log_path: str
    created_at: str
    started_at: str
    ended_at: str


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    task_id: str
    action_type: str
    action_hash: str
    parameters: Mapping[str, Any]
    risk_summary: str
    rollback_plan: str
    status: str
    request_key: str
    requested_at: str
    expires_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    consumed_at: str | None = None

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        from datetime import UTC, datetime

        return datetime.fromisoformat(self.expires_at) <= datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SideEffectRecord:
    effect_id: str
    task_id: str
    approval_id: str | None
    idempotency_key: str
    logical_step: str
    action_type: str
    parameters_hash: str
    status: SideEffectStatus
    external_result_id: str | None
    error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class NormalizedExternalEvent:
    provider: str
    kind: ExternalEventKind
    delivery_id: str
    subject: str
    facts: Mapping[str, Any]
    occurred_at: str
    content_trust: str = "TRUSTED_METADATA"


@dataclass(frozen=True, slots=True)
class ExternalEventRecord:
    external_event_id: str
    task_id: str
    provider: str
    kind: str
    delivery_id: str
    dedupe_key: str
    subject: str
    facts: Mapping[str, Any]
    authenticated: bool
    content_trust: str
    status: ExternalEventStatus
    outcome_reason: str | None
    received_at: str
    processed_at: str | None


@dataclass(frozen=True, slots=True)
class SignalWaitRecord:
    wait_id: str
    task_id: str
    provider: str
    kind: ExternalEventKind
    subject: str
    condition: Mapping[str, Any]
    timeout_behavior: str
    status: SignalWaitStatus
    created_at: str
    deadline_at: str
    satisfied_by: str | None
