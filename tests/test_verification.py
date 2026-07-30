from __future__ import annotations

import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from agent_orchestrator.adapters.fake import FakeExecutionAdapter
from agent_orchestrator.errors import ValidationError
from agent_orchestrator.execution import ExecutionCoordinator
from agent_orchestrator.models import Event, EventType, TaskState
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore, utc_now
from agent_orchestrator.verification import (
    ConstrainedCommandExecutor,
    ExecutionRepairAction,
    LimitedRepairLoop,
    VerificationCheck,
    VerificationCoordinator,
    VerificationPolicy,
)


class RepairingFakeAdapter(FakeExecutionAdapter):
    def start(self, request):
        handle = super().start(request)
        Path(request.workspace_path, "fixed-by-adapter.txt").write_text(
            "fixed",
            encoding="utf-8",
        )
        self.complete(request.run_id, final_response="repair completed")
        return handle


class VerificationPolicyTests(unittest.TestCase):
    def test_policy_parses_argument_array_and_retry_budget(self) -> None:
        policy = VerificationPolicy.parse(
            {
                "checks": [
                    {
                        "name": "unit",
                        "command": [sys.executable, "-m", "unittest"],
                        "timeout_seconds": 30,
                    }
                ]
            },
            {"max_verification_repairs": 1},
        )
        self.assertEqual(policy.checks[0].command[1:], ("-m", "unittest"))
        self.assertEqual(policy.max_repair_attempts, 1)

    def test_policy_rejects_empty_and_duplicate_checks(self) -> None:
        with self.assertRaises(ValidationError):
            VerificationPolicy.parse({})
        with self.assertRaises(ValidationError):
            VerificationPolicy.parse(
                {
                    "checks": [
                        {"name": "same", "command": ["one"]},
                        {"name": "same", "command": ["two"]},
                    ]
                }
            )

    def test_policy_rejects_credentials_in_command_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            VerificationPolicy.parse(
                {
                    "checks": [
                        {
                            "name": "unsafe",
                            "command": ["tool", "--token", "do-not-store"],
                        }
                    ]
                }
            )


class CommandExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)
        self.executor = ConstrainedCommandExecutor(
            log_root=self.workspace / "logs"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_check(self, check: VerificationCheck):
        return self.executor.run(
            task_id="task_test",
            attempt=1,
            check=check,
            workspace_path=self.workspace,
        )

    def test_captures_pass_failure_and_complete_logs(self) -> None:
        passed = self.run_check(
            VerificationCheck(
                name="pass",
                command=(sys.executable, "-c", "print('accepted')"),
            )
        )
        failed = self.run_check(
            VerificationCheck(
                name="fail",
                command=(
                    sys.executable,
                    "-c",
                    "import sys; print('bad', file=sys.stderr); sys.exit(7)",
                ),
            )
        )
        self.assertEqual(passed.status, "PASSED")
        self.assertEqual(failed.status, "FAILED")
        self.assertEqual(failed.exit_code, 7)
        self.assertIn("bad", Path(failed.log_path).read_text(encoding="utf-8"))

    def test_timeout_and_output_truncation_are_explicit(self) -> None:
        timed_out = self.run_check(
            VerificationCheck(
                name="timeout",
                command=(sys.executable, "-c", "import time; time.sleep(5)"),
                timeout_seconds=1,
            )
        )
        truncated = self.run_check(
            VerificationCheck(
                name="large",
                command=(sys.executable, "-c", "print('x' * 2000)"),
                max_output_chars=256,
            )
        )
        self.assertTrue(timed_out.timed_out)
        self.assertEqual(timed_out.status, "FAILED")
        self.assertTrue(truncated.output_truncated)
        self.assertGreater(
            len(Path(truncated.log_path).read_text(encoding="utf-8")),
            len(truncated.summary),
        )

    def test_logs_are_redacted_before_persistence(self) -> None:
        result = self.run_check(
            VerificationCheck(
                name="redacted",
                command=(
                    sys.executable,
                    "-c",
                    "print('token=' + 'credential-' + 'value-12345')",
                ),
            )
        )
        log = Path(result.log_path).read_text(encoding="utf-8")
        self.assertNotIn("credential-value-12345", log)
        self.assertIn("[REDACTED]", log)

    def test_timeout_terminates_descendant_processes(self) -> None:
        marker = self.workspace / "descendant-survived.txt"
        child = (
            "import time; from pathlib import Path; "
            "time.sleep(2); "
            f"Path({str(marker)!r}).write_text('survived', encoding='utf-8')"
        )
        parent = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "time.sleep(10)"
        )
        result = self.run_check(
            VerificationCheck(
                name="tree-timeout",
                command=(sys.executable, "-c", parent),
                timeout_seconds=1,
            )
        )
        self.assertTrue(result.timed_out)
        time.sleep(2.5)
        self.assertFalse(marker.exists())


class VerificationCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = SQLiteStore(self.root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def event(self, task_id: str, event_type: EventType, suffix: str) -> None:
        task = self.store.get_task(task_id)
        result = self.service.process_event(
            Event(
                event_id=f"evt_{uuid.uuid4().hex}",
                task_id=task_id,
                event_type=event_type,
                source="test",
                dedupe_key=f"{task_id}:{suffix}:{task.version}",
                occurred_at=utc_now(),
                expected_version=task.version,
            )
        )
        self.assertEqual(result.outcome, "APPLIED")

    def create_verifying_task(
        self,
        command: tuple[str, ...],
        *,
        required: bool = True,
        max_repairs: int = 1,
    ):
        task = self.service.create_task(
            title="Verify me",
            objective="Only finish with evidence",
            workspace_path=self.workspace,
            acceptance_policy={
                "checks": [
                    {
                        "name": "acceptance",
                        "command": list(command),
                        "required": required,
                        "timeout_seconds": 5,
                    }
                ],
                "max_repair_attempts": max_repairs,
            },
        )
        self.service.validate_task(task.task_id)
        self.event(task.task_id, EventType.RUN_REQUESTED, "run")
        self.event(task.task_id, EventType.PHASE_COMPLETED, "phase")
        return self.store.get_task(task.task_id)

    def verifier(self) -> VerificationCoordinator:
        return VerificationCoordinator(
            store=self.store,
            service=self.service,
            executor=ConstrainedCommandExecutor(log_root=self.root / "logs"),
        )

    def test_passed_required_check_is_persisted_before_success(self) -> None:
        task = self.create_verifying_task(
            (sys.executable, "-c", "print('ok')"),
        )
        suite = self.verifier().verify(task.task_id)
        self.assertTrue(suite.required_passed)
        self.assertEqual(suite.transition.current_state, TaskState.SUCCEEDED)
        self.assertTrue(Path(suite.report_path or "").is_file())
        records = self.store.list_verifications(task.task_id)
        self.assertEqual(records[0].status, "PASSED")
        self.assertTrue(self.store.verify_audit_chain(task.task_id))

    def test_raw_command_output_never_enters_sqlite(self) -> None:
        sentinel = "TOP_SECRET_SENTINEL_VALUE"
        task = self.create_verifying_task(
            (
                sys.executable,
                "-c",
                "print('TOP_' + 'SECRET_' + 'SENTINEL_' + 'VALUE')",
            ),
        )
        suite = self.verifier().verify(task.task_id)
        self.assertIn(sentinel, Path(suite.records[0].log_path).read_text("utf-8"))
        self.assertNotIn(sentinel, suite.records[0].summary)
        self.assertNotIn(
            sentinel.encode("utf-8"),
            (self.root / "state.db").read_bytes(),
        )

    def test_failure_retries_then_escalates_without_false_success(self) -> None:
        task = self.create_verifying_task(
            (sys.executable, "-c", "raise SystemExit(1)"),
            max_repairs=1,
        )
        first = self.verifier().verify(task.task_id)
        self.assertEqual(first.transition.current_state, TaskState.READY)
        self.event(task.task_id, EventType.RUN_REQUESTED, "repair-run")
        self.event(task.task_id, EventType.PHASE_COMPLETED, "repair-phase")
        second = self.verifier().verify(task.task_id)
        self.assertEqual(
            second.transition.current_state,
            TaskState.NEEDS_ATTENTION,
        )
        self.assertFalse(second.required_passed)
        self.assertEqual(
            [record.attempt for record in self.store.list_verifications(task.task_id)],
            [1, 2],
        )

    def test_optional_failure_does_not_block_success(self) -> None:
        task = self.create_verifying_task(
            (sys.executable, "-c", "raise SystemExit(1)"),
            required=False,
        )
        suite = self.verifier().verify(task.task_id)
        self.assertEqual(suite.transition.current_state, TaskState.SUCCEEDED)
        self.assertEqual(suite.records[0].status, "FAILED")

    def test_limited_loop_repairs_then_rechecks(self) -> None:
        target = self.workspace / "fixed.txt"
        task = self.create_verifying_task(
            (
                sys.executable,
                "-c",
                "from pathlib import Path; raise SystemExit(not Path('fixed.txt').exists())",
            ),
            max_repairs=1,
        )

        def repair(current, suite) -> None:
            self.assertEqual(current.state, TaskState.READY)
            self.assertFalse(suite.required_passed)
            target.write_text("fixed", encoding="utf-8")
            self.event(current.task_id, EventType.RUN_REQUESTED, "auto-repair")
            self.event(current.task_id, EventType.PHASE_COMPLETED, "auto-repair-done")

        result = LimitedRepairLoop(
            store=self.store,
            verifier=self.verifier(),
            repair_action=repair,
        ).run(task.task_id)
        self.assertEqual(result.attempt, 2)
        self.assertEqual(result.transition.current_state, TaskState.SUCCEEDED)

    def test_execution_repair_action_uses_durable_run_path(self) -> None:
        task = self.create_verifying_task(
            (
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "raise SystemExit(not Path('fixed-by-adapter.txt').exists())",
            ),
            max_repairs=1,
        )
        execution = ExecutionCoordinator(
            store=self.store,
            service=self.service,
            adapter=RepairingFakeAdapter(),
            owner="verification-test",
        )
        result = LimitedRepairLoop(
            store=self.store,
            verifier=self.verifier(),
            repair_action=ExecutionRepairAction(execution),
        ).run(task.task_id)
        self.assertEqual(result.transition.current_state, TaskState.SUCCEEDED)
        run_finished = [
            entry
            for entry in self.store.list_audit(task_id=task.task_id)
            if entry.kind == "RUN_FINISHED"
        ]
        self.assertEqual(len(run_finished), 1)


if __name__ == "__main__":
    unittest.main()
