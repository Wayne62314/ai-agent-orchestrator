"""Persistent Run coordination around an external execution adapter."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .adapters.base import (
    ExecutionAdapter,
    RunHandle,
    RunRequest,
    RunResult,
    RunStatus,
)
from .errors import ValidationError
from .models import Event, EventResult, EventType, RunRecord, RunState, TaskState
from .service import OrchestratorService
from .store import SQLiteStore, utc_now


@dataclass(frozen=True, slots=True)
class StartedRun:
    record: RunRecord
    handle: RunHandle
    transition: EventResult


@dataclass(frozen=True, slots=True)
class FinishedRun:
    record: RunRecord
    result: RunResult
    transition: EventResult


class ExecutionCoordinator:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        service: OrchestratorService,
        adapter: ExecutionAdapter,
        owner: str,
        lease_seconds: int = 120,
    ):
        if not owner.strip():
            raise ValidationError("Execution owner cannot be empty.")
        self.store = store
        self.service = service
        self.adapter = adapter
        self.owner = owner
        self.lease_seconds = lease_seconds

    def start(
        self,
        *,
        task_id: str,
        prompt: str,
        sandbox: str = "read-only",
        thread_id: str | None = None,
        input_checkpoint_id: str | None = None,
    ) -> StartedRun:
        task = self.store.get_task(task_id)
        if task.state != TaskState.READY:
            raise ValidationError(
                f"Task {task_id!r} must be READY, found {task.state.value}."
            )
        run_id = f"run_{uuid.uuid4().hex}"
        record = self.store.create_run(
            run_id=run_id,
            task_id=task_id,
            engine=type(self.adapter).__name__,
            lease_owner=self.owner,
            lease_seconds=self.lease_seconds,
            input_checkpoint_id=input_checkpoint_id,
        )
        transition = self.service.process_event(
            self._event(
                task_id=task_id,
                run_id=run_id,
                event_type=EventType.RUN_REQUESTED,
                expected_version=task.version,
                suffix="start",
            )
        )
        if transition.outcome != "APPLIED":
            self.store.finish_run(
                run_id=run_id,
                lease_owner=self.owner,
                state=RunState.FAILED,
                exit_reason="task_transition_rejected",
            )
            raise ValidationError(transition.reason or "Run transition was rejected.")
        handle: RunHandle | None = None
        try:
            handle = self.adapter.start(
                RunRequest(
                    task_id=task_id,
                    run_id=run_id,
                    workspace_path=task.workspace_path,
                    prompt=prompt,
                    sandbox=sandbox,
                    thread_id=thread_id,
                    metadata={"checkpoint_id": input_checkpoint_id},
                )
            )
            record = self.store.bind_run(
                run_id=run_id,
                lease_owner=self.owner,
                provider_run_id=handle.provider_run_id,
                thread_id=handle.thread_id,
                lease_seconds=self.lease_seconds,
            )
            return StartedRun(
                record=record,
                handle=handle,
                transition=transition,
            )
        except Exception as exc:
            if handle is not None:
                try:
                    self.adapter.interrupt(handle)
                except Exception:
                    pass
            self.store.finish_run(
                run_id=run_id,
                lease_owner=self.owner,
                state=RunState.FAILED,
                exit_reason="adapter_start_failed",
                result_summary=f"{type(exc).__name__}: {exc}"[:4000],
            )
            current = self.store.get_task(task_id)
            if current.state == TaskState.RUNNING:
                self.service.process_event(
                    self._event(
                        task_id=task_id,
                        run_id=run_id,
                        event_type=EventType.RUN_FAILED,
                        expected_version=current.version,
                        suffix="start-failed",
                    )
                )
            raise

    def heartbeat(self, run_id: str) -> RunRecord:
        return self.store.heartbeat_run(
            run_id=run_id,
            lease_owner=self.owner,
            lease_seconds=self.lease_seconds,
        )

    def collect(
        self,
        started: StartedRun,
        *,
        before_transition: Callable[[RunRecord, RunResult], None] | None = None,
    ) -> FinishedRun:
        return self.finish_result(
            started,
            self.await_result(started),
            before_transition=before_transition,
        )

    def await_result(self, started: StartedRun) -> RunResult:
        return self.adapter.collect(started.handle)

    def request_interrupt(self, started: StartedRun) -> None:
        self.adapter.request_interrupt(started.handle)

    def finish_result(
        self,
        started: StartedRun,
        result: RunResult,
        *,
        forced_event: EventType | None = None,
        before_transition: Callable[[RunRecord, RunResult], None] | None = None,
    ) -> FinishedRun:
        return self._finish(
            started,
            result,
            forced_event=forced_event,
            before_transition=before_transition,
        )

    def interrupt(self, started: StartedRun) -> FinishedRun:
        result = self.adapter.interrupt(started.handle)
        return self._finish(started, result)

    def pause(
        self,
        started: StartedRun,
        *,
        before_transition: Callable[[RunRecord, RunResult], None],
    ) -> FinishedRun:
        result = self.adapter.interrupt(started.handle)
        if result.status == RunStatus.FAILED:
            return self._finish(started, result)
        return self._finish(
            started,
            result,
            forced_event=EventType.PAUSE_REQUESTED,
            before_transition=before_transition,
        )

    def cancel(
        self,
        started: StartedRun,
        *,
        before_transition: Callable[[RunRecord, RunResult], None],
    ) -> FinishedRun:
        result = self.adapter.interrupt(started.handle)
        return self._finish(
            started,
            result,
            forced_event=EventType.CANCEL_REQUESTED,
            before_transition=before_transition,
        )

    def recover_expired(self) -> list[RunRecord]:
        recovered: list[RunRecord] = []
        for run in self.store.list_expired_runs():
            abandoned = self.store.abandon_expired_run(run_id=run.run_id)
            task = self.store.get_task(run.task_id)
            if task.state == TaskState.RUNNING:
                self.service.process_event(
                    self._event(
                        task_id=run.task_id,
                        run_id=run.run_id,
                        event_type=EventType.RUN_FAILED,
                        expected_version=task.version,
                        suffix="lease-expired",
                    )
                )
            recovered.append(abandoned)
        return recovered

    def _finish(
        self,
        started: StartedRun,
        result: RunResult,
        *,
        forced_event: EventType | None = None,
        before_transition: Callable[[RunRecord, RunResult], None] | None = None,
    ) -> FinishedRun:
        run_state, event_type = {
            RunStatus.COMPLETED: (RunState.COMPLETED, EventType.PHASE_COMPLETED),
            RunStatus.INTERRUPTED: (
                RunState.INTERRUPTED,
                EventType.SIGNAL_REQUIRED,
            ),
            RunStatus.FAILED: (RunState.FAILED, EventType.RUN_FAILED),
        }.get(
            result.status,
            (RunState.FAILED, EventType.RUN_FAILED),
        )
        if self._is_usage_limit(result):
            event_type = EventType.SIGNAL_REQUIRED
        record = self.store.finish_run(
            run_id=started.record.run_id,
            lease_owner=self.owner,
            state=run_state,
            exit_reason=result.error_code,
            result_summary=(
                result.final_response or result.error_message or ""
            )[:4000],
        )
        if before_transition is not None:
            try:
                before_transition(record, result)
            except BaseException:
                task = self.store.get_task(record.task_id)
                if task.state == TaskState.RUNNING:
                    self.service.process_event(
                        self._event(
                            task_id=record.task_id,
                            run_id=record.run_id,
                            event_type=EventType.RUN_FAILED,
                            expected_version=task.version,
                            suffix="pre-transition-failed",
                        )
                    )
                raise
        task = self.store.get_task(record.task_id)
        if (
            forced_event == EventType.PAUSE_REQUESTED
            and result.status == RunStatus.FAILED
        ):
            event_type = EventType.RUN_FAILED
        elif forced_event is not None:
            event_type = forced_event
        transition = self.service.process_event(
            self._event(
                task_id=record.task_id,
                run_id=record.run_id,
                event_type=event_type,
                expected_version=task.version,
                suffix=f"finish-{run_state.value.lower()}",
            )
        )
        return FinishedRun(
            record=record,
            result=result,
            transition=transition,
        )

    @staticmethod
    def _is_usage_limit(result: RunResult) -> bool:
        normalized = (result.error_code or "").replace("_", "").casefold()
        return normalized in {
            "usagelimitexceeded",
            "ratelimitexceeded",
        }

    @staticmethod
    def _event(
        *,
        task_id: str,
        run_id: str,
        event_type: EventType,
        expected_version: int,
        suffix: str,
    ) -> Event:
        return Event(
            event_id=f"evt_{uuid.uuid4().hex}",
            task_id=task_id,
            event_type=event_type,
            source="execution-coordinator",
            dedupe_key=f"{task_id}:{run_id}:{suffix}",
            payload={"run_id": run_id},
            occurred_at=utc_now(),
            expected_version=expected_version,
        )
