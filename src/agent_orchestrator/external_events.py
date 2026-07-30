"""Authenticated, deduplicated external signals and durable wait matching."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from .errors import SourceAuthenticationError, ValidationError
from .models import (
    Event,
    EventType,
    ExternalEventKind,
    ExternalEventRecord,
    ExternalEventStatus,
    NormalizedExternalEvent,
    SignalWaitRecord,
    SignalWaitStatus,
    TaskState,
)
from .security import SensitiveDataRedactor
from .service import OrchestratorService
from .store import SQLiteStore, utc_after, utc_now

MAX_WEBHOOK_BYTES = 1_048_576

_ALLOWED_CONDITION_FIELDS: dict[ExternalEventKind, frozenset[str]] = {
    ExternalEventKind.CI_COMPLETED: frozenset(
        {"action", "status", "conclusion", "workflow", "branch", "run_id"}
    ),
    ExternalEventKind.PR_REVIEW: frozenset(
        {"action", "state", "actor", "pr_number"}
    ),
    ExternalEventKind.ISSUE_CHANGED: frozenset(
        {"action", "state", "actor", "issue_number", "labels"}
    ),
    ExternalEventKind.SERVICE_HEALTH: frozenset(
        {"service", "status", "check_id"}
    ),
    ExternalEventKind.RATE_LIMIT: frozenset(
        {"provider", "bucket", "available", "remaining", "reset_at"}
    ),
}


class ExternalEventAdapter(Protocol):
    provider: str

    def normalize(
        self,
        payload: Mapping[str, Any],
        *,
        delivery_id: str,
        occurred_at: str = "",
    ) -> NormalizedExternalEvent: ...


class HmacSha256Authenticator:
    """Verify a webhook without persisting or logging its secret."""

    @staticmethod
    def sign(body: bytes, secret: bytes) -> str:
        return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

    @staticmethod
    def verify(body: bytes, signature: str, secret: bytes) -> None:
        if not secret:
            raise ValidationError("Webhook secret cannot be empty.")
        if not signature.startswith("sha256="):
            raise SourceAuthenticationError("Webhook signature is missing or malformed.")
        expected = HmacSha256Authenticator.sign(body, secret)
        if not hmac.compare_digest(expected, signature):
            raise SourceAuthenticationError("Webhook signature did not match.")


class TrustedEventService:
    def __init__(self, *, store: SQLiteStore, service: OrchestratorService):
        self.store = store
        self.service = service

    def register_wait(
        self,
        task_id: str,
        *,
        provider: str,
        kind: ExternalEventKind,
        subject: str,
        condition: Mapping[str, Any],
        timeout_seconds: int,
    ) -> SignalWaitRecord:
        task = self.store.get_task(task_id)
        if task.state != TaskState.RUNNING:
            raise ValidationError("A signal wait can only be registered for a RUNNING task.")
        if timeout_seconds < 1:
            raise ValidationError("Signal wait timeout must be positive.")
        self._validate_condition(kind, condition)
        wait = self.store.create_signal_wait(
            wait_id=f"wait_{uuid.uuid4().hex}",
            task_id=task_id,
            provider=provider.strip(),
            kind=kind,
            subject=subject.strip(),
            condition=condition,
            timeout_behavior="attention",
            deadline_at=utc_after(timeout_seconds),
        )
        transition = self.service.process_event(
            Event(
                event_id=f"evt_{uuid.uuid4().hex}",
                task_id=task_id,
                event_type=EventType.SIGNAL_REQUIRED,
                source="trusted-event-service",
                dedupe_key=f"{wait.wait_id}:signal-required",
                payload={
                    "wait_id": wait.wait_id,
                    "provider": provider,
                    "event_kind": kind.value,
                    "subject": subject,
                    "deadline_at": wait.deadline_at,
                },
                occurred_at=utc_now(),
            )
        )
        if transition.outcome != "APPLIED":
            self.store.finish_signal_wait(
                wait_id=wait.wait_id,
                status=SignalWaitStatus.CANCELLED,
            )
            raise ValidationError(transition.reason or "Signal wait transition was rejected.")
        return wait

    def ingest_webhook(
        self,
        task_id: str,
        *,
        adapter: ExternalEventAdapter,
        body: bytes,
        delivery_id: str,
        signature: str,
        secret: bytes,
        occurred_at: str = "",
    ) -> ExternalEventRecord:
        if len(body) > MAX_WEBHOOK_BYTES:
            raise ValidationError("Webhook body exceeds the one-megabyte limit.")
        try:
            HmacSha256Authenticator.verify(body, signature, secret)
        except SourceAuthenticationError:
            self.store.append_audit(
                task_id=task_id,
                run_id=None,
                kind="EXTERNAL_EVENT_AUTH_REJECTED",
                payload={
                    "provider": adapter.provider,
                    "delivery_id_sha256": hashlib.sha256(
                        delivery_id.encode("utf-8")
                    ).hexdigest(),
                },
            )
            raise
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("Authenticated webhook body is not valid UTF-8 JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ValidationError("Webhook payload must be a JSON object.")
        normalized = adapter.normalize(
            payload,
            delivery_id=delivery_id,
            occurred_at=occurred_at,
        )
        return self.ingest_normalized(task_id, normalized)

    def ingest_trusted_local(
        self,
        task_id: str,
        *,
        adapter: ExternalEventAdapter,
        payload: Mapping[str, Any],
        delivery_id: str,
        occurred_at: str = "",
    ) -> ExternalEventRecord:
        normalized = adapter.normalize(
            payload,
            delivery_id=delivery_id,
            occurred_at=occurred_at,
        )
        return self.ingest_normalized(task_id, normalized)

    def ingest_normalized(
        self,
        task_id: str,
        event: NormalizedExternalEvent,
    ) -> ExternalEventRecord:
        record, duplicate = self.store.record_external_event(
            external_event_id=f"xevt_{uuid.uuid4().hex}",
            task_id=task_id,
            provider=event.provider,
            kind=event.kind.value,
            delivery_id=event.delivery_id,
            subject=event.subject,
            facts=event.facts,
            authenticated=True,
            content_trust=event.content_trust,
        )
        if duplicate and record.status != ExternalEventStatus.RECEIVED:
            return record
        if duplicate and self.store.find_signal_wait_satisfied_by(
            record.external_event_id
        ):
            return self.store.finish_external_event(
                record.external_event_id,
                status=ExternalEventStatus.CONSUMED,
                reason="active_wait_satisfied",
            )

        waits = self.store.find_matching_signal_waits(
            task_id=task_id,
            provider=event.provider,
            kind=event.kind,
            subject=event.subject,
        )
        matching = next(
            (wait for wait in waits if self._matches(wait.condition, event.facts)),
            None,
        )
        if matching is None:
            return self.store.finish_external_event(
                record.external_event_id,
                status=ExternalEventStatus.IGNORED,
                reason="no_active_wait_satisfied",
            )

        result = self.service.process_event(
            Event(
                event_id=f"evt_external_{record.external_event_id}",
                task_id=task_id,
                event_type=EventType.SIGNAL_RECEIVED,
                source=f"external:{event.provider}",
                dedupe_key=f"{record.dedupe_key}:signal-received",
                payload={
                    "external_event_id": record.external_event_id,
                    "wait_id": matching.wait_id,
                    "event_kind": event.kind.value,
                    "subject": event.subject,
                    "facts": dict(event.facts),
                    "content_trust": event.content_trust,
                },
                occurred_at=event.occurred_at,
            )
        )
        if result.outcome != "APPLIED" and not result.duplicate:
            return self.store.finish_external_event(
                record.external_event_id,
                status=ExternalEventStatus.REJECTED,
                reason=result.reason or "signal_transition_rejected",
            )
        self.store.finish_signal_wait(
            wait_id=matching.wait_id,
            status=SignalWaitStatus.SATISFIED,
            satisfied_by=record.external_event_id,
        )
        return self.store.finish_external_event(
            record.external_event_id,
            status=ExternalEventStatus.CONSUMED,
            reason="active_wait_satisfied",
        )

    def expire_waits(self, *, observed_at: str | None = None) -> list[SignalWaitRecord]:
        expired: list[SignalWaitRecord] = []
        for wait in self.store.list_expired_signal_waits(observed_at=observed_at):
            result = self.service.process_event(
                Event(
                    event_id=f"evt_timeout_{wait.wait_id}",
                    task_id=wait.task_id,
                    event_type=EventType.SIGNAL_TIMEOUT,
                    source="trusted-event-service",
                    dedupe_key=f"{wait.wait_id}:timeout",
                    payload={
                        "wait_id": wait.wait_id,
                        "deadline_at": wait.deadline_at,
                        "timeout_behavior": wait.timeout_behavior,
                    },
                    occurred_at=observed_at or utc_now(),
                )
            )
            if result.outcome == "APPLIED" or result.duplicate:
                expired.append(
                    self.store.finish_signal_wait(
                        wait_id=wait.wait_id,
                        status=SignalWaitStatus.EXPIRED,
                    )
                )
        return expired

    @staticmethod
    def _validate_condition(
        kind: ExternalEventKind,
        condition: Mapping[str, Any],
    ) -> None:
        if not condition:
            raise ValidationError("Signal wait condition cannot be empty.")
        unknown = set(condition) - _ALLOWED_CONDITION_FIELDS[kind]
        if unknown:
            raise ValidationError(
                f"Condition fields are not trusted metadata: {sorted(unknown)!r}."
            )
        SensitiveDataRedactor().require_safe(
            dict(condition),
            context="Signal wait condition",
        )
        for key, expected in condition.items():
            if isinstance(expected, (str, int, float, bool)) or expected is None:
                continue
            if isinstance(expected, list) and all(
                isinstance(item, (str, int, float, bool)) or item is None
                for item in expected
            ):
                continue
            raise ValidationError(
                f"Condition {key!r} must use a scalar or a list of scalars."
            )

    @staticmethod
    def _matches(
        condition: Mapping[str, Any],
        facts: Mapping[str, Any],
    ) -> bool:
        for key, expected in condition.items():
            actual = facts.get(key)
            if isinstance(expected, list):
                if isinstance(actual, list):
                    if not all(item in actual for item in expected):
                        return False
                elif actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True
