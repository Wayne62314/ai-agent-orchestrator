"""Transactional SQLite persistence and hash-chained audit storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import ConcurrencyError, NotFoundError, ValidationError
from .models import (
    AuditEntry,
    CheckpointRecord,
    Event,
    RunRecord,
    RunState,
    Task,
    TaskState,
    VerificationRecord,
)
from .schema import MIGRATIONS


GENESIS_HASH = "0" * 64


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def utc_after(seconds: int) -> str:
    if seconds < 1:
        raise ValidationError("Lease duration must be positive.")
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(
        timespec="microseconds"
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class SQLiteStore:
    """A small transactional repository with one connection per operation."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            for version, script in enumerate(MIGRATIONS, start=1):
                if version in applied:
                    continue
                connection.executescript(script)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_task(
        self,
        *,
        task_id: str,
        title: str,
        objective: str,
        workspace_path: str,
        permissions_policy: Mapping[str, Any],
        acceptance_policy: Mapping[str, Any],
        retry_policy: Mapping[str, Any],
    ) -> Task:
        if not title.strip():
            raise ValidationError("Task title cannot be empty.")
        if not objective.strip():
            raise ValidationError("Task objective cannot be empty.")
        workspace = str(Path(workspace_path).expanduser().resolve())
        timestamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, title, objective, workspace_path, state,
                    permissions_policy_json, acceptance_policy_json,
                    retry_policy_json, created_at, updated_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    task_id,
                    title.strip(),
                    objective.strip(),
                    workspace,
                    TaskState.DRAFT.value,
                    canonical_json(dict(permissions_policy)),
                    canonical_json(dict(acceptance_policy)),
                    canonical_json(dict(retry_policy)),
                    timestamp,
                    timestamp,
                ),
            )
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=None,
                kind="TASK_CREATED",
                payload={
                    "state": TaskState.DRAFT.value,
                    "title": title.strip(),
                    "workspace_path": workspace,
                },
                created_at=timestamp,
            )
            row = self._get_task_row(connection, task_id)
        return self._task_from_row(row)

    def get_task(self, task_id: str) -> Task:
        with self.transaction() as connection:
            row = self._get_task_row(connection, task_id)
        return self._task_from_row(row)

    def list_tasks(
        self,
        *,
        states: Sequence[TaskState] | None = None,
        limit: int = 100,
    ) -> list[Task]:
        if limit < 1 or limit > 1000:
            raise ValidationError("Task list limit must be between 1 and 1000.")
        parameters: list[Any] = []
        query = "SELECT * FROM tasks"
        if states:
            placeholders = ",".join("?" for _ in states)
            query += f" WHERE state IN ({placeholders})"
            parameters.extend(state.value for state in states)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        with self.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._task_from_row(row) for row in rows]

    def create_run(
        self,
        *,
        run_id: str,
        task_id: str,
        engine: str,
        lease_owner: str,
        lease_seconds: int,
        input_checkpoint_id: str | None = None,
    ) -> RunRecord:
        if not engine.strip():
            raise ValidationError("Run engine cannot be empty.")
        if not lease_owner.strip():
            raise ValidationError("Lease owner cannot be empty.")
        started_at = utc_now()
        lease_expires_at = utc_after(lease_seconds)
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            attempt = connection.execute(
                "SELECT COALESCE(MAX(attempt), 0) + 1 FROM runs WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, task_id, attempt, engine, state,
                    input_checkpoint_id, started_at, lease_owner,
                    lease_expires_at, heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    attempt,
                    engine.strip(),
                    RunState.STARTING.value,
                    input_checkpoint_id,
                    started_at,
                    lease_owner,
                    lease_expires_at,
                    started_at,
                ),
            )
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=run_id,
                kind="RUN_CREATED",
                payload={
                    "attempt": attempt,
                    "engine": engine.strip(),
                    "lease_expires_at": lease_expires_at,
                    "state": RunState.STARTING.value,
                },
            )
            row = self._get_run_row(connection, run_id)
        return self._run_from_row(row)

    def get_run(self, run_id: str) -> RunRecord:
        with self.transaction() as connection:
            row = self._get_run_row(connection, run_id)
        return self._run_from_row(row)

    def bind_run(
        self,
        *,
        run_id: str,
        lease_owner: str,
        provider_run_id: str,
        thread_id: str | None,
        lease_seconds: int,
    ) -> RunRecord:
        heartbeat = utc_now()
        expiry = utc_after(lease_seconds)
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET provider_run_id = ?, thread_id = ?, state = ?,
                    heartbeat_at = ?, lease_expires_at = ?
                WHERE run_id = ? AND lease_owner = ? AND state = ?
                """,
                (
                    provider_run_id,
                    thread_id,
                    RunState.RUNNING.value,
                    heartbeat,
                    expiry,
                    run_id,
                    lease_owner,
                    RunState.STARTING.value,
                ),
            )
            if cursor.rowcount != 1:
                current = self._get_run_row(connection, run_id)
                raise ConcurrencyError(
                    f"Cannot bind run {run_id!r}; found "
                    f"{current['state']} owned by {current['lease_owner']!r}."
                )
            row = self._get_run_row(connection, run_id)
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=run_id,
                kind="RUN_BOUND",
                payload={
                    "provider_run_id": provider_run_id,
                    "state": RunState.RUNNING.value,
                    "thread_id": thread_id,
                },
            )
        return self._run_from_row(row)

    def heartbeat_run(
        self,
        *,
        run_id: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> RunRecord:
        heartbeat = utc_now()
        expiry = utc_after(lease_seconds)
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE run_id = ? AND lease_owner = ?
                  AND state IN (?, ?)
                """,
                (
                    heartbeat,
                    expiry,
                    run_id,
                    lease_owner,
                    RunState.STARTING.value,
                    RunState.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                current = self._get_run_row(connection, run_id)
                raise ConcurrencyError(
                    f"Cannot heartbeat run {run_id!r}; found "
                    f"{current['state']} owned by {current['lease_owner']!r}."
                )
            row = self._get_run_row(connection, run_id)
        return self._run_from_row(row)

    def finish_run(
        self,
        *,
        run_id: str,
        lease_owner: str,
        state: RunState,
        exit_reason: str | None = None,
        result_summary: str | None = None,
    ) -> RunRecord:
        if not state.is_terminal:
            raise ValidationError("finish_run requires a terminal run state.")
        ended_at = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET state = ?, ended_at = ?, exit_reason = ?,
                    result_summary = ?, lease_expires_at = NULL
                WHERE run_id = ? AND lease_owner = ?
                  AND state IN (?, ?)
                """,
                (
                    state.value,
                    ended_at,
                    exit_reason,
                    result_summary,
                    run_id,
                    lease_owner,
                    RunState.STARTING.value,
                    RunState.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                current = self._get_run_row(connection, run_id)
                raise ConcurrencyError(
                    f"Cannot finish run {run_id!r}; found "
                    f"{current['state']} owned by {current['lease_owner']!r}."
                )
            row = self._get_run_row(connection, run_id)
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=run_id,
                kind="RUN_FINISHED",
                payload={
                    "exit_reason": exit_reason,
                    "state": state.value,
                },
            )
        return self._run_from_row(row)

    def list_expired_runs(self, *, observed_at: str | None = None) -> list[RunRecord]:
        cutoff = observed_at or utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs
                WHERE state IN (?, ?)
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at ASC
                """,
                (
                    RunState.STARTING.value,
                    RunState.RUNNING.value,
                    cutoff,
                ),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def abandon_expired_run(
        self,
        *,
        run_id: str,
        observed_at: str | None = None,
    ) -> RunRecord:
        cutoff = observed_at or utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET state = ?, ended_at = ?, exit_reason = ?,
                    lease_expires_at = NULL
                WHERE run_id = ?
                  AND state IN (?, ?)
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (
                    RunState.ABANDONED.value,
                    cutoff,
                    "lease_expired",
                    run_id,
                    RunState.STARTING.value,
                    RunState.RUNNING.value,
                    cutoff,
                ),
            )
            if cursor.rowcount != 1:
                current = self._get_run_row(connection, run_id)
                raise ConcurrencyError(
                    f"Run {run_id!r} is not an expired active run; "
                    f"found {current['state']}."
                )
            row = self._get_run_row(connection, run_id)
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=run_id,
                kind="RUN_ABANDONED",
                payload={"reason": "lease_expired"},
            )
        return self._run_from_row(row)

    def reserve_checkpoint(
        self,
        *,
        checkpoint_id: str,
        task_id: str,
        run_id: str | None,
        schema_version: int,
        workspace_revision: str | None,
        payload_path: str,
    ) -> CheckpointRecord:
        created_at = utc_now()
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            sequence = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM checkpoints WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO checkpoints(
                    checkpoint_id, task_id, run_id, sequence,
                    schema_version, workspace_revision, payload_path,
                    payload_hash, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, 'PENDING')
                """,
                (
                    checkpoint_id,
                    task_id,
                    run_id,
                    sequence,
                    schema_version,
                    workspace_revision,
                    payload_path,
                    created_at,
                ),
            )
            row = self._get_checkpoint_row(connection, checkpoint_id)
        return self._checkpoint_from_row(row)

    def finalize_checkpoint(
        self,
        *,
        checkpoint_id: str,
        payload_path: str,
        payload_hash: str,
    ) -> CheckpointRecord:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE checkpoints
                SET payload_path = ?, payload_hash = ?, status = 'READY', error = NULL
                WHERE checkpoint_id = ? AND status = 'PENDING'
                """,
                (payload_path, payload_hash, checkpoint_id),
            )
            if cursor.rowcount != 1:
                current = self._get_checkpoint_row(connection, checkpoint_id)
                raise ConcurrencyError(
                    f"Checkpoint {checkpoint_id!r} is {current['status']}, not PENDING."
                )
            row = self._get_checkpoint_row(connection, checkpoint_id)
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=row["run_id"],
                kind="CHECKPOINT_READY",
                payload={
                    "checkpoint_id": checkpoint_id,
                    "payload_hash": payload_hash,
                    "sequence": row["sequence"],
                },
            )
        return self._checkpoint_from_row(row)

    def fail_checkpoint(self, *, checkpoint_id: str, error: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE checkpoints
                SET status = 'FAILED', error = ?
                WHERE checkpoint_id = ? AND status = 'PENDING'
                """,
                (error, checkpoint_id),
            )

    def latest_checkpoint(self, task_id: str) -> CheckpointRecord:
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM checkpoints
                WHERE task_id = ? AND status = 'READY'
                ORDER BY sequence DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(
                    f"No ready checkpoint exists for task {task_id!r}."
                )
        return self._checkpoint_from_row(row)

    def next_verification_attempt(self, task_id: str) -> int:
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            return int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(attempt), 0) + 1
                    FROM verifications WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()[0]
            )

    def record_verification(
        self,
        *,
        verification_id: str,
        task_id: str,
        run_id: str | None,
        attempt: int,
        check_name: str,
        required: bool,
        status: str,
        command: Sequence[str],
        exit_code: int | None,
        timed_out: bool,
        output_truncated: bool,
        duration_ms: int,
        summary: str,
        log_path: str,
        started_at: str,
        ended_at: str,
    ) -> VerificationRecord:
        if attempt < 1:
            raise ValidationError("Verification attempt must be positive.")
        created_at = utc_now()
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            connection.execute(
                """
                INSERT INTO verifications(
                    verification_id, task_id, run_id, check_name, required,
                    status, exit_code, summary, log_path, created_at, attempt,
                    command_json, timed_out, output_truncated, duration_ms,
                    started_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verification_id,
                    task_id,
                    run_id,
                    check_name,
                    int(required),
                    status,
                    exit_code,
                    summary,
                    log_path,
                    created_at,
                    attempt,
                    canonical_json(list(command)),
                    int(timed_out),
                    int(output_truncated),
                    duration_ms,
                    started_at,
                    ended_at,
                ),
            )
            row = self._get_verification_row(connection, verification_id)
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=run_id,
                kind="VERIFICATION_RECORDED",
                payload={
                    "attempt": attempt,
                    "check_name": check_name,
                    "required": required,
                    "status": status,
                    "timed_out": timed_out,
                },
            )
        return self._verification_from_row(row)

    def list_verifications(
        self,
        task_id: str,
        *,
        attempt: int | None = None,
    ) -> list[VerificationRecord]:
        query = "SELECT * FROM verifications WHERE task_id = ?"
        parameters: list[Any] = [task_id]
        if attempt is not None:
            query += " AND attempt = ?"
            parameters.append(attempt)
        query += " ORDER BY attempt ASC, created_at ASC"
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(query, parameters).fetchall()
        return [self._verification_from_row(row) for row in rows]

    def insert_event(
        self,
        connection: sqlite3.Connection,
        event: Event,
    ) -> tuple[bool, sqlite3.Row | None]:
        occurred_at = event.occurred_at or utc_now()
        payload_json = canonical_json(dict(event.payload))
        try:
            connection.execute(
                """
                INSERT INTO events(
                    event_id, task_id, event_type, source, dedupe_key,
                    payload_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.task_id,
                    event.event_type.value,
                    event.source,
                    event.dedupe_key,
                    payload_json,
                    occurred_at,
                ),
            )
            return True, None
        except sqlite3.IntegrityError:
            existing = connection.execute(
                """
                SELECT * FROM events
                WHERE event_id = ? OR dedupe_key = ?
                ORDER BY CASE WHEN event_id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (event.event_id, event.dedupe_key, event.event_id),
            ).fetchone()
            if existing is None:
                raise
            if (
                existing["task_id"] != event.task_id
                or existing["event_type"] != event.event_type.value
                or existing["source"] != event.source
                or existing["dedupe_key"] != event.dedupe_key
                or existing["payload_json"] != payload_json
            ):
                raise ValidationError(
                    "An event id or dedupe key was reused for different event content."
                )
            return False, existing

    def update_task_state(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
        expected_version: int,
    ) -> Task:
        timestamp = utc_now()
        cursor = connection.execute(
            """
            UPDATE tasks
            SET state = ?, updated_at = ?, version = version + 1
            WHERE task_id = ? AND state = ? AND version = ?
            """,
            (
                to_state.value,
                timestamp,
                task_id,
                from_state.value,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            current = self._get_task_row(connection, task_id)
            raise ConcurrencyError(
                "Task changed concurrently: "
                f"expected {from_state.value}@{expected_version}, "
                f"found {current['state']}@{current['version']}."
            )
        return self._task_from_row(self._get_task_row(connection, task_id))

    def mark_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        outcome: str,
        reason: str | None,
    ) -> None:
        connection.execute(
            """
            UPDATE events
            SET processed_at = ?, outcome = ?, outcome_reason = ?
            WHERE event_id = ?
            """,
            (utc_now(), outcome, reason, event_id),
        )

    def append_audit(
        self,
        *,
        task_id: str | None,
        run_id: str | None,
        kind: str,
        payload: Mapping[str, Any],
    ) -> AuditEntry:
        with self.transaction() as connection:
            return self._append_audit(
                connection,
                task_id=task_id,
                run_id=run_id,
                kind=kind,
                payload=payload,
            )

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str | None,
        run_id: str | None,
        kind: str,
        payload: Mapping[str, Any],
        created_at: str | None = None,
    ) -> AuditEntry:
        timestamp = created_at or utc_now()
        previous = connection.execute(
            """
            SELECT entry_hash FROM audit_log
            WHERE task_id IS ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        previous_hash = previous["entry_hash"] if previous else GENESIS_HASH
        audit_id = f"audit_{uuid.uuid4().hex}"
        payload_json = canonical_json(dict(payload))
        hash_input = canonical_json(
            {
                "audit_id": audit_id,
                "created_at": timestamp,
                "kind": kind,
                "payload": json.loads(payload_json),
                "previous_hash": previous_hash,
                "run_id": run_id,
                "task_id": task_id,
            }
        )
        entry_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        cursor = connection.execute(
            """
            INSERT INTO audit_log(
                audit_id, task_id, run_id, kind, payload_json,
                created_at, previous_hash, entry_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                task_id,
                run_id,
                kind,
                payload_json,
                timestamp,
                previous_hash,
                entry_hash,
            ),
        )
        return AuditEntry(
            sequence=int(cursor.lastrowid),
            audit_id=audit_id,
            task_id=task_id,
            run_id=run_id,
            kind=kind,
            payload=json.loads(payload_json),
            created_at=timestamp,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
        )

    def list_audit(
        self,
        *,
        task_id: str | None,
        limit: int = 200,
    ) -> list[AuditEntry]:
        if limit < 1 or limit > 5000:
            raise ValidationError("Audit list limit must be between 1 and 5000.")
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_log
                WHERE task_id IS ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
        return [self._audit_from_row(row) for row in rows]

    def verify_audit_chain(self, task_id: str | None) -> bool:
        entries = self.list_audit(task_id=task_id, limit=5000)
        expected_previous = GENESIS_HASH
        for entry in entries:
            if entry.previous_hash != expected_previous:
                return False
            hash_input = canonical_json(
                {
                    "audit_id": entry.audit_id,
                    "created_at": entry.created_at,
                    "kind": entry.kind,
                    "payload": dict(entry.payload),
                    "previous_hash": entry.previous_hash,
                    "run_id": entry.run_id,
                    "task_id": entry.task_id,
                }
            )
            if hashlib.sha256(hash_input.encode("utf-8")).hexdigest() != entry.entry_hash:
                return False
            expected_previous = entry.entry_hash
        return True

    def get_event(self, event_id: str) -> sqlite3.Row:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Event {event_id!r} was not found.")
            return row

    @staticmethod
    def _get_task_row(
        connection: sqlite3.Connection,
        task_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Task {task_id!r} was not found.")
        return row

    @staticmethod
    def _get_run_row(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Run {run_id!r} was not found.")
        return row

    @staticmethod
    def _get_checkpoint_row(
        connection: sqlite3.Connection,
        checkpoint_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Checkpoint {checkpoint_id!r} was not found.")
        return row

    @staticmethod
    def _get_verification_row(
        connection: sqlite3.Connection,
        verification_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM verifications WHERE verification_id = ?",
            (verification_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"Verification {verification_id!r} was not found."
            )
        return row

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> Task:
        return Task(
            task_id=row["task_id"],
            title=row["title"],
            objective=row["objective"],
            workspace_path=row["workspace_path"],
            state=TaskState(row["state"]),
            permissions_policy=json.loads(row["permissions_policy_json"]),
            acceptance_policy=json.loads(row["acceptance_policy_json"]),
            retry_policy=json.loads(row["retry_policy_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )

    @staticmethod
    def _audit_from_row(row: sqlite3.Row) -> AuditEntry:
        return AuditEntry(
            sequence=row["sequence"],
            audit_id=row["audit_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            kind=row["kind"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
            previous_hash=row["previous_hash"],
            entry_hash=row["entry_hash"],
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            task_id=row["task_id"],
            attempt=row["attempt"],
            engine=row["engine"],
            state=RunState(row["state"]),
            input_checkpoint_id=row["input_checkpoint_id"],
            started_at=row["started_at"],
            provider_run_id=row["provider_run_id"],
            thread_id=row["thread_id"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            heartbeat_at=row["heartbeat_at"],
            ended_at=row["ended_at"],
            exit_reason=row["exit_reason"],
            result_summary=row["result_summary"],
        )

    @staticmethod
    def _checkpoint_from_row(row: sqlite3.Row) -> CheckpointRecord:
        return CheckpointRecord(
            checkpoint_id=row["checkpoint_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            schema_version=row["schema_version"],
            workspace_revision=row["workspace_revision"],
            payload_path=row["payload_path"],
            payload_hash=row["payload_hash"],
            created_at=row["created_at"],
            status=row["status"],
            error=row["error"],
        )

    @staticmethod
    def _verification_from_row(row: sqlite3.Row) -> VerificationRecord:
        return VerificationRecord(
            verification_id=row["verification_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            attempt=row["attempt"],
            check_name=row["check_name"],
            required=bool(row["required"]),
            status=row["status"],
            command=tuple(json.loads(row["command_json"])),
            exit_code=row["exit_code"],
            timed_out=bool(row["timed_out"]),
            output_truncated=bool(row["output_truncated"]),
            duration_ms=row["duration_ms"],
            summary=row["summary"] or "",
            log_path=row["log_path"] or "",
            created_at=row["created_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )
