from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.adapters.fake import FakeExecutionAdapter
from agent_orchestrator.authorization import (
    ActionRequest,
    ApprovalService,
    SideEffectCoordinator,
)
from agent_orchestrator.checkpoint import CheckpointService
from agent_orchestrator.desktop_rpc import (
    DesktopCommandService,
    DesktopQueryService,
    DesktopRpcApplication,
)
from agent_orchestrator.errors import ValidationError
from agent_orchestrator.execution import ExecutionCoordinator
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore
from agent_orchestrator.task_lifecycle import TaskLifecycleService
from agent_orchestrator.verification import VerificationCoordinator
from agent_orchestrator.worktrees import WorktreeService


@unittest.skipUnless(shutil.which("git"), "Git is required")
class DesktopCommandStageNineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self._git("init")
        self._git("config", "user.name", "Desktop Test")
        self._git("config", "user.email", "desktop@example.invalid")
        (self.repository / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "initial")

        self.store = SQLiteStore(self.root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        self.approvals = ApprovalService(
            store=self.store,
            service=self.service,
        )
        self.adapter = FakeExecutionAdapter()
        self.lifecycle = TaskLifecycleService(
            store=self.store,
            service=self.service,
            execution=ExecutionCoordinator(
                store=self.store,
                service=self.service,
                adapter=self.adapter,
                owner="desktop-test",
            ),
            worktrees=WorktreeService(
                store=self.store,
                side_effects=SideEffectCoordinator(
                    store=self.store,
                    approvals=self.approvals,
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
            owner="desktop-test",
        )
        queries = DesktopQueryService(self.store)
        self.application = DesktopRpcApplication(
            queries,
            DesktopCommandService(
                store=self.store,
                queries=queries,
                lifecycle=self.lifecycle,
                approvals=self.approvals,
            ),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_create_is_idempotent_and_returns_the_ui_read_model(self) -> None:
        params = self._create_params()
        first = self.application.dispatch("task/create", params)
        second = self.application.dispatch("task/create", params)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["state"], "READY")
        self.assertEqual(first["repository"], str(self.repository.resolve()))
        self.assertTrue(Path(str(first["workspacePath"])).is_dir())
        self.assertEqual(len(self.store.list_tasks()), 1)

        snapshot = self.application.dispatch("system/initialize", {})
        self.assertEqual(snapshot["activeTask"]["id"], first["id"])
        self.assertEqual(snapshot["recentTasks"][0]["branch"], first["branch"])
        self.assertIsInstance(snapshot["approvals"], list)

    def test_start_pause_and_cancel_are_versioned_and_idempotent(self) -> None:
        created = self.application.dispatch("task/create", self._create_params())
        started = self.application.dispatch(
            "task/start",
            self._action_params(created),
        )
        self.assertEqual(started["state"], "RUNNING")

        paused = self.application.dispatch(
            "task/pause",
            self._action_params(started),
        )
        self.assertEqual(paused["state"], "PAUSED")
        duplicate = self.application.dispatch(
            "task/pause",
            {
                **self._action_params(paused),
                "expectedVersion": started["version"],
            },
        )
        self.assertEqual(duplicate["state"], "PAUSED")

        cancelled = self.application.dispatch(
            "task/cancel",
            self._action_params(paused),
        )
        self.assertEqual(cancelled["state"], "CANCELLED")

    def test_stale_version_is_rejected_before_a_state_change(self) -> None:
        created = self.application.dispatch("task/create", self._create_params())
        with self.assertRaises(ValidationError):
            self.application.dispatch(
                "task/start",
                {
                    **self._action_params(created),
                    "expectedVersion": created["version"] - 1,
                },
            )
        self.assertEqual(self.store.get_task(str(created["id"])).state.value, "READY")

    def test_approval_decision_is_bound_to_the_displayed_hash(self) -> None:
        created = self.application.dispatch("task/create", self._create_params())
        self.application.dispatch("task/start", self._action_params(created))
        action = ActionRequest(
            action_type="git.push",
            logical_step="publish-task-branch",
            parameters={"remote": "origin", "branch": created["branch"]},
            risk_summary="Publishes the task branch.",
            rollback_plan="Delete the remote task branch.",
        )
        approval = self.approvals.request(str(created["id"]), action).approval
        self.assertIsNotNone(approval)

        with self.assertRaises(ValidationError):
            self.application.dispatch(
                "approval/decide",
                {
                    "approvalId": approval.approval_id,
                    "approved": True,
                    "expectedActionHash": "0" * 64,
                },
            )
        result = self.application.dispatch(
            "approval/decide",
            {
                "approvalId": approval.approval_id,
                "approved": True,
                "expectedActionHash": action.action_hash,
            },
        )
        self.assertEqual(result["status"], "APPROVED")

    def _create_params(self) -> dict:
        return {
            "input": {
                "title": "Desktop controlled task",
                "objective": "Exercise the real application-service boundary.",
                "repository": str(self.repository),
                "permission": "workspace-write",
                "checks": [
                    f"{sys.executable} -c \"print('ok')\"",
                ],
                "maxRepairs": 0,
            },
            "expectedVersion": 0,
            "idempotencyKey": "desktop-create-1",
        }

    @staticmethod
    def _action_params(task: dict) -> dict:
        return {
            "taskId": task["id"],
            "expectedVersion": task["version"],
            "idempotencyKey": f"{task['id']}:{task['state']}",
        }

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
