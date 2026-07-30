from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_orchestrator.adapters.trusted_events import GitHubEventAdapter
from agent_orchestrator.cli import main
from agent_orchestrator.external_events import (
    HmacSha256Authenticator,
    TrustedEventService,
)
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database = root / "state.db"
        self.workspace = root / "workspace"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def call(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(
                [
                    "--db",
                    str(self.database),
                    "--json",
                    *arguments,
                ]
            )
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_create_ready_show_and_verify_audit(self) -> None:
        exit_code, stdout, stderr = self.call(
            "task",
            "create",
            "--title",
            "CLI task",
            "--objective",
            "Verify separate CLI calls share durable state",
            "--workspace",
            str(self.workspace),
            "--ready",
        )
        self.assertEqual(exit_code, 0, stderr)
        created = json.loads(stdout)
        self.assertEqual(created["state"], "READY")
        task_id = created["task_id"]

        exit_code, stdout, stderr = self.call("task", "show", task_id)
        self.assertEqual(exit_code, 0, stderr)
        shown = json.loads(stdout)
        self.assertEqual(shown["version"], 1)

        exit_code, stdout, stderr = self.call("audit", "verify", task_id)
        self.assertEqual(exit_code, 0, stderr)
        self.assertTrue(json.loads(stdout)["valid"])

    def test_rejected_event_returns_nonzero_and_is_queryable(self) -> None:
        exit_code, stdout, stderr = self.call(
            "task",
            "create",
            "--title",
            "Draft task",
            "--objective",
            "Remain draft",
            "--workspace",
            str(self.workspace),
        )
        self.assertEqual(exit_code, 0, stderr)
        task_id = json.loads(stdout)["task_id"]

        exit_code, stdout, stderr = self.call(
            "event",
            "emit",
            task_id,
            "RUN_REQUESTED",
            "--dedupe-key",
            "illegal-run",
        )
        self.assertEqual(exit_code, 2, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["outcome"], "REJECTED")

        exit_code, stdout, stderr = self.call("event", "allowed", task_id)
        self.assertEqual(exit_code, 0, stderr)
        allowed = json.loads(stdout)
        self.assertIn("TASK_VALIDATED", allowed["allowed_events"])

    def test_checkpoint_and_resume_commands(self) -> None:
        exit_code, stdout, stderr = self.call(
            "task",
            "create",
            "--title",
            "Checkpoint task",
            "--objective",
            "Build a CLI resume package",
            "--workspace",
            str(self.workspace),
            "--ready",
        )
        self.assertEqual(exit_code, 0, stderr)
        task_id = json.loads(stdout)["task_id"]
        checkpoint_root = Path(self.temporary_directory.name) / "checkpoints"

        exit_code, stdout, stderr = self.call(
            "checkpoint",
            "create",
            task_id,
            "--root",
            str(checkpoint_root),
            "--progress",
            '{"completed":[],"in_progress":[],"pending":["next"]}',
            "--current-block",
            '{"reason":"pause","waiting_for":"resume"}',
            "--next-action",
            '{"description":"continue","risk_level":"low"}',
            "--verification",
            '{"required_checks":[]}',
            "--permissions",
            '{"granted":["read"],"approvals_required":[]}',
        )
        self.assertEqual(exit_code, 0, stderr)
        checkpoint = json.loads(stdout)
        self.assertEqual(checkpoint["status"], "READY")

        exit_code, stdout, stderr = self.call(
            "checkpoint",
            "latest",
            task_id,
            "--verify",
        )
        self.assertEqual(exit_code, 0, stderr)
        self.assertTrue(json.loads(stdout)["verified"])

        exit_code, stdout, stderr = self.call("resume", "build", task_id)
        self.assertEqual(exit_code, 0, stderr)
        package = json.loads(stdout)
        self.assertIn("OUTPUT CONTRACT", package["prompt"])

    def test_verification_run_list_and_report(self) -> None:
        acceptance = json.dumps(
            {
                "checks": [
                    {
                        "name": "cli-acceptance",
                        "command": [sys.executable, "-c", "print('pass')"],
                    }
                ]
            }
        )
        exit_code, stdout, stderr = self.call(
            "task",
            "create",
            "--title",
            "Verified CLI task",
            "--objective",
            "Persist acceptance evidence",
            "--workspace",
            str(self.workspace),
            "--acceptance",
            acceptance,
            "--ready",
        )
        self.assertEqual(exit_code, 0, stderr)
        task_id = json.loads(stdout)["task_id"]
        for event_type, key in (
            ("RUN_REQUESTED", "cli-run"),
            ("PHASE_COMPLETED", "cli-phase"),
        ):
            exit_code, _, stderr = self.call(
                "event",
                "emit",
                task_id,
                event_type,
                "--dedupe-key",
                key,
            )
            self.assertEqual(exit_code, 0, stderr)

        exit_code, stdout, stderr = self.call("verify", "run", task_id)
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(
            json.loads(stdout)["transition"]["current_state"],
            "SUCCEEDED",
        )
        exit_code, stdout, stderr = self.call("verify", "list", task_id)
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)[0]["status"], "PASSED")
        exit_code, stdout, stderr = self.call("verify", "report", task_id)
        self.assertEqual(exit_code, 0, stderr)
        self.assertTrue(Path(json.loads(stdout)["report_path"]).is_file())

    def test_hash_bound_approval_cli_flow(self) -> None:
        exit_code, stdout, stderr = self.call(
            "task",
            "create",
            "--title",
            "Approval task",
            "--objective",
            "Prove a deployment requires approval",
            "--workspace",
            str(self.workspace),
            "--permissions",
            '{"deployment":{"production":"ask"}}',
            "--ready",
        )
        self.assertEqual(exit_code, 0, stderr)
        task_id = json.loads(stdout)["task_id"]
        exit_code, _, stderr = self.call(
            "event",
            "emit",
            task_id,
            "RUN_REQUESTED",
            "--dedupe-key",
            "approval-cli-run",
        )
        self.assertEqual(exit_code, 0, stderr)
        exit_code, stdout, stderr = self.call(
            "approval",
            "request",
            task_id,
            "deployment.production",
            "--logical-step",
            "release",
            "--parameters",
            '{"version":"1.0.0"}',
            "--risk",
            "Changes production",
            "--rollback",
            "Deploy previous version",
        )
        self.assertEqual(exit_code, 0, stderr)
        requested = json.loads(stdout)
        approval = requested["approval"]
        self.assertEqual(requested["decision"], "ask")
        exit_code, stdout, stderr = self.call(
            "approval",
            "approve",
            approval["approval_id"],
            "--action-hash",
            approval["action_hash"],
            "--by",
            "cli-reviewer",
        )
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "APPROVED")
        exit_code, stdout, stderr = self.call(
            "task",
            "show",
            task_id,
        )
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["state"], "READY")

    def test_register_and_list_signal_wait(self) -> None:
        exit_code, stdout, stderr = self.call(
            "task",
            "create",
            "--title",
            "Signal task",
            "--objective",
            "Wait for healthy service metadata",
            "--workspace",
            str(self.workspace),
            "--ready",
        )
        self.assertEqual(exit_code, 0, stderr)
        task_id = json.loads(stdout)["task_id"]
        exit_code, _, stderr = self.call(
            "event",
            "emit",
            task_id,
            "RUN_REQUESTED",
            "--dedupe-key",
            "signal-cli-run",
        )
        self.assertEqual(exit_code, 0, stderr)
        exit_code, stdout, stderr = self.call(
            "wait",
            "register",
            task_id,
            "--provider",
            "health-probe",
            "--kind",
            "service.health",
            "--subject",
            "api",
            "--condition",
            '{"status":"healthy"}',
            "--timeout-seconds",
            "300",
        )
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "ACTIVE")
        exit_code, stdout, stderr = self.call("wait", "list", task_id)
        self.assertEqual(exit_code, 0, stderr)
        waits = json.loads(stdout)
        self.assertEqual(waits[0]["condition"], {"status": "healthy"})
        self.assertEqual(waits[0]["kind"], "service.health")

    def test_worker_tick_and_serve_secret_boundary(self) -> None:
        exit_code, stdout, stderr = self.call("worker", "tick")
        self.assertEqual(exit_code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["expired_waits"], 0)
        self.assertEqual(result["recovered_side_effects"], 0)

        with patch.dict(
            os.environ,
            {"ORCHESTRATOR_GITHUB_WEBHOOK_SECRET": ""},
        ):
            exit_code, _, stderr = self.call("serve", "--port", "0")
        self.assertEqual(exit_code, 1)
        self.assertIn("is not set", stderr)

    def test_real_ci_demo_preparation_and_evidence(self) -> None:
        exit_code, stdout, stderr = self.call(
            "demo",
            "prepare-ci",
            "--repository",
            "octo/example",
            "--workflow",
            "CI",
            "--branch",
            "demo-branch",
            "--workspace",
            str(self.workspace),
            "--timeout-seconds",
            "300",
        )
        self.assertEqual(exit_code, 0, stderr)
        prepared = json.loads(stdout)
        self.assertEqual(prepared["state"], "WAITING_FOR_SIGNAL")
        self.assertEqual(
            prepared["subject"],
            "octo/example#workflow:CI#branch:demo-branch",
        )

        exit_code, stdout, stderr = self.call(
            "demo",
            "verify-ci",
            prepared["task_id"],
        )
        self.assertEqual(exit_code, 2, stderr)
        self.assertFalse(json.loads(stdout)["passed"])

        secret = b"ci-demo-secret"
        body = json.dumps(
            {
                "action": "completed",
                "repository": {"full_name": "octo/example"},
                "sender": {"login": "github-actions"},
                "workflow_run": {
                    "id": 8675309,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "demo-branch",
                },
            }
        ).encode("utf-8")
        store = SQLiteStore(self.database)
        service = OrchestratorService(store)
        service.initialize()
        TrustedEventService(store=store, service=service).route_webhook(
            adapter=GitHubEventAdapter("workflow_run"),
            body=body,
            delivery_id="real-ci-demo-delivery",
            signature=HmacSha256Authenticator.sign(body, secret),
            secret=secret,
        )

        exit_code, stdout, stderr = self.call(
            "demo",
            "verify-ci",
            prepared["task_id"],
        )
        self.assertEqual(exit_code, 0, stderr)
        evidence = json.loads(stdout)
        self.assertTrue(evidence["passed"])
        self.assertEqual(evidence["task_state"], "READY")
        self.assertEqual(evidence["wait_status"], "SATISFIED")
        self.assertEqual(evidence["event_status"], "CONSUMED")
        self.assertTrue(evidence["event_authenticated"])
        self.assertTrue(evidence["audit_chain_valid"])


if __name__ == "__main__":
    unittest.main()
