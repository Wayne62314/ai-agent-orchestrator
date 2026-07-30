from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Sequence

from agent_orchestrator.database_upgrade import initialize_database
from agent_orchestrator.errors import ValidationError
from agent_orchestrator.schema import MIGRATIONS


class DatabaseUpgradeStage10Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_upgrade_creates_verified_manifest_and_preserves_data(self) -> None:
        _seed_database(self.database, MIGRATIONS[:-1])
        before_version = len(MIGRATIONS) - 1

        first = initialize_database(self.database)
        second = initialize_database(self.database)

        self.assertEqual(first.previous_version, before_version)
        self.assertEqual(first.current_version, len(MIGRATIONS))
        self.assertIsNotNone(first.backup_path)
        self.assertIsNone(second.backup_path)
        backup = first.backup_path
        assert backup is not None
        manifest_path = backup.with_suffix(".manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["sourceDatabaseSchema"], before_version)
        self.assertEqual(manifest["targetDatabaseSchema"], len(MIGRATIONS))
        self.assertEqual(manifest["sha256"], _sha256(backup))
        self.assertEqual(
            len(list((self.root / "backups" / "pre-upgrade").glob("*.db"))),
            1,
        )

        with closing(sqlite3.connect(self.database)) as connection:
            app_settings = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'app_settings'"
            ).fetchone()
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertIsNotNone(app_settings)
        self.assertEqual(
            [row[0] for row in versions],
            list(range(1, len(MIGRATIONS) + 1)),
        )

    def test_failed_migration_never_modifies_the_live_database(self) -> None:
        migrations = (
            MIGRATIONS[0],
            "CREATE TABLE partial_change(value TEXT); THIS IS NOT SQL;",
        )
        _seed_database(self.database, migrations[:1])
        original_digest = _sha256(self.database)

        with self.assertRaises(sqlite3.DatabaseError):
            initialize_database(self.database, migrations=migrations)

        self.assertEqual(_sha256(self.database), original_digest)
        with closing(sqlite3.connect(self.database)) as connection:
            partial = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'partial_change'"
            ).fetchone()
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertIsNone(partial)
        self.assertEqual(versions, [(1,)])
        backups = list(
            (self.root / "backups" / "pre-upgrade").glob(
                "pre-upgrade-v1-to-v2-*.db"
            )
        )
        self.assertEqual(len(backups), 1)

    def test_newer_schema_is_rejected_without_touching_the_database(self) -> None:
        _seed_database(self.database, MIGRATIONS)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (?, 'future')",
                (len(MIGRATIONS) + 1,),
            )
            connection.commit()
        original_digest = _sha256(self.database)

        with self.assertRaisesRegex(ValidationError, "newer application"):
            initialize_database(self.database)

        self.assertEqual(_sha256(self.database), original_digest)
        self.assertFalse((self.root / "backups").exists())

    def test_incomplete_history_is_rejected(self) -> None:
        _seed_database(self.database, MIGRATIONS[:1])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (3, 'gap')"
            )
            connection.commit()

        with self.assertRaisesRegex(ValidationError, "incomplete"):
            initialize_database(self.database)


def _seed_database(database: Path, migrations: Sequence[str]) -> None:
    with closing(sqlite3.connect(database)) as connection:
        for version, script in enumerate(migrations, start=1):
            connection.executescript(script)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (?, 'seed')",
                (version,),
            )
        connection.commit()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
