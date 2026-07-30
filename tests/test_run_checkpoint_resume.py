from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_orchestrator.checkpoint import CheckpointService
from agent_orchestrator.errors import ConcurrencyError, ValidationError
from agent_orchestrator.models import RunState
from agent_orchestrator.resume import ResumePackageBuilder
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore
from agent_orchestrator.workspace import (
    DriftKind,
    DriftReport,
    WorkspaceSnapshot,
)


class RunCheckpointResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = SQLiteStore(self.root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        self.task = self.service.create_task(
            title="Stage two task",
            objective="Verify durable runs and checkpoints",
            workspace_path=self.workspace,
            acceptance_policy={"commands": ["tests"]},
            permissions_policy={"git": {"push": "deny"}},
        )
        self.service.validate_task(self.task.task_id)
        self.task = self.store.get_task(self.task.task_id)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            path=str(self.workspace.resolve()),
            observed_at=datetime.now(UTC).isoformat(),
            vcs_type="git",
            branch="main",
            head="abc123",
            files={"app.py": {"status": " M", "hash": "hash-one"}},
            fingerprint="fingerprint-one",
        )

    def test_run_lease_bind_heartbeat_and_finish(self) -> None:
        run = self.store.create_run(
            run_id="run_lease",
            task_id=self.task.task_id,
            engine="fake",
            lease_owner="worker-1",
            lease_seconds=60,
        )
        self.assertEqual(run.state, RunState.STARTING)
        bound = self.store.bind_run(
            run_id=run.run_id,
            lease_owner="worker-1",
            provider_run_id="provider-1",
            thread_id="thread-1",
            lease_seconds=60,
        )
        self.assertEqual(bound.state, RunState.RUNNING)
        heartbeat = self.store.heartbeat_run(
            run_id=run.run_id,
            lease_owner="worker-1",
            lease_seconds=120,
        )
        self.assertGreater(
            heartbeat.lease_expires_at or "",
            bound.lease_expires_at or "",
        )
        finished = self.store.finish_run(
            run_id=run.run_id,
            lease_owner="worker-1",
            state=RunState.COMPLETED,
            result_summary="done",
        )
        self.assertEqual(finished.state, RunState.COMPLETED)
        self.assertIsNone(finished.lease_expires_at)

    def test_wrong_lease_owner_cannot_heartbeat(self) -> None:
        run = self.store.create_run(
            run_id="run_owner",
            task_id=self.task.task_id,
            engine="fake",
            lease_owner="worker-1",
            lease_seconds=60,
        )
        with self.assertRaises(ConcurrencyError):
            self.store.heartbeat_run(
                run_id=run.run_id,
                lease_owner="worker-2",
                lease_seconds=60,
            )

    def test_expired_run_is_abandoned(self) -> None:
        run = self.store.create_run(
            run_id="run_expired",
            task_id=self.task.task_id,
            engine="fake",
            lease_owner="worker-1",
            lease_seconds=1,
        )
        future = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        expired = self.store.list_expired_runs(observed_at=future)
        self.assertEqual([item.run_id for item in expired], [run.run_id])
        abandoned = self.store.abandon_expired_run(
            run_id=run.run_id,
            observed_at=future,
        )
        self.assertEqual(abandoned.state, RunState.ABANDONED)

    def test_checkpoint_round_trip_and_hash_verification(self) -> None:
        checkpoints = CheckpointService(
            self.store,
            self.root / "checkpoints",
        )
        record = checkpoints.create(
            task=self.task,
            run_id=None,
            workspace=self.snapshot(),
            progress={
                "completed": [{"item": "model", "evidence": "test"}],
                "in_progress": [],
                "pending": ["handler"],
                "failed_attempts": [],
            },
            decisions=[],
            current_block={
                "reason": "rate_limit",
                "waiting_for": "reset",
            },
            next_action={
                "description": "implement handler",
                "expected_result": "tests pass",
                "risk_level": "low",
            },
            verification={"last_results": [], "required_checks": ["tests"]},
            permissions={"granted": ["read"], "approvals_required": []},
            relevant_files=["app.py"],
        )
        self.assertEqual(record.status, "READY")
        payload = checkpoints.load(record)
        self.assertEqual(payload["task"]["id"], self.task.task_id)
        self.assertEqual(
            payload["provenance"]["payload_hash"],
            record.payload_hash,
        )
        self.assertEqual(
            self.store.latest_checkpoint(self.task.task_id).checkpoint_id,
            record.checkpoint_id,
        )

        path = Path(record.payload_path)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["next_action"]["description"] = "tampered"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValidationError):
            checkpoints.load(record)

    def test_resume_package_allows_no_drift(self) -> None:
        checkpoints = CheckpointService(
            self.store,
            self.root / "checkpoints",
        )
        baseline = self.snapshot()
        record = checkpoints.create(
            task=self.task,
            run_id=None,
            workspace=baseline,
            progress={
                "completed": [],
                "in_progress": [],
                "pending": ["next"],
                "failed_attempts": [],
            },
            decisions=[],
            current_block={"reason": "pause", "waiting_for": "resume"},
            next_action={"description": "continue", "risk_level": "low"},
            verification={"required_checks": []},
            permissions={"granted": ["read"], "approvals_required": []},
            relevant_files=["app.py"],
        )
        payload = checkpoints.load(record)
        drift = DriftReport(
            kind=DriftKind.NONE,
            changed_paths=(),
            conflicting_paths=(),
            summary="Workspace matches the checkpoint.",
        )
        package = ResumePackageBuilder().build(
            task=self.task,
            checkpoint=record,
            payload=payload,
            current_workspace=baseline,
            drift=drift,
            thread_id="thread-1",
        )
        self.assertIn(self.task.task_id, package.prompt)
        self.assertIn("OUTPUT CONTRACT", package.prompt)
        self.assertEqual(package.thread_id, "thread-1")

    def test_resume_package_blocks_conflicting_drift(self) -> None:
        record = self.store.reserve_checkpoint(
            checkpoint_id=f"checkpoint_{uuid.uuid4().hex}",
            task_id=self.task.task_id,
            run_id=None,
            schema_version=1,
            workspace_revision="abc123",
            payload_path=str(self.root / "unused.json"),
        )
        drift = DriftReport(
            kind=DriftKind.CONFLICT,
            changed_paths=("app.py",),
            conflicting_paths=("app.py",),
            summary="Conflict",
        )
        with self.assertRaises(ValidationError):
            ResumePackageBuilder().build(
                task=self.task,
                checkpoint=record,
                payload={},
                current_workspace=self.snapshot(),
                drift=drift,
                thread_id=None,
            )


if __name__ == "__main__":
    unittest.main()

