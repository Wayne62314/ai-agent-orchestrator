from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.adapters.fake import FakeExecutionAdapter
from agent_orchestrator.adapters.trusted_events import RateLimitEventAdapter
from agent_orchestrator.authorization import (
    ApprovalService,
    SideEffectCoordinator,
)
from agent_orchestrator.checkpoint import CheckpointService
from agent_orchestrator.errors import ConcurrencyError
from agent_orchestrator.execution import ExecutionCoordinator
from agent_orchestrator.external_events import TrustedEventService
from agent_orchestrator.models import TaskState, WorktreeState
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore
from agent_orchestrator.task_lifecycle import TaskLifecycleService
from agent_orchestrator.verification import VerificationCoordinator
from agent_orchestrator.workspace import WorkspaceInspector
from agent_orchestrator.worktrees import WorktreeService


@unittest.skipUnless(shutil.which("git"), "Git is required")
class TaskLifecycleStageEightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self._git("init")
        self._git("config", "user.name", "Stage Eight Test")
        self._git("config", "user.email", "stage8@example.invalid")
        (self.repository / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "initial")

        self.store = SQLiteStore(self.root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        approvals = ApprovalService(store=self.store, service=self.service)
        self.worktrees = WorktreeService(
            store=self.store,
            side_effects=SideEffectCoordinator(
                store=self.store,
                approvals=approvals,
            ),
            managed_root=self.root / "worktrees",
        )
        self.adapter = FakeExecutionAdapter()
        self.execution = ExecutionCoordinator(
            store=self.store,
            service=self.service,
            adapter=self.adapter,
            owner="desktop-process",
        )
        self.lifecycle = TaskLifecycleService(
            store=self.store,
            service=self.service,
            execution=self.execution,
            worktrees=self.worktrees,
            checkpoints=CheckpointService(
                self.store,
                self.root / "checkpoints",
            ),
            workspace=WorkspaceInspector(),
            verifier=VerificationCoordinator(
                store=self.store,
                service=self.service,
            ),
            owner="desktop-process",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_create_pause_resume_verify_and_retain(self) -> None:
        created = self._create("task-lifecycle")
        self.assertEqual(created.task.state, TaskState.READY)
        self.assertTrue(Path(created.worktree_path).is_dir())

        started = self.lifecycle.start(
            created.task.task_id,
            prompt="Make the requested local change.",
            sandbox="workspace-write",
        )
        paused = self.lifecycle.pause(created.task.task_id)
        self.assertEqual(paused.finished.transition.current_state, TaskState.PAUSED)
        self.assertEqual(
            self.store.latest_checkpoint(created.task.task_id).status,
            "READY",
        )

        resumed = self.lifecycle.resume(
            created.task.task_id,
            sandbox="workspace-write",
        )
        self.assertEqual(
            resumed.started.record.thread_id,
            started.record.thread_id,
        )
        self.assertEqual(
            resumed.started.record.input_checkpoint_id,
            paused.checkpoint.checkpoint_id,
        )
        self.adapter.complete(
            resumed.started.record.run_id,
            final_response="Completed.",
        )
        completed = self.lifecycle.collect(created.task.task_id)
        self.assertEqual(completed.task.state, TaskState.SUCCEEDED)
        self.assertIsNotNone(completed.verification)
        self.assertIsNone(self.store.get_active_task())
        self.assertEqual(
            self.store.get_worktree(created.task.task_id).state,
            WorktreeState.RETAINED,
        )

    def test_only_one_nonterminal_task_can_own_execution_slot(self) -> None:
        first = self._create("task-first")
        second = self._create("task-second")
        self.lifecycle.start(first.task.task_id, prompt="First.")
        with self.assertRaises(ConcurrencyError):
            self.lifecycle.start(second.task.task_id, prompt="Second.")
        self.lifecycle.cancel(first.task.task_id)
        started = self.lifecycle.start(second.task.task_id, prompt="Second.")
        self.assertEqual(started.record.task_id, second.task.task_id)

    def test_cancel_running_task_checkpoints_and_releases_slot(self) -> None:
        created = self._create("task-cancel")
        self.lifecycle.start(created.task.task_id, prompt="Work.")
        transition = self.lifecycle.cancel(created.task.task_id)
        self.assertEqual(transition.current_state, TaskState.CANCELLED)
        self.assertEqual(
            self.store.latest_checkpoint(created.task.task_id).status,
            "READY",
        )
        self.assertIsNone(self.store.get_active_task())
        self.assertEqual(
            self.store.get_worktree(created.task.task_id).state,
            WorktreeState.RETAINED,
        )

    def test_expired_run_can_be_checkpointed_and_resumed_after_restart(self) -> None:
        created = self._create("task-restart")
        original = self.lifecycle.start(created.task.task_id, prompt="Work.")
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at = ? WHERE run_id = ?",
                ("2000-01-01T00:00:00+00:00", original.record.run_id),
            )
            connection.execute(
                "UPDATE active_task_lease SET expires_at = ? WHERE slot = 1",
                ("2000-01-01T00:00:00+00:00",),
            )

        restarted_adapter = FakeExecutionAdapter()
        restarted = TaskLifecycleService(
            store=self.store,
            service=self.service,
            execution=ExecutionCoordinator(
                store=self.store,
                service=self.service,
                adapter=restarted_adapter,
                owner="desktop-process",
            ),
            worktrees=self.worktrees,
            checkpoints=CheckpointService(
                self.store,
                self.root / "checkpoints",
            ),
            workspace=WorkspaceInspector(),
            verifier=None,
            owner="desktop-process",
        )
        candidates = restarted.recover_expired()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].task.state,
            TaskState.NEEDS_ATTENTION,
        )
        resumed = restarted.recover(
            created.task.task_id,
            sandbox="workspace-write",
        )
        self.assertEqual(
            resumed.started.record.thread_id,
            original.record.thread_id,
        )
        self.assertEqual(
            self.store.get_task(created.task.task_id).state,
            TaskState.RUNNING,
        )

    def test_usage_limit_waits_for_trusted_signal_then_continues(self) -> None:
        created = self._create("task-rate-limit")
        started = self.lifecycle.start(created.task.task_id, prompt="Work.")
        self.adapter.fail(
            started.record.run_id,
            error_code="UsageLimitExceeded",
            error_message="Wait for the current usage window.",
        )
        limited = self.lifecycle.collect(created.task.task_id)
        self.assertEqual(limited.task.state, TaskState.WAITING_FOR_SIGNAL)
        self.assertEqual(
            self.store.latest_checkpoint(created.task.task_id).status,
            "READY",
        )

        events = TrustedEventService(store=self.store, service=self.service)
        events.ingest_trusted_local(
            created.task.task_id,
            adapter=RateLimitEventAdapter(),
            payload={
                "provider": "codex",
                "bucket": "codex",
                "available": True,
                "remaining": 100,
            },
            delivery_id="stage8-rate-restored",
        )
        resumed = self.lifecycle.continue_ready(
            created.task.task_id,
            sandbox="workspace-write",
        )
        self.assertEqual(
            resumed.started.record.thread_id,
            started.record.thread_id,
        )
        self.assertEqual(
            self.store.get_task(created.task.task_id).state,
            TaskState.RUNNING,
        )

    def _create(self, task_id: str):
        return self.lifecycle.create(
            repository_path=self.repository,
            title=task_id,
            objective="Exercise the complete local lifecycle.",
            acceptance_policy={
                "checks": [
                    {
                        "name": "python-ok",
                        "command": [
                            sys.executable,
                            "-c",
                            "print('ok')",
                        ],
                    }
                ],
                "max_repair_attempts": 0,
            },
            task_id=task_id,
        )

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
