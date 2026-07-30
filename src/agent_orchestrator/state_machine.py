"""Pure task-state transition rules."""

from __future__ import annotations

from .errors import InvalidTransitionError
from .models import EventType, TaskState


TRANSITIONS: dict[tuple[TaskState, EventType], TaskState] = {
    (TaskState.DRAFT, EventType.TASK_VALIDATED): TaskState.READY,
    (TaskState.READY, EventType.RUN_REQUESTED): TaskState.RUNNING,
    (TaskState.RUNNING, EventType.PHASE_COMPLETED): TaskState.VERIFYING,
    (TaskState.RUNNING, EventType.SIGNAL_REQUIRED): TaskState.WAITING_FOR_SIGNAL,
    (TaskState.RUNNING, EventType.APPROVAL_REQUIRED): TaskState.WAITING_FOR_APPROVAL,
    (TaskState.WAITING_FOR_SIGNAL, EventType.SIGNAL_RECEIVED): TaskState.READY,
    (TaskState.WAITING_FOR_APPROVAL, EventType.APPROVED): TaskState.READY,
    (TaskState.WAITING_FOR_APPROVAL, EventType.APPROVAL_DENIED): TaskState.CANCELLED,
    (TaskState.VERIFYING, EventType.CHECKS_PASSED): TaskState.SUCCEEDED,
    (TaskState.VERIFYING, EventType.CONTINUATION_REQUIRED): TaskState.READY,
    (TaskState.VERIFYING, EventType.CHECKS_FAILED_RETRYABLE): TaskState.READY,
    (TaskState.VERIFYING, EventType.CHECKS_FAILED_FINAL): TaskState.NEEDS_ATTENTION,
    (TaskState.RUNNING, EventType.RUN_FAILED): TaskState.NEEDS_ATTENTION,
    (TaskState.NEEDS_ATTENTION, EventType.ATTENTION_RESOLVED): TaskState.READY,
}

for _state in TaskState:
    if not _state.is_terminal:
        TRANSITIONS[(_state, EventType.CANCEL_REQUESTED)] = TaskState.CANCELLED


def next_state(current: TaskState, event_type: EventType) -> TaskState:
    """Return the legal next state or raise a domain error."""

    try:
        return TRANSITIONS[(current, event_type)]
    except KeyError as exc:
        raise InvalidTransitionError(
            f"Event {event_type.value} is not legal while task is {current.value}."
        ) from exc


def allowed_events(current: TaskState) -> tuple[EventType, ...]:
    """List legal events for a task state in stable name order."""

    return tuple(
        sorted(
            (
                event_type
                for (state, event_type), _target in TRANSITIONS.items()
                if state == current
            ),
            key=lambda item: item.value,
        )
    )
