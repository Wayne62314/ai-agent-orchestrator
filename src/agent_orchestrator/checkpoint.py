"""Versioned checkpoint files with two-phase database registration."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .models import CheckpointRecord, Task
from .security import SensitiveDataRedactor
from .store import SQLiteStore, canonical_json, utc_now
from .workspace import WorkspaceSnapshot

CHECKPOINT_SCHEMA_VERSION = 1
REQUIRED_SECTIONS = {
    "schema_version",
    "task",
    "workspace",
    "progress",
    "decisions",
    "current_block",
    "next_action",
    "verification",
    "permissions",
    "provenance",
}


class CheckpointService:
    def __init__(
        self,
        store: SQLiteStore,
        checkpoint_root: str | Path,
        *,
        redactor: SensitiveDataRedactor | None = None,
    ):
        self.store = store
        self.checkpoint_root = Path(checkpoint_root).expanduser().resolve()
        self.redactor = redactor or SensitiveDataRedactor()

    def create(
        self,
        *,
        task: Task,
        run_id: str | None,
        workspace: WorkspaceSnapshot,
        progress: Mapping[str, Any],
        decisions: Sequence[Mapping[str, Any]],
        current_block: Mapping[str, Any],
        next_action: Mapping[str, Any],
        verification: Mapping[str, Any],
        permissions: Mapping[str, Any],
        relevant_files: Sequence[str] = (),
    ) -> CheckpointRecord:
        checkpoint_id = f"checkpoint_{uuid.uuid4().hex}"
        task_directory = self.checkpoint_root / task.task_id
        final_path = task_directory / f"{checkpoint_id}.json"
        reserved = self.store.reserve_checkpoint(
            checkpoint_id=checkpoint_id,
            task_id=task.task_id,
            run_id=run_id,
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            workspace_revision=workspace.head,
            payload_path=str(final_path),
        )
        created_at = utc_now()
        payload: dict[str, Any] = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "task": {
                "id": task.task_id,
                "objective": task.objective,
                "acceptance_criteria": task.acceptance_policy,
                "scope": task.permissions_policy,
            },
            "workspace": {
                **workspace.to_dict(),
                "relevant_files": sorted(set(relevant_files)),
            },
            "progress": dict(progress),
            "decisions": [dict(item) for item in decisions],
            "current_block": dict(current_block),
            "next_action": dict(next_action),
            "verification": dict(verification),
            "permissions": dict(permissions),
            "provenance": {
                "checkpoint_id": checkpoint_id,
                "run_id": run_id,
                "sequence": reserved.sequence,
                "created_at": created_at,
                "payload_hash": "",
            },
        }
        payload = self.redactor.redact(payload)
        payload_hash = self._payload_hash(payload)
        payload["provenance"]["payload_hash"] = payload_hash
        try:
            self._atomic_write_json(final_path, payload)
            return self.store.finalize_checkpoint(
                checkpoint_id=checkpoint_id,
                payload_path=str(final_path),
                payload_hash=payload_hash,
            )
        except BaseException as exc:
            self.store.fail_checkpoint(
                checkpoint_id=checkpoint_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def load(self, record: CheckpointRecord) -> dict[str, Any]:
        if record.status != "READY":
            raise ValidationError(
                f"Checkpoint {record.checkpoint_id!r} is not ready."
            )
        path = Path(record.payload_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Checkpoint cannot be read: {exc}") from exc
        self.validate(payload)
        computed = self._payload_hash(payload)
        embedded = payload["provenance"]["payload_hash"]
        if computed != record.payload_hash or computed != embedded:
            raise ValidationError("Checkpoint hash verification failed.")
        return payload

    @staticmethod
    def validate(payload: Mapping[str, Any]) -> None:
        missing = REQUIRED_SECTIONS - set(payload)
        if missing:
            raise ValidationError(
                f"Checkpoint is missing sections: {', '.join(sorted(missing))}."
            )
        if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValidationError(
                f"Unsupported checkpoint schema: {payload.get('schema_version')!r}."
            )
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            raise ValidationError("Checkpoint provenance must be an object.")
        if not provenance.get("checkpoint_id"):
            raise ValidationError("Checkpoint id is required.")

    @staticmethod
    def _payload_hash(payload: Mapping[str, Any]) -> str:
        normalized = json.loads(canonical_json(payload))
        provenance = normalized.get("provenance")
        if isinstance(provenance, dict):
            provenance["payload_hash"] = ""
        return hashlib.sha256(
            canonical_json(normalized).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(
                    payload,
                    stream,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
