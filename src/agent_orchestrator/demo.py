"""Repeatable preparation and verification for a real GitHub CI webhook demo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError
from .external_events import TrustedEventService
from .models import (
    Event,
    EventType,
    ExternalEventKind,
    ExternalEventStatus,
    SignalWaitStatus,
    TaskState,
)
from .service import OrchestratorService
from .store import SQLiteStore, utc_now


@dataclass(frozen=True, slots=True)
class CiDemoPreparation:
    task_id: str
    wait_id: str
    state: TaskState
    repository: str
    workflow: str
    branch: str
    subject: str


@dataclass(frozen=True, slots=True)
class CiDemoEvidence:
    task_id: str
    passed: bool
    task_state: TaskState
    wait_status: SignalWaitStatus | None
    event_status: ExternalEventStatus | None
    event_authenticated: bool
    event_content_trust: str | None
    audit_chain_valid: bool


class CiWebhookDemo:
    """Create a durable CI wait and evaluate its persisted delivery evidence."""

    def __init__(self, *, store: SQLiteStore, service: OrchestratorService):
        self.store = store
        self.service = service

    def prepare(
        self,
        *,
        repository: str,
        workflow: str,
        branch: str,
        workspace: Path,
        timeout_seconds: int,
    ) -> CiDemoPreparation:
        repository = repository.strip()
        workflow = workflow.strip()
        branch = branch.strip()
        if repository.count("/") != 1 or any(
            not part.strip() for part in repository.split("/")
        ):
            raise ValidationError("Repository must use the OWNER/REPO form.")
        if not workflow:
            raise ValidationError("Workflow must be non-empty.")
        if not branch:
            raise ValidationError("Branch must be non-empty.")
        if timeout_seconds < 1:
            raise ValidationError("Demo timeout must be positive.")

        task = self.service.create_task(
            title=f"GitHub CI webhook demo for {repository}",
            objective="Resume from an authenticated real GitHub workflow_run delivery.",
            workspace_path=workspace,
            permissions_policy={},
            acceptance_policy={},
            retry_policy={"max_attempts": 1},
        )
        self.service.validate_task(task.task_id)
        transition = self.service.process_event(
            Event(
                event_id=f"evt_demo_run_{task.task_id}",
                task_id=task.task_id,
                event_type=EventType.RUN_REQUESTED,
                source="ci-webhook-demo",
                dedupe_key=f"{task.task_id}:demo-run",
                occurred_at=utc_now(),
            )
        )
        if transition.outcome != "APPLIED":
            raise ValidationError(transition.reason or "Demo task could not start.")

        subject = f"{repository}#workflow:{workflow}#branch:{branch}"
        wait = TrustedEventService(
            store=self.store,
            service=self.service,
        ).register_wait(
            task.task_id,
            provider="github",
            kind=ExternalEventKind.CI_COMPLETED,
            subject=subject,
            condition={
                "action": "completed",
                "status": "completed",
                "conclusion": "success",
            },
            timeout_seconds=timeout_seconds,
        )
        return CiDemoPreparation(
            task_id=task.task_id,
            wait_id=wait.wait_id,
            state=self.store.get_task(task.task_id).state,
            repository=repository,
            workflow=workflow,
            branch=branch,
            subject=subject,
        )

    def evidence(self, task_id: str) -> CiDemoEvidence:
        task = self.store.get_task(task_id)
        waits = self.store.list_signal_waits(task_id)
        events = self.store.list_external_events(task_id)
        wait = waits[-1] if waits else None
        event = events[-1] if events else None
        audit_valid = self.store.verify_audit_chain(task_id)
        passed = (
            task.state == TaskState.READY
            and wait is not None
            and wait.status == SignalWaitStatus.SATISFIED
            and event is not None
            and event.status == ExternalEventStatus.CONSUMED
            and event.authenticated
            and audit_valid
        )
        return CiDemoEvidence(
            task_id=task_id,
            passed=passed,
            task_state=task.state,
            wait_status=wait.status if wait else None,
            event_status=event.status if event else None,
            event_authenticated=bool(event and event.authenticated),
            event_content_trust=event.content_trust if event else None,
            audit_chain_valid=audit_valid,
        )
