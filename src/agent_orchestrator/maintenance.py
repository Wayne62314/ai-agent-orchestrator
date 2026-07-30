"""Guarded backup, restore, and redacted diagnostic export for desktop use."""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import zipfile
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database_files import backup_database, verify_database
from .errors import ValidationError
from .store import SQLiteStore


@dataclass(frozen=True, slots=True)
class BackupInfo:
    backup_id: str
    created_at: str
    size_bytes: int


def list_backups(destination_directory: Path) -> list[BackupInfo]:
    items: list[BackupInfo] = []
    for path in _backup_paths(destination_directory):
        stat = path.stat()
        items.append(
            BackupInfo(
                backup_id=path.name,
                created_at=datetime.fromtimestamp(
                    stat.st_mtime,
                    UTC,
                ).isoformat(timespec="seconds"),
                size_bytes=stat.st_size,
            )
        )
    return items


def resolve_backup(destination_directory: Path, backup_id: str) -> Path:
    if (
        not backup_id
        or Path(backup_id).name != backup_id
        or not backup_id.startswith("state-")
        or not backup_id.endswith(".db")
    ):
        raise ValidationError("The backup identifier is invalid.")
    root = destination_directory.expanduser().resolve()
    candidate = (root / backup_id).resolve()
    if candidate.parent != root or not candidate.is_file():
        raise ValidationError("The selected backup does not exist.")
    verify_database(candidate)
    return candidate


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


def create_diagnostic_bundle(
    store: SQLiteStore,
    destination_directory: Path,
    *,
    app_version: str,
    schema_version: int,
) -> Path:
    verify_database(store.database_path)
    destination_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC)
    name = timestamp.strftime("diagnostics-%Y%m%dT%H%M%S%fZ.zip")
    destination = destination_directory / name
    temporary = destination.with_suffix(".zip.tmp")
    tasks = store.list_tasks(limit=100)
    task_states = Counter(task.state.value for task in tasks)
    active = store.get_active_task()
    audits: list[dict[str, Any]] = []
    for task in tasks:
        for entry in store.list_audit(task_id=task.task_id, limit=50):
            audits.append(
                {
                    "sequence": entry.sequence,
                    "taskId": entry.task_id,
                    "runId": entry.run_id,
                    "kind": entry.kind,
                    "createdAt": entry.created_at,
                }
            )
    summary = {
        "format": "aiao.diagnostics.v1",
        "createdAt": timestamp.isoformat(timespec="seconds"),
        "appVersion": app_version,
        "schemaVersion": schema_version,
        "database": {
            "integrity": "ok",
            "sizeBytes": store.database_path.stat().st_size,
        },
        "runtime": {
            "os": platform.system(),
            "osRelease": platform.release(),
            "python": platform.python_version(),
            "architecture": platform.machine(),
        },
        "tasks": {
            "total": len(tasks),
            "byState": dict(sorted(task_states.items())),
            "activeTaskId": active.task_id if active is not None else None,
        },
        "auditEntries": sorted(
            audits,
            key=lambda item: (item["createdAt"], item["sequence"]),
            reverse=True,
        )[:200],
    }
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "diagnostics.json",
                json.dumps(
                    summary,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
            )
            archive.writestr(
                "README.txt",
                (
                    "This bundle contains redacted runtime metadata only.\n"
                    "It excludes credentials, source code, task prompts, raw logs, "
                    "checkpoint payloads, and the SQLite database.\n"
                ),
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def backup_to_dict(info: BackupInfo) -> dict[str, Any]:
    return {
        "id": info.backup_id,
        "createdAt": info.created_at,
        "sizeBytes": info.size_bytes,
    }


def _backup_paths(destination_directory: Path) -> list[Path]:
    if not destination_directory.exists():
        return []
    return sorted(
        destination_directory.glob("state-*.db"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
