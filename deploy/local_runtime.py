"""Consistent backup and guarded restore helpers for a local SQLite runtime."""

from __future__ import annotations

import argparse
import os
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


def backup_database(source: Path, destination_directory: Path, keep: int) -> Path:
    if keep < 1:
        raise ValueError("Backup retention must be at least one.")
    verify_database(source)
    destination_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_directory / f"state-{timestamp}.db"
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
        destination_directory.glob("state-*.db"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for expired in backups[keep:]:
        expired.unlink()
    return destination


def process_is_running(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def restore_database(
    backup: Path,
    target: Path,
    *,
    pid_file: Path,
    confirm_replace: bool,
) -> Path | None:
    if not confirm_replace:
        raise ValueError("Restore requires --confirm-replace.")
    if process_is_running(pid_file):
        raise ValueError("Stop the local orchestrator before restoring.")
    verify_database(backup)

    safety_backup: Path | None = None
    if target.exists():
        safety_backup = backup_database(
            target,
            target.parent / "pre-restore",
            keep=5,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".restore.tmp")
    try:
        with (
            closing(
                sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
            ) as source_connection,
            closing(sqlite3.connect(temporary)) as destination_connection,
        ):
            source_connection.backup(destination_connection)
        verify_database(temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return safety_backup


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--destination", type=Path, required=True)
    backup.add_argument("--keep", type=int, default=30)

    restore = commands.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--database", type=Path, required=True)
    restore.add_argument("--pid-file", type=Path, required=True)
    restore.add_argument("--confirm-replace", action="store_true")

    verify = commands.add_parser("verify")
    verify.add_argument("--database", type=Path, required=True)

    arguments = parser.parse_args()
    try:
        if arguments.command == "backup":
            result = backup_database(
                arguments.database,
                arguments.destination,
                arguments.keep,
            )
            print(f"Backup created: {result}")
        elif arguments.command == "restore":
            safety = restore_database(
                arguments.backup,
                arguments.database,
                pid_file=arguments.pid_file,
                confirm_replace=arguments.confirm_replace,
            )
            print("Database restored.")
            if safety is not None:
                print(f"Pre-restore backup: {safety}")
        else:
            verify_database(arguments.database)
            print("Database integrity check passed.")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Local runtime operation failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
