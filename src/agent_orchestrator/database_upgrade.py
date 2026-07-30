"""Fail-closed, backup-first SQLite schema upgrades."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from .database_files import backup_database, verify_database
from .errors import ValidationError
from .schema import MIGRATIONS


@dataclass(frozen=True, slots=True)
class DatabaseUpgradeResult:
    previous_version: int
    current_version: int
    backup_path: Path | None


def initialize_database(
    database_path: Path,
    *,
    backup_directory: Path | None = None,
    migrations: Sequence[str] = MIGRATIONS,
) -> DatabaseUpgradeResult:
    database_path = database_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    target_version = len(migrations)
    existed = database_path.is_file()
    applied = _read_applied_versions(database_path) if existed else []
    previous_version = applied[-1] if applied else 0

    if previous_version > target_version:
        raise ValidationError(
            "The database was created by a newer application version "
            f"(schema {previous_version}; supported through {target_version})."
        )
    if applied != list(range(1, previous_version + 1)):
        raise ValidationError(
            "The database migration history is incomplete or out of order."
        )
    if previous_version == target_version:
        verify_database(database_path)
        return DatabaseUpgradeResult(
            previous_version=previous_version,
            current_version=target_version,
            backup_path=None,
        )

    backup_path: Path | None = None
    if existed and previous_version > 0:
        destination = backup_directory or (
            database_path.parent / "backups" / "pre-upgrade"
        )
        prefix = f"pre-upgrade-v{previous_version}-to-v{target_version}"
        backup_path = backup_database(
            database_path,
            destination,
            keep=5,
            name_prefix=prefix,
        )
        _write_backup_manifest(
            backup_path,
            previous_version=previous_version,
            target_version=target_version,
        )

    working = database_path.with_name(
        f".{database_path.name}.migration-{uuid.uuid4().hex}.tmp"
    )
    try:
        if existed:
            _copy_database(database_path, working)
        _apply_pending_migrations(
            working,
            migrations=migrations,
            previous_version=previous_version,
        )
        verify_database(working)
        _verify_schema_version(working, target_version)
        os.replace(working, database_path)
    finally:
        working.unlink(missing_ok=True)
        working.with_name(f"{working.name}-journal").unlink(missing_ok=True)

    return DatabaseUpgradeResult(
        previous_version=previous_version,
        current_version=target_version,
        backup_path=backup_path,
    )


def _read_applied_versions(database_path: Path) -> list[int]:
    verify_database(database_path)
    with closing(
        sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    ) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "schema_migrations" not in tables:
            if tables:
                raise ValidationError(
                    "The existing database has no recognized migration history."
                )
            return []
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]


def _copy_database(source: Path, destination: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as source_connection,
        closing(sqlite3.connect(destination)) as destination_connection,
    ):
        source_connection.backup(destination_connection)


def _apply_pending_migrations(
    database_path: Path,
    *,
    migrations: Sequence[str],
    previous_version: int,
) -> None:
    with closing(
        sqlite3.connect(database_path, timeout=10, isolation_level=None)
    ) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA busy_timeout = 10000")
        for version, script in enumerate(migrations, start=1):
            if version <= previous_version:
                continue
            applied_at = datetime.now(UTC).isoformat(timespec="microseconds")
            escaped_at = applied_at.replace("'", "''")
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{script}\n"
                    "INSERT INTO schema_migrations(version, applied_at) "
                    f"VALUES ({version}, '{escaped_at}');\n"
                    "COMMIT;\n"
                )
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValidationError(
                "Schema migration produced foreign-key violations."
            )


def _verify_schema_version(database_path: Path, expected: int) -> None:
    versions = _read_applied_versions(database_path)
    if versions != list(range(1, expected + 1)):
        raise ValidationError(
            "Schema migration did not produce the expected version history."
        )


def _write_backup_manifest(
    backup_path: Path,
    *,
    previous_version: int,
    target_version: int,
) -> None:
    digest_builder = hashlib.sha256()
    with backup_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest_builder.update(block)
    digest = digest_builder.hexdigest()
    payload = {
        "schemaVersion": 1,
        "file": backup_path.name,
        "sizeBytes": backup_path.stat().st_size,
        "sha256": digest,
        "sourceDatabaseSchema": previous_version,
        "targetDatabaseSchema": target_version,
        "createdAtUtc": datetime.now(UTC).isoformat(timespec="microseconds"),
    }
    manifest = backup_path.with_suffix(".manifest.json")
    temporary = manifest.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, manifest)
    finally:
        temporary.unlink(missing_ok=True)
