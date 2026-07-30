from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from agent_orchestrator.errors import ValidationError
from agent_orchestrator.models import Event, EventType, TaskState
from agent_orchestrator.schema import MIGRATIONS
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore, utc_now


class StoreServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database = root / "state.db"
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.store = SQLiteStore(self.database)
        self.service = OrchestratorService(self.store)
        self.service.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_task(self, *, task_id: str | None = None):
        return self.service.create_task(
            title="Test task",
            objective="Exercise the stage-one core",
            workspace_path=self.workspace,
            permissions_policy={"filesystem": {"write": "workspace"}},
            acceptance_policy={"commands": ["python -m unittest"]},
            retry_policy={"max_attempts": 2},
            task_id=task_id,
        )

    def event(
        self,
        task_id: str,
        event_type: EventType,
        *,
        dedupe_key: str,
        expected_version: int | None = None,
        event_id: str | None = None,
    ) -> Event:
        return Event(
            event_id=event_id or f"evt_{uuid.uuid4().hex}",
            task_id=task_id,
            event_type=event_type,
            source="test",
            dedupe_key=dedupe_key,
            occurred_at=utc_now(),
            expected_version=expected_version,
        )

    def test_initialize_is_idempotent(self) -> None:
        self.service.initialize()
        self.service.initialize()
        connection = sqlite3.connect(self.database)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, len(MIGRATIONS))

    def test_existing_stage_one_database_migrates_forward(self) -> None:
        migrated_database = (
            Path(self.temporary_directory.name) / "stage-one.db"
        )
        connection = sqlite3.connect(migrated_database)
        try:
            connection.executescript(MIGRATIONS[0])
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                (utc_now(),),
            )
            connection.commit()
        finally:
            connection.close()

        upgraded = SQLiteStore(migrated_database)
        upgraded.initialize()
        connection = sqlite3.connect(migrated_database)
        try:
            run_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            verification_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(verifications)"
                ).fetchall()
            }
            approval_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(approvals)"
                ).fetchall()
            }
            side_effect_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'side_effects'
                """
            ).fetchone()
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        finally:
            connection.close()
        self.assertIn("lease_expires_at", run_columns)
        self.assertIn("timed_out", verification_columns)
        self.assertIn("attempt", verification_columns)
        self.assertIn("parameters_json", approval_columns)
        self.assertIsNotNone(side_effect_table)
        self.assertEqual(
            [row[0] for row in versions],
            list(range(1, len(MIGRATIONS) + 1)),
        )

    def test_task_persists_across_store_instances(self) -> None:
        task = self.create_task()
        self.service.validate_task(task.task_id)

        reopened = SQLiteStore(self.database)
        reopened.initialize()
        loaded = reopened.get_task(task.task_id)

        self.assertEqual(loaded.state, TaskState.READY)
        self.assertEqual(loaded.version, 1)
        self.assertEqual(loaded.permissions_policy["filesystem"]["write"], "workspace")

    def test_duplicate_event_is_applied_only_once(self) -> None:
        task = self.create_task()
        first = self.event(
            task.task_id,
            EventType.TASK_VALIDATED,
            dedupe_key="same-logical-event",
            event_id="evt_fixed",
        )
        result_one = self.service.process_event(first)
        result_two = self.service.process_event(first)
        loaded = self.store.get_task(task.task_id)

        self.assertEqual(result_one.outcome, "APPLIED")
        self.assertTrue(result_two.duplicate)
        self.assertEqual(loaded.state, TaskState.READY)
        self.assertEqual(loaded.version, 1)
        transitions = [
            item
            for item in self.store.list_audit(task_id=task.task_id)
            if item.kind == "STATE_TRANSITIONED"
        ]
        self.assertEqual(len(transitions), 1)

    def test_dedupe_collision_with_different_content_is_rejected(self) -> None:
        task = self.create_task()
        first = self.event(
            task.task_id,
            EventType.TASK_VALIDATED,
            dedupe_key="collision",
        )
        self.service.process_event(first)
        second = self.event(
            task.task_id,
            EventType.CANCEL_REQUESTED,
            dedupe_key="collision",
        )
        with self.assertRaises(ValidationError):
            self.service.process_event(second)

    def test_event_id_reuse_with_different_dedupe_key_is_rejected(self) -> None:
        task = self.create_task()
        first = self.event(
            task.task_id,
            EventType.TASK_VALIDATED,
            dedupe_key="first-key",
            event_id="evt_reused",
        )
        self.service.process_event(first)
        second = self.event(
            task.task_id,
            EventType.TASK_VALIDATED,
            dedupe_key="different-key",
            event_id="evt_reused",
        )
        with self.assertRaises(ValidationError):
            self.service.process_event(second)

    def test_illegal_transition_is_recorded_without_state_change(self) -> None:
        task = self.create_task()
        event = self.event(
            task.task_id,
            EventType.RUN_REQUESTED,
            dedupe_key="run-too-soon",
        )
        result = self.service.process_event(event)

        self.assertEqual(result.outcome, "REJECTED")
        self.assertEqual(result.current_state, TaskState.DRAFT)
        self.assertEqual(self.store.get_task(task.task_id).version, 0)
        stored_event = self.store.get_event(event.event_id)
        self.assertEqual(stored_event["outcome"], "REJECTED")
        self.assertIn("not legal", stored_event["outcome_reason"])

    def test_stale_expected_version_is_rejected(self) -> None:
        task = self.create_task()
        result = self.service.process_event(
            self.event(
                task.task_id,
                EventType.TASK_VALIDATED,
                dedupe_key="stale-version",
                expected_version=99,
            )
        )
        self.assertEqual(result.outcome, "REJECTED")
        self.assertIn("Expected task version", result.reason or "")
        self.assertEqual(self.store.get_task(task.task_id).state, TaskState.DRAFT)

    def test_full_success_lifecycle(self) -> None:
        task = self.create_task()
        for index, event_type in enumerate(
            (
                EventType.TASK_VALIDATED,
                EventType.RUN_REQUESTED,
                EventType.PHASE_COMPLETED,
                EventType.CHECKS_PASSED,
            ),
            start=1,
        ):
            result = self.service.process_event(
                self.event(
                    task.task_id,
                    event_type,
                    dedupe_key=f"lifecycle-{index}",
                    expected_version=index - 1,
                )
            )
            self.assertEqual(result.outcome, "APPLIED")
        loaded = self.store.get_task(task.task_id)
        self.assertEqual(loaded.state, TaskState.SUCCEEDED)
        self.assertEqual(loaded.version, 4)

    def test_audit_chain_verifies_and_detects_tampering(self) -> None:
        task = self.create_task()
        self.service.validate_task(task.task_id)
        self.assertTrue(self.store.verify_audit_chain(task.task_id))

        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                """
                UPDATE audit_log
                SET payload_json = '{"tampered":true}'
                WHERE task_id = ? AND kind = 'STATE_TRANSITIONED'
                """,
                (task.task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertFalse(self.store.verify_audit_chain(task.task_id))

    def test_task_filters(self) -> None:
        draft = self.create_task(task_id="task_draft")
        ready = self.create_task(task_id="task_ready")
        self.service.validate_task(ready.task_id)

        ready_tasks = self.store.list_tasks(states=[TaskState.READY])
        self.assertEqual([task.task_id for task in ready_tasks], [ready.task_id])
        self.assertEqual(self.store.get_task(draft.task_id).state, TaskState.DRAFT)


if __name__ == "__main__":
    unittest.main()
