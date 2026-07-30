"""Product-level task lifecycle over worktrees, runs, and checkpoints."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .adapters.base import RunResult
from .checkpoint import CheckpointService
from .errors import ValidationError
from .execution import ExecutionCoordinator, FinishedRun, StartedRun
from .models import (
    CheckpointRecord,
    Event,
    EventResult,
    EventType,
    ExternalEventKind,
    Task,
    TaskState,
)
from .resume import ResumePackage, ResumePackageBuilder
from .service import OrchestratorService
from .store import SQLiteStore, utc_after, utc_now
from .verification import (
    VerificationCoordinator,
    VerificationSuite,
    build_repair_prompt,
)
from .workspace import WorkspaceInspector, WorkspaceSnapshot
from .worktrees import WorktreeService


@dataclass(frozen=True, slots=True)
class CreatedTask:
    task: Task
    repository_path: str
    worktree_path: str


@dataclass(frozen=True, slots=True)
class PausedTask:
    finished: FinishedRun
    checkpoint: CheckpointRecord


@dataclass(frozen=True, slots=True)
class ResumedTask:
    started: StartedRun
    package: ResumePackage


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    task: Task
    checkpoint: CheckpointRecord


@dataclass(frozen=True, slots=True)
class LifecycleCompletion:
    finished: FinishedRun
    verification: VerificationSuite | None
    task: Task


class TaskLifecycleService:
    """The application-facing lifecycle for one locally managed Codex task."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        service: OrchestratorService,
        execution: ExecutionCoordinator,
        worktrees: WorktreeService,
        checkpoints: CheckpointService,
        workspace: WorkspaceInspector | None = None,
        resume_builder: ResumePackageBuilder | None = None,
        verifier: VerificationCoordinator | None = None,
        owner: str,
        active_lease_seconds: int = 180,
        rate_wait_timeout_seconds: int = 86_400,
    ):
        if not owner.strip():
            raise ValidationError("Lifecycle owner cannot be empty.")
        if active_lease_seconds < 30:
            raise ValidationError("Active-task lease must be at least 30 seconds.")
        if rate_wait_timeout_seconds < 60:
            raise ValidationError(
                "Rate-limit wait timeout must be at least 60 seconds."
            )
        self.store = store
        self.service = service
        self.execution = execution
        self.worktrees = worktrees
        self.checkpoints = checkpoints
        self.workspace = workspace or WorkspaceInspector()
        self.resume_builder = resume_builder or ResumePackageBuilder()
        self.verifier = verifier
        self.owner = owner.strip()
        self.active_lease_seconds = active_lease_seconds
        self.rate_wait_timeout_seconds = rate_wait_timeout_seconds
        self._live: dict[str, StartedRun] = {}

    def create(
        self,
        *,
        repository_path: str | Path,
        title: str,
        objective: str,
        permissions_policy: Mapping[str, Any] | None = None,
        acceptance_policy: Mapping[str, Any] | None = None,
        retry_policy: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> CreatedTask:
        inspection = self.worktrees.inspect_repository(repository_path)
        identity = task_id or f"task_{uuid.uuid4().hex}"
        target = self.worktrees.path_for_task(identity)
        self.service.create_task(
            title=title,
            objective=objective,
            workspace_path=target,
            permissions_policy=permissions_policy
            or {
                "git": {"worktree": {"create": "allow"}},
                "filesystem": {"delete": {"worktree": "ask"}},
            },
            acceptance_policy=acceptance_policy,
            retry_policy=retry_policy,
            task_id=identity,
        )
        try:
            record = self.worktrees.prepare(
                task_id=identity,
                repository=inspection,
            )
            transition = self.service.validate_task(identity)
            if transition.outcome != "APPLIED":
                raise ValidationError(
                    transition.reason or "Task validation was rejected."
                )
        except BaseException:
            current = self.store.get_task(identity)
            if current.state == TaskState.DRAFT:
                self.service.process_event(
                    self._event(
                        current,
                        EventType.SETUP_FAILED,
                        suffix="worktree-setup-failed",
                    )
                )
            raise
        return CreatedTask(
            task=self.store.get_task(identity),
            repository_path=record.repository_path,
            worktree_path=record.worktree_path,
        )

    def start(
        self,
        task_id: str,
        *,
        prompt: str | None = None,
        sandbox: str = "read-only",
        thread_id: str | None = None,
        input_checkpoint_id: str | None = None,
    ) -> StartedRun:
        task = self.store.get_task(task_id)
        if task.state != TaskState.READY:
            raise ValidationError(
                f"Task {task_id!r} must be READY, found {task.state.value}."
            )
        self.worktrees.validate(task_id)
        self.store.acquire_active_task(
            task_id=task_id,
            owner=self.owner,
            lease_seconds=self.active_lease_seconds,
        )
        try:
            started = self.execution.start(
                task_id=task_id,
                prompt=prompt or self._initial_prompt(task),
                sandbox=sandbox,
                thread_id=thread_id,
                input_checkpoint_id=input_checkpoint_id,
            )
        except BaseException:
            current = self.store.get_task(task_id)
            if current.state.is_terminal:
                self.store.release_active_task(task_id=task_id, owner=self.owner)
            raise
        self._live[task_id] = started
        return started

    def heartbeat(self, task_id: str) -> None:
        started = self._require_live(task_id)
        self.execution.heartbeat(started.record.run_id)
        self.store.heartbeat_active_task(
            task_id=task_id,
            owner=self.owner,
            lease_seconds=self.active_lease_seconds,
        )

    def pause(self, task_id: str) -> PausedTask:
        started = self._require_live(task_id)
        created: CheckpointRecord | None = None

        def checkpoint_before_pause(
            _record: object,
            result: RunResult,
        ) -> None:
            nonlocal created
            created = self._checkpoint(
                task_id,
                run_id=started.record.run_id,
                reason="user_pause",
                result=result,
                next_description="Resume the interrupted Codex turn.",
            )

        finished = self.execution.pause(
            started,
            before_transition=checkpoint_before_pause,
        )
        self._live.pop(task_id, None)
        if finished.transition.current_state != TaskState.PAUSED or created is None:
            raise ValidationError("The running task could not be paused safely.")
        self.store.heartbeat_active_task(
            task_id=task_id,
            owner=self.owner,
            lease_seconds=self.active_lease_seconds,
        )
        return PausedTask(finished=finished, checkpoint=created)

    def resume(
        self,
        task_id: str,
        *,
        sandbox: str = "read-only",
    ) -> ResumedTask:
        task = self.store.get_task(task_id)
        if task.state != TaskState.PAUSED:
            raise ValidationError(
                f"Task {task_id!r} must be PAUSED, found {task.state.value}."
            )
        self.worktrees.validate(task_id)
        self.store.acquire_active_task(
            task_id=task_id,
            owner=self.owner,
            lease_seconds=self.active_lease_seconds,
        )
        package = self._resume_package(task)
        transition = self.service.process_event(
            self._event(task, EventType.RESUME_REQUESTED, suffix="resume")
        )
        if transition.outcome != "APPLIED":
            raise ValidationError(transition.reason or "Resume was rejected.")
        started = self.start(
            task_id,
            prompt=package.prompt,
            sandbox=sandbox,
            thread_id=package.thread_id,
            input_checkpoint_id=package.checkpoint_id,
        )
        return ResumedTask(started=started, package=package)

    def recover_expired(self) -> tuple[RecoveryCandidate, ...]:
        """Convert abandoned runs into explicit, checkpointed recovery work."""
        recovered: list[RecoveryCandidate] = []
        for run in self.execution.recover_expired():
            task = self.store.get_task(run.task_id)
            checkpoint = self._checkpoint(
                task.task_id,
                run_id=run.run_id,
                reason="process_restart",
                result=None,
                next_description="Resume the interrupted Codex thread.",
            )
            recovered.append(
                RecoveryCandidate(task=task, checkpoint=checkpoint)
            )
        return tuple(recovered)

    def recover(
        self,
        task_id: str,
        *,
        sandbox: str = "read-only",
    ) -> ResumedTask:
        task = self.store.get_task(task_id)
        if task.state != TaskState.NEEDS_ATTENTION:
            raise ValidationError(
                f"Task {task_id!r} must be NEEDS_ATTENTION, "
                f"found {task.state.value}."
            )
        self.worktrees.validate(task_id)
        self.store.acquire_active_task(
            task_id=task_id,
            owner=self.owner,
            lease_seconds=self.active_lease_seconds,
        )
        package = self._resume_package(task)
        transition = self.service.process_event(
            self._event(
                task,
                EventType.ATTENTION_RESOLVED,
                suffix="restart-recovery",
            )
        )
        if transition.outcome != "APPLIED":
            raise ValidationError(
                transition.reason or "Restart recovery was rejected."
            )
        started = self.start(
            task_id,
            prompt=package.prompt,
            sandbox=sandbox,
            thread_id=package.thread_id,
            input_checkpoint_id=package.checkpoint_id,
        )
        return ResumedTask(started=started, package=package)

    def _resume_package(self, task: Task) -> ResumePackage:
        checkpoint = self.store.latest_checkpoint(task.task_id)
        payload = self.checkpoints.load(checkpoint)
        workspace_payload = payload.get("workspace")
        if not isinstance(workspace_payload, dict):
            raise ValidationError("Checkpoint workspace must be an object.")
        baseline = WorkspaceSnapshot.from_dict(workspace_payload)
        current = self.workspace.snapshot(task.workspace_path)
        relevant = workspace_payload.get("relevant_files") or []
        if not isinstance(relevant, list):
            raise ValidationError("Checkpoint relevant_files must be a list.")
        drift = self.workspace.compare(
            baseline,
            current,
            relevant_files=tuple(str(item) for item in relevant),
        )
        previous = self.store.latest_run(task.task_id)
        package = self.resume_builder.build(
            task=task,
            checkpoint=checkpoint,
            payload=payload,
            current_workspace=current,
            drift=drift,
            thread_id=previous.thread_id if previous is not None else None,
        )
        return package

    def collect(
        self,
        task_id: str,
        *,
        auto_repair: bool = True,
        sandbox: str = "read-only",
    ) -> LifecycleCompletion:
        started = self._require_live(task_id)
        def checkpoint_limit_wait(
            _record: object,
            result: RunResult,
        ) -> None:
            if not self._is_usage_limit(result):
                return
            self._checkpoint(
                task_id,
                run_id=started.record.run_id,
                reason="usage_limit",
                result=result,
                next_description=(
                    "Resume after the Codex rate-limit bucket is available."
                ),
            )
            self.store.create_signal_wait(
                wait_id=f"wait_{uuid.uuid4().hex}",
                task_id=task_id,
                provider="app-server",
                kind=ExternalEventKind.RATE_LIMIT,
                subject="codex:codex",
                condition={
                    "provider": "codex",
                    "bucket": "codex",
                    "available": True,
                },
                timeout_behavior="attention",
                deadline_at=utc_after(self.rate_wait_timeout_seconds),
            )

        finished = self.execution.collect(
            started,
            before_transition=checkpoint_limit_wait,
        )
        self._live.pop(task_id, None)
        suite: VerificationSuite | None = None
        while (
            self.verifier is not None
            and finished.transition.current_state == TaskState.VERIFYING
        ):
            suite = self.verifier.verify(
                task_id,
                run_id=finished.record.run_id,
            )
            if suite.transition.current_state != TaskState.READY or not auto_repair:
                break
            repair = self.start(
                task_id,
                prompt=build_repair_prompt(
                    self.store.get_task(task_id),
                    suite,
                ),
                sandbox=sandbox,
                thread_id=finished.record.thread_id,
            )
            finished = self.execution.collect(repair)
            self._live.pop(task_id, None)
        task = self.store.get_task(task_id)
        self._finalize_if_terminal(task)
        return LifecycleCompletion(
            finished=finished,
            verification=suite,
            task=task,
        )

    def continue_ready(
        self,
        task_id: str,
        *,
        sandbox: str = "read-only",
    ) -> ResumedTask:
        """Continue a READY task released by a trusted external signal."""
        task = self.store.get_task(task_id)
        if task.state != TaskState.READY:
            raise ValidationError(
                f"Task {task_id!r} must be READY, found {task.state.value}."
            )
        package = self._resume_package(task)
        started = self.start(
            task_id,
            prompt=package.prompt,
            sandbox=sandbox,
            thread_id=package.thread_id,
            input_checkpoint_id=package.checkpoint_id,
        )
        return ResumedTask(started=started, package=package)

    def cancel(self, task_id: str) -> EventResult:
        task = self.store.get_task(task_id)
        if task.state.is_terminal:
            raise ValidationError(f"Task {task_id!r} is already terminal.")
        started = self._live.get(task_id)
        if task.state == TaskState.RUNNING:
            if started is None:
                raise ValidationError(
                    "The running task is not attached to this process; recover "
                    "its run before cancelling."
                )

            def checkpoint_before_cancel(
                _record: object,
                result: RunResult,
            ) -> None:
                self._checkpoint(
                    task_id,
                    run_id=started.record.run_id,
                    reason="user_cancel",
                    result=result,
                    next_description="Inspect retained work before cleanup.",
                )

            finished = self.execution.cancel(
                started,
                before_transition=checkpoint_before_cancel,
            )
            transition = finished.transition
            self._live.pop(task_id, None)
        else:
            if task.state != TaskState.PAUSED:
                latest = self.store.latest_run(task_id)
                self._checkpoint(
                    task_id,
                    run_id=latest.run_id if latest is not None else None,
                    reason="user_cancel",
                    result=None,
                    next_description="Inspect retained work before cleanup.",
                )
            transition = self.service.process_event(
                self._event(task, EventType.CANCEL_REQUESTED, suffix="cancel")
            )
        self._finalize_if_terminal(self.store.get_task(task_id))
        return transition

    def _checkpoint(
        self,
        task_id: str,
        *,
        run_id: str | None,
        reason: str,
        result: RunResult | None,
        next_description: str,
    ) -> CheckpointRecord:
        task = self.store.get_task(task_id)
        snapshot = self.workspace.snapshot(task.workspace_path)
        relevant_files = tuple(snapshot.files)
        summary = ""
        if result is not None:
            summary = result.final_response or result.error_message or ""
        return self.checkpoints.create(
            task=task,
            run_id=run_id,
            workspace=snapshot,
            progress={
                "completed": [],
                "in_progress": [task.objective],
                "pending": [],
                "failed_attempts": [],
                "last_response": summary[:4000],
            },
            decisions=[],
            current_block={
                "reason": reason,
                "waiting_for": "user_resume"
                if reason == "user_pause"
                else "user_review",
            },
            next_action={
                "description": next_description,
                "expected_result": "Continue from durable local state.",
                "risk_level": "low",
            },
            verification={
                "last_results": [],
                "required_checks": task.acceptance_policy.get("checks", []),
            },
            permissions={
                "granted": task.permissions_policy,
                "approvals_required": [],
            },
            relevant_files=relevant_files,
        )

    def _finalize_if_terminal(self, task: Task) -> None:
        if not task.state.is_terminal:
            return
        try:
            self.worktrees.retain(task.task_id)
        except ValidationError:
            pass
        active = self.store.get_active_task()
        if (
            active is not None
            and active.task_id == task.task_id
            and active.owner == self.owner
        ):
            self.store.release_active_task(
                task_id=task.task_id,
                owner=self.owner,
            )

    def _require_live(self, task_id: str) -> StartedRun:
        try:
            return self._live[task_id]
        except KeyError as exc:
            raise ValidationError(
                f"Task {task_id!r} has no live run in this process."
            ) from exc

    @staticmethod
    def _is_usage_limit(result: RunResult) -> bool:
        normalized = (result.error_code or "").replace("_", "").casefold()
        return normalized in {
            "usagelimitexceeded",
            "ratelimitexceeded",
        }

    @staticmethod
    def _initial_prompt(task: Task) -> str:
        return (
            f"Task: {task.title}\n"
            f"Objective: {task.objective}\n"
            f"Acceptance policy: {dict(task.acceptance_policy)}\n"
            f"Permission boundary: {dict(task.permissions_policy)}\n"
            "Work only inside the assigned workspace. Report completed work, "
            "modified files, checks, unresolved issues, and recommended state."
        )

    @staticmethod
    def _event(
        task: Task,
        event_type: EventType,
        *,
        suffix: str,
    ) -> Event:
        return Event(
            event_id=f"evt_{uuid.uuid4().hex}",
            task_id=task.task_id,
            event_type=event_type,
            source="task-lifecycle",
            dedupe_key=f"{task.task_id}:{suffix}:{task.version}",
            payload={},
            occurred_at=utc_now(),
            expected_version=task.version,
        )
