"""Least-privilege action authorization and hash-bound approvals."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from .errors import (
    AuthorizationDeniedError,
    SideEffectUncertainError,
    ValidationError,
)
from .models import (
    ApprovalRecord,
    Event,
    EventType,
    SideEffectRecord,
    SideEffectStatus,
    TaskState,
)
from .security import SensitiveDataRedactor
from .service import OrchestratorService
from .store import SQLiteStore, canonical_json, utc_now


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


HIGH_RISK_PREFIXES = (
    "deployment.",
    "git.push",
    "git.merge",
    "filesystem.delete",
    "database.delete",
    "network.authenticated_write",
    "messaging.",
    "billing.",
    "permissions.",
)
_ACTION_TYPE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")


@dataclass(frozen=True, slots=True)
class ActionRequest:
    action_type: str
    logical_step: str
    parameters: Mapping[str, Any]
    risk_summary: str
    rollback_plan: str

    def __post_init__(self) -> None:
        if not isinstance(self.action_type, str) or not _ACTION_TYPE.fullmatch(
            self.action_type
        ):
            raise ValidationError(
                "Action type must be a dotted lowercase identifier."
            )
        if not isinstance(self.logical_step, str) or not self.logical_step.strip():
            raise ValidationError("Action logical_step cannot be empty.")
        if not isinstance(self.risk_summary, str) or not self.risk_summary.strip():
            raise ValidationError("Action risk_summary cannot be empty.")
        if not isinstance(self.rollback_plan, str) or not self.rollback_plan.strip():
            raise ValidationError("Action rollback_plan cannot be empty.")
        if not isinstance(self.parameters, Mapping):
            raise ValidationError("Action parameters must be an object.")
        SensitiveDataRedactor().require_safe(
            dict(self.parameters),
            context="Action parameters",
        )
        try:
            canonical_json(dict(self.parameters))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "Action parameters must be JSON-serializable."
            ) from exc

    @property
    def action_hash(self) -> str:
        normalized = canonical_json(
            {
                "action_type": self.action_type,
                "logical_step": self.logical_step,
                "parameters": dict(self.parameters),
            }
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def idempotency_key(self, task_id: str) -> str:
        value = canonical_json(
            {
                "action_hash": self.action_hash,
                "action_type": self.action_type,
                "logical_step": self.logical_step,
                "task_id": task_id,
            }
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    decision: PermissionDecision
    authorized: bool
    action_hash: str
    approval: ApprovalRecord | None = None
    reason: str = ""


class PermissionPolicy:
    def evaluate(
        self,
        policy: Mapping[str, Any],
        action_type: str,
    ) -> PermissionDecision:
        current: Any = policy
        for segment in action_type.split("."):
            if isinstance(current, str):
                return self._decision(current)
            if not isinstance(current, Mapping):
                return self._default(action_type)
            if segment in current:
                current = current[segment]
            elif "*" in current:
                current = current["*"]
            else:
                return self._default(action_type)
        if isinstance(current, str):
            return self._decision(current)
        if isinstance(current, Mapping) and "default" in current:
            return self._decision(current["default"])
        return self._default(action_type)

    @staticmethod
    def _decision(value: Any) -> PermissionDecision:
        try:
            return PermissionDecision(str(value).casefold())
        except ValueError:
            return PermissionDecision.DENY

    @staticmethod
    def _default(action_type: str) -> PermissionDecision:
        if any(
            action_type == prefix.rstrip(".") or action_type.startswith(prefix)
            for prefix in HIGH_RISK_PREFIXES
        ):
            return PermissionDecision.ASK
        return PermissionDecision.DENY


class ApprovalService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        service: OrchestratorService,
        policy: PermissionPolicy | None = None,
    ):
        self.store = store
        self.service = service
        self.policy = policy or PermissionPolicy()

    def request(
        self,
        task_id: str,
        action: ActionRequest,
        *,
        ttl_seconds: int = 900,
    ) -> AuthorizationResult:
        task = self.store.get_task(task_id)
        decision = self.policy.evaluate(
            task.permissions_policy,
            action.action_type,
        )
        if decision == PermissionDecision.ALLOW:
            return AuthorizationResult(
                decision=decision,
                authorized=True,
                action_hash=action.action_hash,
                reason="Explicit policy allows this action.",
            )
        if decision == PermissionDecision.DENY:
            return AuthorizationResult(
                decision=decision,
                authorized=False,
                action_hash=action.action_hash,
                reason="Policy denies this action.",
            )
        request_key = f"{task_id}:{action.action_hash}"
        existing = self.store.find_approval_by_request_key(request_key)
        if existing is not None:
            if existing.status == "REQUESTED":
                return AuthorizationResult(
                    decision=decision,
                    authorized=False,
                    action_hash=action.action_hash,
                    approval=existing,
                    reason="An identical approval request is already pending.",
                )
            if existing.status == "APPROVED" and not existing.is_expired:
                return AuthorizationResult(
                    decision=decision,
                    authorized=True,
                    action_hash=action.action_hash,
                    approval=existing,
                    reason="An identical action already has a valid approval.",
                )
        if task.state != TaskState.RUNNING:
            raise ValidationError(
                "An approval request can only pause a RUNNING task; "
                f"found {task.state.value}."
            )
        if ttl_seconds < 60 or ttl_seconds > 86_400:
            raise ValidationError("Approval TTL must be between 60 and 86400 seconds.")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        ).isoformat(timespec="microseconds")
        approval = self.store.create_approval(
            approval_id=f"approval_{uuid.uuid4().hex}",
            task_id=task_id,
            action_type=action.action_type,
            action_hash=action.action_hash,
            parameters=dict(action.parameters),
            risk_summary=action.risk_summary,
            rollback_plan=action.rollback_plan,
            request_key=request_key,
            expires_at=expires_at,
        )
        transition = self.service.process_event(
            Event(
                event_id=f"evt_{uuid.uuid4().hex}",
                task_id=task_id,
                event_type=EventType.APPROVAL_REQUIRED,
                source="approval-service",
                dedupe_key=f"{approval.approval_id}:requested",
                payload={
                    "approval_id": approval.approval_id,
                    "action_type": action.action_type,
                    "action_hash": action.action_hash,
                },
                occurred_at=utc_now(),
                expected_version=task.version,
            )
        )
        if transition.outcome != "APPLIED":
            raise ValidationError(transition.reason or "Approval transition failed.")
        return AuthorizationResult(
            decision=decision,
            authorized=False,
            action_hash=action.action_hash,
            approval=approval,
            reason="Explicit user approval is required.",
        )

    def decide(
        self,
        approval_id: str,
        *,
        approved: bool,
        expected_action_hash: str,
        decided_by: str,
    ) -> ApprovalRecord:
        if not decided_by.strip():
            raise ValidationError("Approval decision actor cannot be empty.")
        approval = self.store.get_approval(approval_id)
        if approval.action_hash != expected_action_hash:
            raise ValidationError(
                "Approval action hash does not match the displayed action."
            )
        decided = self.store.decide_approval(
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
        )
        task = self.store.get_task(decided.task_id)
        event_type = EventType.APPROVED if approved else EventType.APPROVAL_DENIED
        transition = self.service.process_event(
            Event(
                event_id=f"evt_{uuid.uuid4().hex}",
                task_id=task.task_id,
                event_type=event_type,
                source="approval-service",
                dedupe_key=f"{approval_id}:{'approved' if approved else 'denied'}",
                payload={
                    "approval_id": approval_id,
                    "action_hash": approval.action_hash,
                },
                occurred_at=utc_now(),
                expected_version=task.version,
            )
        )
        if transition.outcome != "APPLIED":
            raise ValidationError(transition.reason or "Approval decision failed.")
        return decided

    def require_authorized(
        self,
        task_id: str,
        action: ActionRequest,
        *,
        approval_id: str | None,
    ) -> ApprovalRecord | None:
        task = self.store.get_task(task_id)
        decision = self.policy.evaluate(
            task.permissions_policy,
            action.action_type,
        )
        if decision == PermissionDecision.DENY:
            raise AuthorizationDeniedError(
                f"Policy denies {action.action_type}."
            )
        if decision == PermissionDecision.ALLOW:
            return None
        if approval_id is None:
            raise AuthorizationDeniedError(
                f"Action {action.action_type} requires approval."
            )
        approval = self.store.get_approval(approval_id)
        if (
            approval.task_id != task_id
            or approval.action_hash != action.action_hash
            or approval.status != "APPROVED"
            or approval.is_expired
        ):
            raise AuthorizationDeniedError(
                "Approval is missing, expired, consumed, or bound to another action."
            )
        return approval


@dataclass(frozen=True, slots=True)
class SideEffectExecution:
    record: SideEffectRecord
    duplicate: bool = False


SideEffectPerformer = Callable[[ActionRequest], str | None]


class SideEffectCoordinator:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        approvals: ApprovalService,
    ):
        self.store = store
        self.approvals = approvals

    def execute(
        self,
        task_id: str,
        action: ActionRequest,
        performer: SideEffectPerformer,
        *,
        approval_id: str | None = None,
    ) -> SideEffectExecution:
        idempotency_key = action.idempotency_key(task_id)
        existing = self.store.find_side_effect_by_idempotency(idempotency_key)
        if existing is not None:
            if existing.status == SideEffectStatus.SUCCEEDED:
                return SideEffectExecution(record=existing, duplicate=True)
            raise SideEffectUncertainError(
                f"Side effect {existing.effect_id} is {existing.status.value}; "
                "reconcile it before any retry."
            )
        approval = self.approvals.require_authorized(
            task_id,
            action,
            approval_id=approval_id,
        )
        record, created = self.store.reserve_side_effect(
            effect_id=f"effect_{uuid.uuid4().hex}",
            task_id=task_id,
            approval_id=approval.approval_id if approval else None,
            idempotency_key=idempotency_key,
            logical_step=action.logical_step,
            action_type=action.action_type,
            parameters_hash=action.action_hash,
        )
        if not created:
            if record.status == SideEffectStatus.SUCCEEDED:
                return SideEffectExecution(record=record, duplicate=True)
            raise SideEffectUncertainError(
                f"Side effect {record.effect_id} is {record.status.value}; "
                "reconcile it before any retry."
            )
        try:
            external_result_id = performer(action)
        except BaseException as exc:
            self.store.finish_side_effect(
                effect_id=record.effect_id,
                status=SideEffectStatus.UNKNOWN,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise SideEffectUncertainError(
                "The external action result is unknown and will not be retried."
            ) from exc
        record = self.store.finish_side_effect(
            effect_id=record.effect_id,
            status=SideEffectStatus.SUCCEEDED,
            external_result_id=external_result_id,
        )
        if approval is not None:
            self.store.consume_approval(approval.approval_id)
        return SideEffectExecution(record=record)

    def recover_stale(self, *, older_than_seconds: int = 300) -> list[SideEffectRecord]:
        return self.store.mark_stale_side_effects_unknown(
            older_than_seconds=older_than_seconds
        )

    def reconcile(
        self,
        effect_id: str,
        *,
        succeeded: bool,
        external_result_id: str | None = None,
    ) -> SideEffectRecord:
        record = self.store.finish_side_effect(
            effect_id=effect_id,
            status=(
                SideEffectStatus.SUCCEEDED
                if succeeded
                else SideEffectStatus.FAILED
            ),
            external_result_id=external_result_id,
        )
        if succeeded and record.approval_id:
            self.store.consume_approval(record.approval_id)
        return record
