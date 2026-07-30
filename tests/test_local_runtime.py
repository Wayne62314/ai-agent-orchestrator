from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from deploy.local_runtime import (
    backup_database,
    restore_database,
    verify_database,
)


class LocalRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "data" / "state.db"
        self.database.parent.mkdir()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker VALUES ('before')")
            connection.commit()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def read_marker(self, path: Path) -> str:
        with closing(sqlite3.connect(path)) as connection:
            row = connection.execute("SELECT value FROM marker").fetchone()
        assert row is not None
        return str(row[0])

    def test_online_backup_is_valid_and_retention_is_enforced(self) -> None:
        backup_directory = self.root / "backups"
        first = backup_database(self.database, backup_directory, keep=1)
        self.assertEqual(self.read_marker(first), "before")

        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE marker SET value = 'after'")
            connection.commit()
        second = backup_database(self.database, backup_directory, keep=1)

        self.assertNotEqual(first, second)
        self.assertFalse(first.exists())
        self.assertEqual(self.read_marker(second), "after")
        verify_database(second)

    def test_restore_requires_confirmation_and_stopped_service(self) -> None:
        backup = backup_database(self.database, self.root / "backups", keep=3)
        pid_file = self.root / "orchestrator.pid"

        with self.assertRaisesRegex(ValueError, "confirm-replace"):
            restore_database(
                backup,
                self.database,
                pid_file=pid_file,
                confirm_replace=False,
            )

        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Stop the local orchestrator"):
            restore_database(
                backup,
                self.database,
                pid_file=pid_file,
                confirm_replace=True,
            )

    def test_restore_creates_safety_backup_and_replaces_database(self) -> None:
        backup = backup_database(self.database, self.root / "backups", keep=3)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE marker SET value = 'changed'")
            connection.commit()

        with mock.patch("deploy.local_runtime.process_is_running", return_value=False):
            safety = restore_database(
                backup,
                self.database,
                pid_file=self.root / "missing.pid",
                confirm_replace=True,
            )

        self.assertIsNotNone(safety)
        assert safety is not None
        self.assertEqual(self.read_marker(safety), "changed")
        self.assertEqual(self.read_marker(self.database), "before")

    def test_verify_rejects_missing_or_corrupt_database(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            verify_database(self.root / "missing.db")
        corrupt = self.root / "corrupt.db"
        corrupt.write_text("not sqlite", encoding="utf-8")
        with self.assertRaises(sqlite3.DatabaseError):
            verify_database(corrupt)


if __name__ == "__main__":
    unittest.main()
