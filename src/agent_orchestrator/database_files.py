"""Verified SQLite file backup primitives."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def verify_database(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Database does not exist: {path}")
    with closing(
        sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise ValueError(f"Database integrity check failed: {path}")


def backup_database(
    source: Path,
    destination_directory: Path,
    keep: int,
    *,
    name_prefix: str = "state",
) -> Path:
    if keep < 1:
        raise ValueError("Backup retention must be at least one.")
    if (
        not name_prefix
        or any(
            not (character.isalnum() or character == "-")
            for character in name_prefix
        )
    ):
        raise ValueError("Backup name prefix contains unsupported characters.")
    verify_database(source)
    destination_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_directory / f"{name_prefix}-{timestamp}.db"
    temporary = destination.with_suffix(".db.tmp")
    try:
        with (
            closing(
                sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            ) as source_connection,
            closing(sqlite3.connect(temporary)) as destination_connection,
        ):
            source_connection.backup(destination_connection)
        verify_database(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    backups = sorted(
        destination_directory.glob(f"{name_prefix}-*.db"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for expired in backups[keep:]:
        expired.unlink()
        expired.with_suffix(".manifest.json").unlink(missing_ok=True)
    return destination
