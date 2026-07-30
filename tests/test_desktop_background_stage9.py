from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from agent_orchestrator.adapters.base import (
    RunHandle,
    RunRequest,
    RunResult,
    RunStatus,
)
from agent_orchestrator.authorization import ApprovalService, SideEffectCoordinator
from agent_orchestrator.checkpoint import CheckpointService
from agent_orchestrator.desktop_rpc import DesktopRunCoordinator
from agent_orchestrator.execution import ExecutionCoordinator
from agent_orchestrator.models import RunState, TaskState
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore
from agent_orchestrator.task_lifecycle import TaskLifecycleService
from agent_orchestrator.verification import VerificationCoordinator
from agent_orchestrator.worktrees import WorktreeService


class BlockingExecutionAdapter:
    def __init__(self) -> None:
        self.handles: dict[str, RunHandle] = {}
        self.results: dict[str, RunResult] = {}
        self.completed: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.complete_on_interrupt = False

    def start(self, request: RunRequest) -> RunHandle:
        handle = RunHandle(
            run_id=request.run_id,
            provider_run_id=f"blocking-{request.run_id}",
            thread_id=request.thread_id or f"thread-{request.task_id}",
            status=RunStatus.RUNNING,
        )
        with self.lock:
            self.handles[request.run_id] = handle
            self.completed[request.run_id] = threading.Event()
        return handle

    def inspect(self, handle: RunHandle) -> RunHandle:
        with self.lock:
            return self.handles[handle.run_id]

    def request_interrupt(self, handle: RunHandle) -> None:
        with self.lock:
            current = self.handles[handle.run_id]
            if current.status.is_terminal:
                return
            if self.complete_on_interrupt:
                self.complete(handle.run_id)
                return
            result = RunResult(
                run_id=handle.run_id,
                provider_run_id=handle.provider_run_id,
                thread_id=handle.thread_id,
                status=RunStatus.INTERRUPTED,
            )
            self.handles[handle.run_id] = replace(
                current,
                status=RunStatus.INTERRUPTED,
            )
            self.results[handle.run_id] = result
            self.completed[handle.run_id].set()

    def interrupt(self, handle: RunHandle) -> RunResult:
        self.request_interrupt(handle)
        return self.collect(handle)

    def collect(self, handle: RunHandle) -> RunResult:
        if not self.completed[handle.run_id].wait(timeout=5):
            raise TimeoutError("Test Run did not complete.")
        with self.lock:
            return self.results[handle.run_id]

    def complete(self, run_id: str) -> None:
        with self.lock:
            current = self.handles[run_id]
            result = RunResult(
                run_id=run_id,
                provider_run_id=current.provider_run_id,
                thread_id=current.thread_id,
                status=RunStatus.COMPLETED,
                final_response="done",
            )
            self.handles[run_id] = replace(
                current,
                status=RunStatus.COMPLETED,
            )
            self.results[run_id] = result
            self.completed[run_id].set()


@unittest.skipUnless(shutil.which("git"), "Git is required")
class DesktopBackgroundStageNineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self._git("init")
        self._git("config", "user.name", "Background Test")
        self._git("config", "user.email", "background@example.invalid")
        (self.repository / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "initial")

        self.store = SQLiteStore(self.root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        approvals = ApprovalService(store=self.store, service=self.service)
        self.adapter = BlockingExecutionAdapter()
        self.lifecycle = TaskLifecycleService(
            store=self.store,
            service=self.service,
            execution=ExecutionCoordinator(
                store=self.store,
                service=self.service,
                adapter=self.adapter,
                owner="desktop-background-test",
                lease_seconds=30,
            ),
            worktrees=WorktreeService(
                store=self.store,
                side_effects=SideEffectCoordinator(
                    store=self.store,
                    approvals=approvals,
                ),
                managed_root=self.root / "worktrees",
            ),
            checkpoints=CheckpointService(
                self.store,
                self.root / "checkpoints",
            ),
            verifier=VerificationCoordinator(
                store=self.store,
                service=self.service,
            ),
            owner="desktop-background-test",
            active_lease_seconds=30,
        )
        self.background = DesktopRunCoordinator(
            self.lifecycle,
            heartbeat_seconds=0.01,
            recovery_seconds=0.01,
            control_timeout_seconds=2,
        )
        self.background.start()

    def tearDown(self) -> None:
        self.background.close()
        self.temporary_directory.cleanup()

    def test_background_completion_runs_verification_and_releases_slot(self) -> None:
        task_id, run_id = self._start("complete")
        self.adapter.complete(run_id)
        self._wait_for_state(task_id, TaskState.SUCCEEDED)
        self._wait_for_slot_release()

        run = self.store.latest_run(task_id)
        self.assertIsNotNone(run)
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertIsNone(self.store.get_active_task())
        self.assertFalse(self.background.status()["running"])

    def test_pause_has_one_settlement_and_one_checkpoint(self) -> None:
        task_id, _run_id = self._start("pause")
        self.background.pause(task_id)

        self.assertEqual(self.store.get_task(task_id).state, TaskState.PAUSED)
        self.assertEqual(self._run_count(task_id), 1)
        self.assertEqual(
            self.store.latest_checkpoint(task_id).status,
            "READY",
        )

    def test_background_heartbeat_renews_the_run_lease(self) -> None:
        task_id, run_id = self._start("heartbeat")
        initial = self.store.get_run(run_id).heartbeat_at
        for _index in range(200):
            if self.store.get_run(run_id).heartbeat_at != initial:
                break
            threading.Event().wait(0.01)
        else:
            self.fail("The desktop coordinator did not renew the Run lease.")
        self.background.cancel(task_id)

    def test_cancel_has_one_settlement_and_releases_slot(self) -> None:
        task_id, _run_id = self._start("cancel")
        self.background.cancel(task_id)

        self.assertEqual(self.store.get_task(task_id).state, TaskState.CANCELLED)
        self.assertEqual(self._run_count(task_id), 1)
        self.assertIsNone(self.store.get_active_task())

    def test_provider_completion_wins_a_late_pause_race(self) -> None:
        task_id, _run_id = self._start("completion-race")
        self.adapter.complete_on_interrupt = True
        self.background.pause(task_id)

        self.assertEqual(self.store.get_task(task_id).state, TaskState.SUCCEEDED)
        self.assertEqual(self._run_count(task_id), 1)
        self.assertIsNone(self.store.get_active_task())

    def test_recovery_loop_converts_an_expired_run_to_attention(self) -> None:
        task_id, run_id = self._start("restart", track=False)
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at = ? WHERE run_id = ?",
                ("2000-01-01T00:00:00+00:00", run_id),
            )
            connection.execute(
                "UPDATE active_task_lease SET expires_at = ? WHERE slot = 1",
                ("2000-01-01T00:00:00+00:00",),
            )
        self._wait_for_state(task_id, TaskState.NEEDS_ATTENTION)
        self._wait_for_checkpoint(task_id)
        self.assertEqual(
            self.store.latest_checkpoint(task_id).status,
            "READY",
        )

    def _start(self, suffix: str, *, track: bool = True) -> tuple[str, str]:
        created = self.lifecycle.create(
            repository_path=self.repository,
            title=f"Background {suffix}",
            objective="Verify one durable desktop settlement path.",
            permissions_policy={
                "codex_sandbox": "workspace-write",
                "git": {"worktree": {"create": "allow"}},
                "filesystem": {"delete": {"worktree": "ask"}},
            },
            acceptance_policy={
                "checks": [
                    {
                        "name": "unit",
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                ],
                "max_repair_attempts": 0,
            },
            task_id=f"task-background-{suffix}",
        )
        started = self.lifecycle.start(
            created.task.task_id,
            sandbox="workspace-write",
        )
        if track:
            self.background.track(
                created.task.task_id,
                sandbox="workspace-write",
            )
        return created.task.task_id, started.record.run_id

    def _wait_for_state(self, task_id: str, state: TaskState) -> None:
        for _index in range(200):
            if self.store.get_task(task_id).state == state:
                return
            threading.Event().wait(0.01)
        self.fail(
            f"Task {task_id} did not reach {state.value}; "
            f"found {self.store.get_task(task_id).state.value}."
        )

    def _run_count(self, task_id: str) -> int:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM runs WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row["count"])

    def _wait_for_slot_release(self) -> None:
        for _index in range(200):
            if self.store.get_active_task() is None:
                return
            threading.Event().wait(0.01)
        self.fail("The active task slot was not released.")

    def _wait_for_checkpoint(self, task_id: str) -> None:
        for _index in range(200):
            try:
                self.store.latest_checkpoint(task_id)
                return
            except Exception:
                threading.Event().wait(0.01)
        self.fail(f"Task {task_id} did not produce a recovery checkpoint.")

    def _git(self, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
