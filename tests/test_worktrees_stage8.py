from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.authorization import (
    ApprovalService,
    SideEffectCoordinator,
)
from agent_orchestrator.errors import ValidationError
from agent_orchestrator.models import Event, EventType, WorktreeState
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore, utc_now
from agent_orchestrator.worktrees import WorktreeService


@unittest.skipUnless(shutil.which("git"), "Git is required")
class WorktreeStageEightTests(unittest.TestCase):
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
        self.approvals = approvals
        self.worktrees = WorktreeService(
            store=self.store,
            side_effects=SideEffectCoordinator(
                store=self.store,
                approvals=approvals,
            ),
            managed_root=self.root / "managed-worktrees",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_prepare_isolated_worktree_and_retain_it(self) -> None:
        inspection = self.worktrees.inspect_repository(self.repository)
        task = self.service.create_task(
            title="Worktree task",
            objective="Create an isolated worktree",
            workspace_path=self.root / "managed-worktrees" / "task-worktree",
            permissions_policy={
                "git": {"worktree": {"create": "allow"}},
                "filesystem": {"delete": {"worktree": "ask"}},
            },
            task_id="task-worktree",
        )
        record = self.worktrees.prepare(
            task_id=task.task_id,
            repository=inspection,
        )
        self.assertEqual(record.state, WorktreeState.ACTIVE)
        self.assertTrue(Path(record.worktree_path).is_dir())
        self.assertEqual(record.base_revision, inspection.head_revision)
        self.assertEqual(
            self.worktrees.validate(task.task_id).branch_name,
            "aiao/task-taskworktree",
        )
        retained = self.worktrees.retain(task.task_id)
        self.assertEqual(retained.state, WorktreeState.RETAINED)
        self.assertTrue((self.repository / "README.md").is_file())

    def test_clean_terminal_worktree_requires_exact_approval_to_remove(self) -> None:
        inspection = self.worktrees.inspect_repository(self.repository)
        task = self.service.create_task(
            title="Cleanup task",
            objective="Remove only an approved clean worktree",
            workspace_path=self.root / "managed-worktrees" / "task-cleanup",
            permissions_policy={
                "git": {"worktree": {"create": "allow"}},
                "filesystem": {"delete": {"worktree": "ask"}},
            },
            task_id="task-cleanup",
        )
        record = self.worktrees.prepare(
            task_id=task.task_id,
            repository=inspection,
        )
        self.service.process_event(
            Event(
                event_id="evt-cancel-cleanup",
                task_id=task.task_id,
                event_type=EventType.CANCEL_REQUESTED,
                source="test",
                dedupe_key="cancel-cleanup",
                occurred_at=utc_now(),
            )
        )
        action = self.worktrees.cleanup_action(task.task_id)
        requested = self.approvals.request(task.task_id, action)
        approval = requested.approval
        self.assertIsNotNone(approval)
        assert approval is not None
        self.approvals.decide(
            approval.approval_id,
            approved=True,
            expected_action_hash=action.action_hash,
            decided_by="local-user",
        )
        removed = self.worktrees.cleanup(
            task.task_id,
            approval_id=approval.approval_id,
        )
        self.assertEqual(removed.state, WorktreeState.REMOVED)
        self.assertFalse(Path(record.worktree_path).exists())
        self.assertEqual(
            self._git(
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{record.branch_name}",
                check=False,
            ).returncode,
            0,
        )

    def test_dirty_worktree_is_never_removed(self) -> None:
        inspection = self.worktrees.inspect_repository(self.repository)
        task = self.service.create_task(
            title="Dirty task",
            objective="Retain dirty work",
            workspace_path=self.root / "managed-worktrees" / "task-dirty",
            permissions_policy={
                "git": {"worktree": {"create": "allow"}},
                "filesystem": {"delete": {"worktree": "ask"}},
            },
            task_id="task-dirty",
        )
        record = self.worktrees.prepare(
            task_id=task.task_id,
            repository=inspection,
        )
        (Path(record.worktree_path) / "new.txt").write_text(
            "keep me\n",
            encoding="utf-8",
        )
        self.service.process_event(
            Event(
                event_id="evt-cancel-dirty",
                task_id=task.task_id,
                event_type=EventType.CANCEL_REQUESTED,
                source="test",
                dedupe_key="cancel-dirty",
                occurred_at=utc_now(),
            )
        )
        with self.assertRaisesRegex(ValidationError, "Dirty worktrees"):
            self.worktrees.cleanup(task.task_id, approval_id="unused")
        self.assertTrue((Path(record.worktree_path) / "new.txt").is_file())

    def _git(
        self,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )


if __name__ == "__main__":
    unittest.main()
