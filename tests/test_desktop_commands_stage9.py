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
    DesktopAccountService,
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

    def test_repository_inspection_returns_canonical_read_only_summary(self) -> None:
        result = self.application.dispatch(
            "repository/inspect",
            {"path": str(self.repository / ".")},
        )
        self.assertEqual(result["repository"], str(self.repository.resolve()))
        self.assertEqual(result["branch"], "master")
        self.assertFalse(result["dirty"])
        self.assertEqual(result["dirtyPaths"], [])
        self.assertEqual(len(result["headRevision"]), 40)
        self.assertEqual(result["suggestedChecks"], [])

    def test_new_project_is_initialized_before_the_task_worktree(self) -> None:
        parent = self.root / "new-projects"
        parent.mkdir()
        params = self._create_params()
        params["idempotencyKey"] = "desktop-create-new-project"
        params["input"].update(
            {
                "repository": "",
                "repositoryMode": "new",
                "projectParent": str(parent),
                "projectName": "Clock Widget",
            }
        )

        created = self.application.dispatch("task/create", params)

        repository = parent / "Clock Widget"
        self.assertEqual(created["state"], "READY")
        self.assertEqual(created["repository"], str(repository.resolve()))
        self.assertEqual((repository / "README.md").read_text(encoding="utf-8"), "# Clock Widget\n")
        self.assertEqual(
            subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repository,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.strip(),
            "main",
        )
        self.assertTrue(Path(str(created["workspacePath"])).is_dir())
        effects = self.store.list_side_effects(str(created["id"]))
        self.assertEqual(
            [effect.action_type for effect in effects],
            ["git.repository.create", "git.worktree.create"],
        )

    def test_new_project_rejects_an_existing_target(self) -> None:
        parent = self.root / "new-projects"
        parent.mkdir()
        (parent / "Existing").mkdir()
        params = self._create_params()
        params["idempotencyKey"] = "desktop-create-existing-target"
        params["input"].update(
            {
                "repository": "",
                "repositoryMode": "new",
                "projectParent": str(parent),
                "projectName": "Existing",
            }
        )

        with self.assertRaisesRegex(ValidationError, "already exists"):
            self.application.dispatch("task/create", params)

        self.assertEqual(len(self.store.list_tasks()), 0)

    def test_repository_suggestions_only_use_declared_manifest_scripts(self) -> None:
        (self.repository / "package.json").write_text(
            '{"scripts":{"test":"vitest run"}}',
            encoding="utf-8",
        )
        result = self.application.dispatch(
            "repository/inspect",
            {"path": str(self.repository)},
        )
        self.assertEqual(
            result["suggestedChecks"],
            [
                {
                    "command": "npm test",
                    "source": "package.json → scripts.test",
                    "label": "运行前端测试",
                }
            ],
        )

    def test_task_without_project_commands_completes_with_ai_evidence(self) -> None:
        params = self._create_params()
        params["input"]["checks"] = []
        created = self.application.dispatch("task/create", params)
        started = self.application.dispatch(
            "task/start",
            self._action_params(created),
        )
        run = self.store.latest_run(str(started["id"]))
        assert run is not None
        self.adapter.complete(
            run.run_id,
            final_response="Implementation complete after reviewing the objective.",
        )
        completed = self.lifecycle.collect(str(started["id"]))

        self.assertEqual(completed.task.state.value, "SUCCEEDED")
        self.assertEqual(self.store.list_verifications(str(started["id"])), [])
        report = self.application.dispatch(
            "task/detail",
            {"taskId": started["id"], "section": "report"},
        )
        self.assertEqual(report["evidence"]["ai"]["status"], "PASSED")
        self.assertEqual(report["evidence"]["commands"]["configured"], 0)
        self.assertIsNone(report["evidence"]["manual"])

    def test_optional_manual_confirmation_controls_final_success(self) -> None:
        params = self._create_params()
        params["input"]["checks"] = []
        params["input"]["manualConfirmation"] = True
        created = self.application.dispatch("task/create", params)
        started = self.application.dispatch(
            "task/start",
            self._action_params(created),
        )
        run = self.store.latest_run(str(started["id"]))
        assert run is not None
        self.adapter.complete(run.run_id, final_response="Ready for review.")
        pending = self.lifecycle.collect(str(started["id"]))
        summary = self.application.dispatch(
            "task/read",
            {"taskId": started["id"]},
        )
        self.assertEqual(pending.task.state.value, "NEEDS_ATTENTION")
        self.assertTrue(summary["manualConfirmationPending"], summary)

        changes_requested = self.application.dispatch(
            "task/confirm",
            {
                **self._action_params(summary),
                "approved": False,
            },
        )
        self.assertEqual(changes_requested["state"], "READY")
        restarted = self.application.dispatch(
            "task/start",
            self._action_params(changes_requested),
        )
        rerun = self.store.latest_run(str(restarted["id"]))
        assert rerun is not None
        self.adapter.complete(rerun.run_id, final_response="Changes addressed.")
        self.lifecycle.collect(str(restarted["id"]))
        summary = self.application.dispatch(
            "task/read",
            {"taskId": restarted["id"]},
        )
        self.assertTrue(summary["manualConfirmationPending"], summary)

        confirmed = self.application.dispatch(
            "task/confirm",
            {
                **self._action_params(summary),
                "approved": True,
            },
        )
        self.assertEqual(confirmed["state"], "SUCCEEDED")
        report = self.application.dispatch(
            "task/detail",
            {"taskId": started["id"], "section": "report"},
        )
        self.assertEqual(
            report["evidence"]["manual"]["status"],
            "CONFIRMED",
        )

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


class _FakeModel:
    def __init__(self, value: dict):
        self.value = value

    def model_dump(self, **_kwargs: object) -> dict:
        return self.value


class _FakeLoginHandle:
    login_id = "login-1"
    auth_url = "https://auth.example/browser"
    verification_url = "https://auth.example/device"
    user_code = "ABCD-EFGH"

    def __init__(self) -> None:
        self.cancelled = False

    def wait(self) -> _FakeModel:
        return _FakeModel({"success": True})

    def cancel(self) -> None:
        self.cancelled = True


class _FakeCodexClient:
    def __init__(self) -> None:
        self.signed_in = False
        self.api_key_seen: str | None = None
        self.account_calls = 0

    def account(self, *, refresh_token: bool = False) -> _FakeModel:
        del refresh_token
        self.account_calls += 1
        account = (
            {
                "type": "chatgpt",
                "email": "user@example.invalid",
                "planType": "plus",
            }
            if self.signed_in
            else None
        )
        return _FakeModel(
            {"account": account, "requiresOpenaiAuth": True}
        )

    def login_chatgpt(self) -> _FakeLoginHandle:
        self.signed_in = True
        return _FakeLoginHandle()

    def login_chatgpt_device_code(self) -> _FakeLoginHandle:
        self.signed_in = True
        return _FakeLoginHandle()

    def login_api_key(self, api_key: str) -> None:
        self.api_key_seen = api_key
        self.signed_in = True

    def logout(self) -> None:
        self.signed_in = False


class DesktopAccountStageNineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _FakeCodexClient()
        self.accounts = DesktopAccountService(self.client)

    def test_api_key_is_forwarded_without_being_returned(self) -> None:
        result = self.accounts.start_login(
            {"type": "apiKey", "apiKey": "temporary-test-key"}
        )
        self.assertEqual(self.client.api_key_seen, "temporary-test-key")
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertNotIn("apiKey", result)
        self.assertTrue(result["account"]["signedIn"])

    def test_browser_and_device_login_return_only_safe_login_metadata(self) -> None:
        browser = self.accounts.start_login({"type": "chatgpt"})
        device = self.accounts.start_login({"type": "chatgptDeviceCode"})
        self.assertEqual(browser["authorizationUrl"], _FakeLoginHandle.auth_url)
        self.assertEqual(device["userCode"], _FakeLoginHandle.user_code)
        self.assertNotIn("handle", browser)
        self.assertNotIn("handle", device)

    def test_logout_returns_signed_out_summary(self) -> None:
        self.client.signed_in = True
        result = self.accounts.logout()
        self.assertFalse(result["signedIn"])

    def test_repeated_snapshot_reads_use_the_cached_account(self) -> None:
        first = self.accounts.read_account()
        second = self.accounts.read_account()

        self.assertEqual(first, second)
        self.assertEqual(self.client.account_calls, 1)

        self.accounts.read_account(refresh=True)
        self.assertEqual(self.client.account_calls, 2)


if __name__ == "__main__":
    unittest.main()
