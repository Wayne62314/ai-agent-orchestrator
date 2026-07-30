"""Consistent backup and guarded restore helpers for a local SQLite runtime."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from agent_orchestrator.maintenance import (
    backup_database,
    restore_database,
    verify_database,
)


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
