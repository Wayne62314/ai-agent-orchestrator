from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_orchestrator.desktop_rpc import DesktopMaintenanceService
from agent_orchestrator.errors import NotFoundError, ValidationError
from agent_orchestrator.service import OrchestratorService
from agent_orchestrator.store import SQLiteStore


class DesktopMaintenanceStageNineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = SQLiteStore(self.root / "state.db")
        self.service = OrchestratorService(self.store)
        self.service.initialize()
        self.first = self.service.create_task(
            title="Before backup",
            objective="Prove desktop backup and restore.",
            workspace_path=self.root / "repository",
            permissions_policy={},
            acceptance_policy={"checks": []},
            task_id="task-before-backup",
        )
        self.maintenance = DesktopMaintenanceService(
            store=self.store,
            data_root=self.root,
            background_reader=lambda: {"running": False},
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_backup_restore_round_trip_uses_only_registered_backup_id(self) -> None:
        created = self.maintenance.create_backup({})
        backup_id = created["createdBackupId"]
        self.assertTrue(created["restoreAvailable"])
        self.service.create_task(
            title="After backup",
            objective="This task must disappear after restore.",
            workspace_path=self.root / "repository",
            permissions_policy={},
            acceptance_policy={"checks": []},
            task_id="task-after-backup",
        )

        restored = self.maintenance.restore_backup(
            {
                "backupId": backup_id,
                "confirmation": "RESTORE_BACKUP",
            }
        )
        self.assertTrue(restored["restartRecommended"])
        self.assertTrue(restored["safetyBackupCreated"])
        self.assertEqual(
            self.store.list_audit(task_id=None)[-1].kind,
            "DESKTOP_BACKUP_RESTORED",
        )
        self.assertEqual(self.store.get_task(self.first.task_id).title, "Before backup")
        with self.assertRaises(NotFoundError):
            self.store.get_task("task-after-backup")

        with self.assertRaises(ValidationError):
            self.maintenance.restore_backup(
                {
                    "backupId": "../state.db",
                    "confirmation": "RESTORE_BACKUP",
                }
            )

    def test_restore_requires_exact_confirmation_and_no_active_task(self) -> None:
        backup_id = self.maintenance.create_backup({})["createdBackupId"]
        with self.assertRaisesRegex(ValidationError, "exact"):
            self.maintenance.restore_backup(
                {"backupId": backup_id, "confirmation": "yes"}
            )
        self.store.acquire_active_task(
            task_id=self.first.task_id,
            owner="maintenance-test",
            lease_seconds=60,
        )
        with self.assertRaisesRegex(ValidationError, "active task"):
            self.maintenance.restore_backup(
                {
                    "backupId": backup_id,
                    "confirmation": "RESTORE_BACKUP",
                }
            )

    def test_diagnostic_bundle_excludes_database_and_payloads(self) -> None:
        self.store.append_audit(
            task_id=self.first.task_id,
            run_id=None,
            kind="DIAGNOSTIC_TEST",
            payload={"private": "must-not-enter-diagnostics"},
        )
        exported = self.maintenance.export_diagnostics({})
        path = Path(exported["path"])
        self.assertTrue(path.is_file())
        self.assertFalse(exported["containsSensitiveData"])
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"README.txt", "diagnostics.json"},
            )
            raw = archive.read("diagnostics.json").decode("utf-8")
        self.assertNotIn("must-not-enter-diagnostics", raw)
        self.assertNotIn(self.first.objective, raw)
        summary = json.loads(raw)
        self.assertEqual(summary["database"]["integrity"], "ok")
        self.assertEqual(summary["tasks"]["total"], 1)
        self.assertEqual(
            self.store.list_audit(task_id=None)[-1].kind,
            "DESKTOP_DIAGNOSTICS_EXPORTED",
        )


if __name__ == "__main__":
    unittest.main()
