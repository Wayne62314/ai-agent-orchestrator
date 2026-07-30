"""Application service for atomic event processing."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import InvalidTransitionError, ValidationError
from .models import Event, EventResult, EventType, Task
from .state_machine import next_state
from .store import SQLiteStore, utc_now


class OrchestratorService:
    """Coordinates domain rules and durable storage in one transaction."""

    def __init__(self, store: SQLiteStore):
        self.store = store

    def initialize(self) -> None:
        self.store.initialize()

    def create_task(
        self,
        *,
        title: str,
        objective: str,
        workspace_path: str | Path,
        permissions_policy: Mapping[str, Any] | None = None,
        acceptance_policy: Mapping[str, Any] | None = None,
        retry_policy: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> Task:
        return self.store.create_task(
            task_id=task_id or f"task_{uuid.uuid4().hex}",
            title=title,
            objective=objective,
            workspace_path=str(workspace_path),
            permissions_policy=permissions_policy or {},
            acceptance_policy=acceptance_policy or {},
            retry_policy=retry_policy or {"max_attempts": 3},
        )

    def validate_task(
        self,
        task_id: str,
        *,
        dedupe_key: str | None = None,
    ) -> EventResult:
        return self.process_event(
            Event(
                event_id=f"evt_{uuid.uuid4().hex}",
                task_id=task_id,
                event_type=EventType.TASK_VALIDATED,
                source="orchestrator",
                dedupe_key=dedupe_key or f"{task_id}:initial-validation",
                payload={},
                occurred_at=utc_now(),
            )
        )

    def process_event(self, event: Event) -> EventResult:
        if not event.source.strip():
            raise ValidationError("Event source cannot be empty.")
        if not event.dedupe_key.strip():
            raise ValidationError("Event dedupe key cannot be empty.")

        with self.store.transaction() as connection:
            task_row = self.store._get_task_row(connection, event.task_id)
            current = self.store._task_from_row(task_row)
            inserted, existing = self.store.insert_event(connection, event)

            if not inserted:
                return EventResult(
                    event_id=existing["event_id"],
                    task_id=existing["task_id"],
                    outcome=existing["outcome"] or "PENDING",
                    previous_state=current.state,
                    current_state=current.state,
                    task_version=current.version,
                    duplicate=True,
                    reason=existing["outcome_reason"],
                )

            if (
                event.expected_version is not None
                and event.expected_version != current.version
            ):
                reason = (
                    f"Expected task version {event.expected_version}, "
                    f"found {current.version}."
                )
                self.store.mark_event(
                    connection,
                    event_id=event.event_id,
                    outcome="REJECTED",
                    reason=reason,
                )
                self.store._append_audit(
                    connection,
                    task_id=event.task_id,
                    run_id=None,
                    kind="EVENT_REJECTED",
                    payload={
                        "event_id": event.event_id,
                        "event_type": event.event_type.value,
                        "reason": reason,
                    },
                )
                return EventResult(
                    event_id=event.event_id,
                    task_id=event.task_id,
                    outcome="REJECTED",
                    previous_state=current.state,
                    current_state=current.state,
                    task_version=current.version,
                    reason=reason,
                )

            try:
                target = next_state(current.state, event.event_type)
            except InvalidTransitionError as exc:
                reason = str(exc)
                self.store.mark_event(
                    connection,
                    event_id=event.event_id,
                    outcome="REJECTED",
                    reason=reason,
                )
                self.store._append_audit(
                    connection,
                    task_id=event.task_id,
                    run_id=None,
                    kind="EVENT_REJECTED",
                    payload={
                        "event_id": event.event_id,
                        "event_type": event.event_type.value,
                        "reason": reason,
                    },
                )
                return EventResult(
                    event_id=event.event_id,
                    task_id=event.task_id,
                    outcome="REJECTED",
                    previous_state=current.state,
                    current_state=current.state,
                    task_version=current.version,
                    reason=reason,
                )

            updated = self.store.update_task_state(
                connection,
                task_id=event.task_id,
                from_state=current.state,
                to_state=target,
                expected_version=current.version,
            )
            self.store.mark_event(
                connection,
                event_id=event.event_id,
                outcome="APPLIED",
                reason=None,
            )
            self.store._append_audit(
                connection,
                task_id=event.task_id,
                run_id=None,
                kind="STATE_TRANSITIONED",
                payload={
                    "event_id": event.event_id,
                    "event_type": event.event_type.value,
                    "from_state": current.state.value,
                    "source": event.source,
                    "to_state": target.value,
                    "version": updated.version,
                },
            )
            return EventResult(
                event_id=event.event_id,
                task_id=event.task_id,
                outcome="APPLIED",
                previous_state=current.state,
                current_state=target,
                task_version=updated.version,
            )
