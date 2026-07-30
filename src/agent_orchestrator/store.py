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
    ActiveTaskLease,
    ApprovalRecord,
    AuditEntry,
    CheckpointRecord,
    Event,
    ExternalEventKind,
    ExternalEventRecord,
    ExternalEventStatus,
    RunRecord,
    RunState,
    SideEffectRecord,
    SideEffectStatus,
    SignalWaitRecord,
    SignalWaitStatus,
    Task,
    TaskState,
    VerificationRecord,
    WorktreeRecord,
    WorktreeState,
)
from .schema import MIGRATIONS
from .security import SensitiveDataRedactor

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
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            connection.execute("BEGIN IMMEDIATE")
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
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ValidationError(
                    "Schema migration produced foreign-key violations."
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

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
        redactor = SensitiveDataRedactor()
        redactor.require_safe(title, context="Task title")
        redactor.require_safe(objective, context="Task objective")
        redactor.require_safe(
            dict(permissions_policy),
            context="Permissions policy",
        )
        redactor.require_safe(
            dict(acceptance_policy),
            context="Acceptance policy",
        )
        redactor.require_safe(dict(retry_policy), context="Retry policy")
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

    def create_worktree(
        self,
        *,
        task_id: str,
        repository_path: str | Path,
        worktree_path: str | Path,
        branch_name: str,
        base_revision: str,
    ) -> WorktreeRecord:
        if not branch_name.strip():
            raise ValidationError("Worktree branch name cannot be empty.")
        if not base_revision.strip():
            raise ValidationError("Worktree base revision cannot be empty.")
        repository = str(Path(repository_path).expanduser().resolve())
        worktree = str(Path(worktree_path).expanduser().resolve())
        timestamp = utc_now()
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            connection.execute(
                """
                INSERT INTO task_worktrees(
                    task_id, repository_path, worktree_path, branch_name,
                    base_revision, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    repository,
                    worktree,
                    branch_name.strip(),
                    base_revision.strip(),
                    WorktreeState.CREATING.value,
                    timestamp,
                    timestamp,
                ),
            )
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=None,
                kind="WORKTREE_REGISTERED",
                payload={
                    "base_revision": base_revision.strip(),
                    "branch_name": branch_name.strip(),
                    "repository_path": repository,
                    "state": WorktreeState.CREATING.value,
                    "worktree_path": worktree,
                },
            )
            row = self._get_worktree_row(connection, task_id)
        return self._worktree_from_row(row)

    def get_worktree(self, task_id: str) -> WorktreeRecord:
        with self.transaction() as connection:
            row = self._get_worktree_row(connection, task_id)
        return self._worktree_from_row(row)

    def update_worktree_state(
        self,
        *,
        task_id: str,
        expected: WorktreeState,
        target: WorktreeState,
    ) -> WorktreeRecord:
        timestamp = utc_now()
        removed_at = timestamp if target == WorktreeState.REMOVED else None
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE task_worktrees
                SET state = ?, updated_at = ?, removed_at = ?
                WHERE task_id = ? AND state = ?
                """,
                (
                    target.value,
                    timestamp,
                    removed_at,
                    task_id,
                    expected.value,
                ),
            )
            if cursor.rowcount != 1:
                current = self._get_worktree_row(connection, task_id)
                raise ConcurrencyError(
                    f"Cannot move worktree {task_id!r} from {expected.value}; "
                    f"found {current['state']}."
                )
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=None,
                kind="WORKTREE_STATE_CHANGED",
                payload={
                    "from_state": expected.value,
                    "to_state": target.value,
                },
            )
            row = self._get_worktree_row(connection, task_id)
        return self._worktree_from_row(row)

    def acquire_active_task(
        self,
        *,
        task_id: str,
        owner: str,
        lease_seconds: int,
    ) -> ActiveTaskLease:
        if not owner.strip():
            raise ValidationError("Active-task lease owner cannot be empty.")
        acquired_at = utc_now()
        expires_at = utc_after(lease_seconds)
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            current = connection.execute(
                "SELECT * FROM active_task_lease WHERE slot = 1"
            ).fetchone()
            if current is not None:
                if (
                    current["task_id"] == task_id
                    and current["owner"] == owner
                ):
                    connection.execute(
                        """
                        UPDATE active_task_lease
                        SET heartbeat_at = ?, expires_at = ?
                        WHERE slot = 1
                        """,
                        (acquired_at, expires_at),
                    )
                    row = connection.execute(
                        "SELECT * FROM active_task_lease WHERE slot = 1"
                    ).fetchone()
                    return self._active_task_lease_from_row(row)
                if current["expires_at"] > acquired_at:
                    raise ConcurrencyError(
                        "Another task is active: "
                        f"{current['task_id']} owned by {current['owner']!r}."
                    )
                previous_task = self._task_from_row(
                    self._get_task_row(connection, current["task_id"])
                )
                if (
                    current["task_id"] != task_id
                    and not previous_task.state.is_terminal
                ):
                    raise ConcurrencyError(
                        "The active-task lease expired, but its task is still "
                        f"{previous_task.state.value}: {previous_task.task_id}. "
                        "Recover or cancel that task before starting another."
                    )
                connection.execute(
                    "DELETE FROM active_task_lease WHERE slot = 1"
                )
                self._append_audit(
                    connection,
                    task_id=current["task_id"],
                    run_id=None,
                    kind="ACTIVE_TASK_LEASE_EXPIRED",
                    payload={
                        "expired_at": current["expires_at"],
                        "owner": current["owner"],
                    },
                )
            connection.execute(
                """
                INSERT INTO active_task_lease(
                    slot, task_id, owner, acquired_at, heartbeat_at, expires_at
                ) VALUES (1, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    owner.strip(),
                    acquired_at,
                    acquired_at,
                    expires_at,
                ),
            )
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=None,
                kind="ACTIVE_TASK_LEASE_ACQUIRED",
                payload={
                    "expires_at": expires_at,
                    "owner": owner.strip(),
                },
            )
            row = connection.execute(
                "SELECT * FROM active_task_lease WHERE slot = 1"
            ).fetchone()
        return self._active_task_lease_from_row(row)

    def heartbeat_active_task(
        self,
        *,
        task_id: str,
        owner: str,
        lease_seconds: int,
    ) -> ActiveTaskLease:
        heartbeat_at = utc_now()
        expires_at = utc_after(lease_seconds)
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE active_task_lease
                SET heartbeat_at = ?, expires_at = ?
                WHERE slot = 1 AND task_id = ? AND owner = ?
                """,
                (heartbeat_at, expires_at, task_id, owner),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyError(
                    f"Task {task_id!r} does not own the active-task lease."
                )
            row = connection.execute(
                "SELECT * FROM active_task_lease WHERE slot = 1"
            ).fetchone()
        return self._active_task_lease_from_row(row)

    def release_active_task(self, *, task_id: str, owner: str) -> bool:
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM active_task_lease WHERE slot = 1"
            ).fetchone()
            if current is None:
                return False
            if current["task_id"] != task_id or current["owner"] != owner:
                raise ConcurrencyError(
                    f"Task {task_id!r} does not own the active-task lease."
                )
            connection.execute("DELETE FROM active_task_lease WHERE slot = 1")
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=None,
                kind="ACTIVE_TASK_LEASE_RELEASED",
                payload={"owner": owner},
            )
        return True

    def get_active_task(self) -> ActiveTaskLease | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM active_task_lease WHERE slot = 1"
            ).fetchone()
        return self._active_task_lease_from_row(row) if row is not None else None

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

    def latest_run(self, task_id: str) -> RunRecord | None:
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            row = connection.execute(
                """
                SELECT * FROM runs
                WHERE task_id = ?
                ORDER BY attempt DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs_page(
        self,
        task_id: str,
        *,
        limit: int,
        before_attempt: int | None = None,
    ) -> tuple[list[RunRecord], int | None]:
        query = "SELECT * FROM runs WHERE task_id = ?"
        parameters: list[Any] = [task_id]
        if before_attempt is not None:
            query += " AND attempt < ?"
            parameters.append(before_attempt)
        query += " ORDER BY attempt DESC LIMIT ?"
        parameters.append(limit + 1)
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(query, parameters).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = int(page[-1]["attempt"]) if has_more and page else None
        return [self._run_from_row(row) for row in page], next_cursor

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

    def list_checkpoints_page(
        self,
        task_id: str,
        *,
        limit: int,
        before_sequence: int | None = None,
    ) -> tuple[list[CheckpointRecord], int | None]:
        query = "SELECT * FROM checkpoints WHERE task_id = ?"
        parameters: list[Any] = [task_id]
        if before_sequence is not None:
            query += " AND sequence < ?"
            parameters.append(before_sequence)
        query += " ORDER BY sequence DESC LIMIT ?"
        parameters.append(limit + 1)
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(query, parameters).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = int(page[-1]["sequence"]) if has_more and page else None
        return [self._checkpoint_from_row(row) for row in page], next_cursor

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

    def list_verifications_page(
        self,
        task_id: str,
        *,
        limit: int,
        before_rowid: int | None = None,
    ) -> tuple[list[VerificationRecord], int | None]:
        query = (
            "SELECT rowid AS cursor_key, * FROM verifications "
            "WHERE task_id = ?"
        )
        parameters: list[Any] = [task_id]
        if before_rowid is not None:
            query += " AND rowid < ?"
            parameters.append(before_rowid)
        query += " ORDER BY rowid DESC LIMIT ?"
        parameters.append(limit + 1)
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(query, parameters).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            int(page[-1]["cursor_key"]) if has_more and page else None
        )
        return [self._verification_from_row(row) for row in page], next_cursor

    def create_approval(
        self,
        *,
        approval_id: str,
        task_id: str,
        action_type: str,
        action_hash: str,
        parameters: Mapping[str, Any],
        risk_summary: str,
        rollback_plan: str,
        request_key: str,
        expires_at: str,
    ) -> ApprovalRecord:
        redactor = SensitiveDataRedactor()
        redactor.require_safe(dict(parameters), context="Approval parameters")
        requested_at = utc_now()
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            try:
                connection.execute(
                    """
                    INSERT INTO approvals(
                        approval_id, task_id, requested_action, action_hash,
                        risk_summary, status, requested_at, expires_at,
                        action_type, parameters_json, rollback_plan, request_key
                    ) VALUES (?, ?, ?, ?, ?, 'REQUESTED', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        task_id,
                        action_type,
                        action_hash,
                        redactor.redact_text(risk_summary),
                        requested_at,
                        expires_at,
                        action_type,
                        canonical_json(dict(parameters)),
                        redactor.redact_text(rollback_plan),
                        request_key,
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM approvals WHERE request_key = ?",
                    (request_key,),
                ).fetchone()
                if (
                    row is None
                    or row["task_id"] != task_id
                    or row["action_hash"] != action_hash
                    or row["action_type"] != action_type
                    or row["parameters_json"] != canonical_json(dict(parameters))
                ):
                    raise
                return self._approval_from_row(row)
            row = self._get_approval_row(connection, approval_id)
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=None,
                kind="APPROVAL_REQUESTED",
                payload={
                    "approval_id": approval_id,
                    "action_type": action_type,
                    "action_hash": action_hash,
                    "expires_at": expires_at,
                },
            )
        return self._approval_from_row(row)

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        with self.transaction() as connection:
            row = self._get_approval_row(connection, approval_id)
        return self._approval_from_row(row)

    def find_approval_by_request_key(
        self,
        request_key: str,
    ) -> ApprovalRecord | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE request_key = ?",
                (request_key,),
            ).fetchone()
        return self._approval_from_row(row) if row is not None else None

    def list_approvals(
        self,
        task_id: str,
        *,
        status: str | None = None,
    ) -> list[ApprovalRecord]:
        query = "SELECT * FROM approvals WHERE task_id = ?"
        parameters: list[Any] = [task_id]
        if status:
            query += " AND status = ?"
            parameters.append(status)
        query += " ORDER BY requested_at ASC"
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(query, parameters).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def decide_approval(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
    ) -> ApprovalRecord:
        decided_at = utc_now()
        status = "APPROVED" if approved else "DENIED"
        with self.transaction() as connection:
            current = self._get_approval_row(connection, approval_id)
            if current["status"] == status:
                return self._approval_from_row(current)
            if current["status"] != "REQUESTED":
                raise ConcurrencyError(
                    f"Approval {approval_id!r} is {current['status']}, not REQUESTED."
                )
            if current["expires_at"] and current["expires_at"] <= decided_at:
                raise ValidationError(f"Approval {approval_id!r} has expired.")
            connection.execute(
                """
                UPDATE approvals
                SET status = ?, decided_at = ?, decided_by = ?
                WHERE approval_id = ? AND status = 'REQUESTED'
                """,
                (status, decided_at, decided_by.strip(), approval_id),
            )
            row = self._get_approval_row(connection, approval_id)
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=None,
                kind="APPROVAL_DECIDED",
                payload={
                    "approval_id": approval_id,
                    "action_hash": row["action_hash"],
                    "decided_by": decided_by.strip(),
                    "status": status,
                },
            )
        return self._approval_from_row(row)

    def consume_approval(self, approval_id: str) -> ApprovalRecord:
        consumed_at = utc_now()
        with self.transaction() as connection:
            current = self._get_approval_row(connection, approval_id)
            if current["status"] == "CONSUMED":
                return self._approval_from_row(current)
            if current["status"] != "APPROVED":
                raise ConcurrencyError(
                    f"Approval {approval_id!r} is {current['status']}, not APPROVED."
                )
            connection.execute(
                """
                UPDATE approvals SET status = 'CONSUMED', consumed_at = ?
                WHERE approval_id = ? AND status = 'APPROVED'
                """,
                (consumed_at, approval_id),
            )
            row = self._get_approval_row(connection, approval_id)
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=None,
                kind="APPROVAL_CONSUMED",
                payload={
                    "approval_id": approval_id,
                    "action_hash": row["action_hash"],
                },
            )
        return self._approval_from_row(row)

    def reserve_side_effect(
        self,
        *,
        effect_id: str,
        task_id: str,
        approval_id: str | None,
        idempotency_key: str,
        logical_step: str,
        action_type: str,
        parameters_hash: str,
    ) -> tuple[SideEffectRecord, bool]:
        timestamp = utc_now()
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            try:
                connection.execute(
                    """
                    INSERT INTO side_effects(
                        effect_id, task_id, approval_id, idempotency_key,
                        logical_step, action_type, parameters_hash, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                    """,
                    (
                        effect_id,
                        task_id,
                        approval_id,
                        idempotency_key,
                        logical_step,
                        action_type,
                        parameters_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                created = True
                row = self._get_side_effect_row(connection, effect_id)
                self._append_audit(
                    connection,
                    task_id=task_id,
                    run_id=None,
                    kind="SIDE_EFFECT_RESERVED",
                    payload={
                        "effect_id": effect_id,
                        "action_type": action_type,
                        "idempotency_key": idempotency_key,
                        "parameters_hash": parameters_hash,
                    },
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM side_effects WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if (
                    row is None
                    or row["task_id"] != task_id
                    or row["logical_step"] != logical_step
                    or row["action_type"] != action_type
                    or row["parameters_hash"] != parameters_hash
                ):
                    raise
                created = False
        return self._side_effect_from_row(row), created

    def get_side_effect(self, effect_id: str) -> SideEffectRecord:
        with self.transaction() as connection:
            row = self._get_side_effect_row(connection, effect_id)
        return self._side_effect_from_row(row)

    def find_side_effect_by_idempotency(
        self,
        idempotency_key: str,
    ) -> SideEffectRecord | None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM side_effects WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._side_effect_from_row(row) if row is not None else None

    def list_side_effects(
        self,
        task_id: str,
        *,
        status: SideEffectStatus | None = None,
    ) -> list[SideEffectRecord]:
        query = "SELECT * FROM side_effects WHERE task_id = ?"
        parameters: list[Any] = [task_id]
        if status:
            query += " AND status = ?"
            parameters.append(status.value)
        query += " ORDER BY created_at ASC"
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(query, parameters).fetchall()
        return [self._side_effect_from_row(row) for row in rows]

    def finish_side_effect(
        self,
        *,
        effect_id: str,
        status: SideEffectStatus,
        external_result_id: str | None = None,
        error: str | None = None,
    ) -> SideEffectRecord:
        if status not in {
            SideEffectStatus.SUCCEEDED,
            SideEffectStatus.UNKNOWN,
            SideEffectStatus.FAILED,
        }:
            raise ValidationError("Side effect finish status is invalid.")
        timestamp = utc_now()
        redactor = SensitiveDataRedactor()
        safe_result = (
            redactor.redact_text(external_result_id)
            if external_result_id is not None
            else None
        )
        safe_error = redactor.redact_text(error) if error else None
        with self.transaction() as connection:
            current = self._get_side_effect_row(connection, effect_id)
            current_status = SideEffectStatus(current["status"])
            if current_status == status:
                return self._side_effect_from_row(current)
            allowed = {
                SideEffectStatus.PENDING: {
                    SideEffectStatus.SUCCEEDED,
                    SideEffectStatus.UNKNOWN,
                    SideEffectStatus.FAILED,
                },
                SideEffectStatus.UNKNOWN: {
                    SideEffectStatus.SUCCEEDED,
                    SideEffectStatus.FAILED,
                },
            }
            if status not in allowed.get(current_status, set()):
                raise ConcurrencyError(
                    f"Cannot move side effect {effect_id!r} from "
                    f"{current_status.value} to {status.value}."
                )
            connection.execute(
                """
                UPDATE side_effects
                SET status = ?, external_result_id = ?, error = ?, updated_at = ?
                WHERE effect_id = ? AND status = ?
                """,
                (
                    status.value,
                    safe_result,
                    safe_error,
                    timestamp,
                    effect_id,
                    current_status.value,
                ),
            )
            row = self._get_side_effect_row(connection, effect_id)
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=None,
                kind="SIDE_EFFECT_FINISHED",
                payload={
                    "effect_id": effect_id,
                    "status": status.value,
                    "external_result_id": safe_result,
                },
            )
        return self._side_effect_from_row(row)

    def mark_stale_side_effects_unknown(
        self,
        *,
        older_than_seconds: int,
    ) -> list[SideEffectRecord]:
        if older_than_seconds < 1:
            raise ValidationError("Stale side-effect age must be positive.")
        cutoff = (
            datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        ).isoformat(timespec="microseconds")
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM side_effects
                WHERE status = 'PENDING' AND updated_at <= ?
                ORDER BY created_at ASC
                """,
                (cutoff,),
            ).fetchall()
            recovered: list[SideEffectRecord] = []
            for current in rows:
                connection.execute(
                    """
                    UPDATE side_effects
                    SET status = 'UNKNOWN', error = ?, updated_at = ?
                    WHERE effect_id = ? AND status = 'PENDING'
                    """,
                    ("process_restarted_before_result_was_recorded", utc_now(), current["effect_id"]),
                )
                row = self._get_side_effect_row(connection, current["effect_id"])
                self._append_audit(
                    connection,
                    task_id=row["task_id"],
                    run_id=None,
                    kind="SIDE_EFFECT_RECOVERED_UNKNOWN",
                    payload={"effect_id": row["effect_id"]},
                )
                recovered.append(self._side_effect_from_row(row))
        return recovered

    def create_signal_wait(
        self,
        *,
        wait_id: str,
        task_id: str,
        provider: str,
        kind: ExternalEventKind,
        subject: str,
        condition: Mapping[str, Any],
        timeout_behavior: str,
        deadline_at: str,
    ) -> SignalWaitRecord:
        if not provider.strip() or not subject.strip():
            raise ValidationError("Signal provider and subject cannot be empty.")
        if timeout_behavior != "attention":
            raise ValidationError("Only the 'attention' timeout behavior is supported.")
        timestamp = utc_now()
        safe_condition = SensitiveDataRedactor().redact(dict(condition))
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            try:
                connection.execute(
                    """
                    INSERT INTO signal_waits(
                        wait_id, task_id, provider, event_kind, subject,
                        condition_json, timeout_behavior, status, created_at,
                        deadline_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                    """,
                    (
                        wait_id,
                        task_id,
                        provider,
                        kind.value,
                        subject,
                        canonical_json(safe_condition),
                        timeout_behavior,
                        timestamp,
                        deadline_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError(
                    "An active wait already exists for this task, source, kind, and subject."
                ) from exc
            row = connection.execute(
                "SELECT * FROM signal_waits WHERE wait_id = ?",
                (wait_id,),
            ).fetchone()
            self._append_audit(
                connection,
                task_id=task_id,
                run_id=None,
                kind="SIGNAL_WAIT_REGISTERED",
                payload={
                    "wait_id": wait_id,
                    "provider": provider,
                    "event_kind": kind.value,
                    "subject": subject,
                    "deadline_at": deadline_at,
                },
            )
        return self._signal_wait_from_row(row)

    def list_signal_waits(
        self,
        task_id: str,
        *,
        status: SignalWaitStatus | None = None,
    ) -> list[SignalWaitRecord]:
        query = "SELECT * FROM signal_waits WHERE task_id = ?"
        parameters: list[Any] = [task_id]
        if status is not None:
            query += " AND status = ?"
            parameters.append(status.value)
        query += " ORDER BY created_at ASC"
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(query, parameters).fetchall()
        return [self._signal_wait_from_row(row) for row in rows]

    def list_expired_signal_waits(
        self,
        *,
        observed_at: str | None = None,
    ) -> list[SignalWaitRecord]:
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM signal_waits
                WHERE status = 'ACTIVE' AND deadline_at <= ?
                ORDER BY deadline_at ASC
                """,
                (observed_at or utc_now(),),
            ).fetchall()
        return [self._signal_wait_from_row(row) for row in rows]

    def find_matching_signal_waits(
        self,
        *,
        task_id: str,
        provider: str,
        kind: ExternalEventKind,
        subject: str,
    ) -> list[SignalWaitRecord]:
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM signal_waits
                WHERE task_id = ? AND provider = ? AND event_kind = ?
                  AND subject = ? AND status = 'ACTIVE'
                ORDER BY created_at ASC
                """,
                (task_id, provider, kind.value, subject),
            ).fetchall()
        return [self._signal_wait_from_row(row) for row in rows]

    def find_active_signal_waits(
        self,
        *,
        provider: str,
        kind: ExternalEventKind,
        subject: str,
    ) -> list[SignalWaitRecord]:
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM signal_waits
                WHERE provider = ? AND event_kind = ? AND subject = ?
                  AND status = 'ACTIVE'
                ORDER BY created_at ASC
                """,
                (provider, kind.value, subject),
            ).fetchall()
        return [self._signal_wait_from_row(row) for row in rows]

    def find_signal_wait_satisfied_by(
        self,
        external_event_id: str,
    ) -> SignalWaitRecord | None:
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM signal_waits
                WHERE satisfied_by = ? AND status = 'SATISFIED'
                """,
                (external_event_id,),
            ).fetchone()
        return self._signal_wait_from_row(row) if row is not None else None

    def finish_signal_wait(
        self,
        *,
        wait_id: str,
        status: SignalWaitStatus,
        satisfied_by: str | None = None,
    ) -> SignalWaitRecord:
        if status not in {
            SignalWaitStatus.SATISFIED,
            SignalWaitStatus.EXPIRED,
            SignalWaitStatus.CANCELLED,
        }:
            raise ValidationError("Signal wait finish status is invalid.")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM signal_waits WHERE wait_id = ?",
                (wait_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Signal wait {wait_id!r} was not found.")
            if row["status"] == status.value:
                return self._signal_wait_from_row(row)
            if row["status"] != SignalWaitStatus.ACTIVE.value:
                raise ConcurrencyError("Only an active signal wait can be finished.")
            connection.execute(
                """
                UPDATE signal_waits
                SET status = ?, satisfied_by = ?
                WHERE wait_id = ? AND status = 'ACTIVE'
                """,
                (status.value, satisfied_by, wait_id),
            )
            updated = connection.execute(
                "SELECT * FROM signal_waits WHERE wait_id = ?",
                (wait_id,),
            ).fetchone()
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=None,
                kind="SIGNAL_WAIT_FINISHED",
                payload={
                    "wait_id": wait_id,
                    "status": status.value,
                    "satisfied_by": satisfied_by,
                },
            )
        return self._signal_wait_from_row(updated)

    def record_external_event(
        self,
        *,
        external_event_id: str,
        task_id: str,
        provider: str,
        kind: str,
        delivery_id: str,
        subject: str,
        facts: Mapping[str, Any],
        authenticated: bool,
        content_trust: str,
        status: ExternalEventStatus = ExternalEventStatus.RECEIVED,
        outcome_reason: str | None = None,
    ) -> tuple[ExternalEventRecord, bool]:
        dedupe_key = f"{provider}:{delivery_id}"
        safe_facts = SensitiveDataRedactor().redact(dict(facts))
        timestamp = utc_now()
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            try:
                connection.execute(
                    """
                    INSERT INTO external_events(
                        external_event_id, task_id, provider, event_kind,
                        delivery_id, dedupe_key, subject, facts_json,
                        authenticated, content_trust, status, outcome_reason,
                        received_at, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        external_event_id,
                        task_id,
                        provider,
                        kind,
                        delivery_id,
                        dedupe_key,
                        subject,
                        canonical_json(safe_facts),
                        int(authenticated),
                        content_trust,
                        status.value,
                        outcome_reason,
                        timestamp,
                        timestamp if status != ExternalEventStatus.RECEIVED else None,
                    ),
                )
                duplicate = False
            except sqlite3.IntegrityError:
                duplicate = True
            row = connection.execute(
                """
                SELECT * FROM external_events
                WHERE provider = ? AND delivery_id = ?
                """,
                (provider, delivery_id),
            ).fetchone()
            if row is None:
                raise ValidationError("External event could not be recorded.")
            if duplicate and (
                row["task_id"] != task_id
                or row["event_kind"] != kind
                or row["subject"] != subject
                or row["facts_json"] != canonical_json(safe_facts)
            ):
                raise ValidationError(
                    "An external delivery id was reused for different content."
                )
            if not duplicate:
                self._append_audit(
                    connection,
                    task_id=task_id,
                    run_id=None,
                    kind="EXTERNAL_EVENT_RECORDED",
                    payload={
                        "external_event_id": external_event_id,
                        "provider": provider,
                        "event_kind": kind,
                        "authenticated": authenticated,
                        "status": status.value,
                    },
                )
        return self._external_event_from_row(row), duplicate

    def finish_external_event(
        self,
        external_event_id: str,
        *,
        status: ExternalEventStatus,
        reason: str,
    ) -> ExternalEventRecord:
        if status not in {
            ExternalEventStatus.CONSUMED,
            ExternalEventStatus.IGNORED,
            ExternalEventStatus.REJECTED,
        }:
            raise ValidationError("External event finish status is invalid.")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM external_events WHERE external_event_id = ?",
                (external_event_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(
                    f"External event {external_event_id!r} was not found."
                )
            if row["status"] == status.value:
                return self._external_event_from_row(row)
            if row["status"] != ExternalEventStatus.RECEIVED.value:
                raise ConcurrencyError("External event is already finalized.")
            connection.execute(
                """
                UPDATE external_events
                SET status = ?, outcome_reason = ?, processed_at = ?
                WHERE external_event_id = ? AND status = 'RECEIVED'
                """,
                (status.value, reason, utc_now(), external_event_id),
            )
            updated = connection.execute(
                "SELECT * FROM external_events WHERE external_event_id = ?",
                (external_event_id,),
            ).fetchone()
            self._append_audit(
                connection,
                task_id=row["task_id"],
                run_id=None,
                kind="EXTERNAL_EVENT_FINISHED",
                payload={
                    "external_event_id": external_event_id,
                    "status": status.value,
                    "reason": reason,
                },
            )
        return self._external_event_from_row(updated)

    def list_external_events(self, task_id: str) -> list[ExternalEventRecord]:
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(
                """
                SELECT * FROM external_events
                WHERE task_id = ? ORDER BY received_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [self._external_event_from_row(row) for row in rows]

    def get_external_event_by_delivery(
        self,
        *,
        provider: str,
        delivery_id: str,
    ) -> ExternalEventRecord | None:
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM external_events
                WHERE provider = ? AND delivery_id = ?
                """,
                (provider, delivery_id),
            ).fetchone()
        return self._external_event_from_row(row) if row is not None else None

    def insert_event(
        self,
        connection: sqlite3.Connection,
        event: Event,
    ) -> tuple[bool, sqlite3.Row | None]:
        occurred_at = event.occurred_at or utc_now()
        payload_json = canonical_json(
            SensitiveDataRedactor().redact(dict(event.payload))
        )
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
        payload_json = canonical_json(
            SensitiveDataRedactor().redact(dict(payload))
        )
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

    def list_audit_page(
        self,
        *,
        task_id: str,
        limit: int,
        before_sequence: int | None = None,
    ) -> tuple[list[AuditEntry], int | None]:
        if limit < 1 or limit > 100:
            raise ValidationError("Audit page limit must be between 1 and 100.")
        query = "SELECT * FROM audit_log WHERE task_id = ?"
        parameters: list[Any] = [task_id]
        if before_sequence is not None:
            query += " AND sequence < ?"
            parameters.append(before_sequence)
        query += " ORDER BY sequence DESC LIMIT ?"
        parameters.append(limit + 1)
        with self.transaction() as connection:
            self._get_task_row(connection, task_id)
            rows = connection.execute(query, parameters).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = int(page[-1]["sequence"]) if has_more and page else None
        return [self._audit_from_row(row) for row in page], next_cursor

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
    def _get_worktree_row(
        connection: sqlite3.Connection,
        task_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM task_worktrees WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Worktree for task {task_id!r} was not found.")
        return row

    @staticmethod
    def _get_approval_row(
        connection: sqlite3.Connection,
        approval_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Approval {approval_id!r} was not found.")
        return row

    @staticmethod
    def _get_side_effect_row(
        connection: sqlite3.Connection,
        effect_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM side_effects WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Side effect {effect_id!r} was not found.")
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
    def _worktree_from_row(row: sqlite3.Row) -> WorktreeRecord:
        return WorktreeRecord(
            task_id=row["task_id"],
            repository_path=row["repository_path"],
            worktree_path=row["worktree_path"],
            branch_name=row["branch_name"],
            base_revision=row["base_revision"],
            state=WorktreeState(row["state"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            removed_at=row["removed_at"],
        )

    @staticmethod
    def _active_task_lease_from_row(row: sqlite3.Row) -> ActiveTaskLease:
        return ActiveTaskLease(
            task_id=row["task_id"],
            owner=row["owner"],
            acquired_at=row["acquired_at"],
            heartbeat_at=row["heartbeat_at"],
            expires_at=row["expires_at"],
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

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row["approval_id"],
            task_id=row["task_id"],
            action_type=row["action_type"] or row["requested_action"],
            action_hash=row["action_hash"],
            parameters=json.loads(row["parameters_json"]),
            risk_summary=row["risk_summary"],
            rollback_plan=row["rollback_plan"],
            status=row["status"],
            request_key=row["request_key"] or "",
            requested_at=row["requested_at"],
            expires_at=row["expires_at"] or "",
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            consumed_at=row["consumed_at"],
        )

    @staticmethod
    def _side_effect_from_row(row: sqlite3.Row) -> SideEffectRecord:
        return SideEffectRecord(
            effect_id=row["effect_id"],
            task_id=row["task_id"],
            approval_id=row["approval_id"],
            idempotency_key=row["idempotency_key"],
            logical_step=row["logical_step"],
            action_type=row["action_type"],
            parameters_hash=row["parameters_hash"],
            status=SideEffectStatus(row["status"]),
            external_result_id=row["external_result_id"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _signal_wait_from_row(row: sqlite3.Row) -> SignalWaitRecord:
        return SignalWaitRecord(
            wait_id=row["wait_id"],
            task_id=row["task_id"],
            provider=row["provider"],
            kind=ExternalEventKind(row["event_kind"]),
            subject=row["subject"],
            condition=json.loads(row["condition_json"]),
            timeout_behavior=row["timeout_behavior"],
            status=SignalWaitStatus(row["status"]),
            created_at=row["created_at"],
            deadline_at=row["deadline_at"],
            satisfied_by=row["satisfied_by"],
        )

    @staticmethod
    def _external_event_from_row(row: sqlite3.Row) -> ExternalEventRecord:
        return ExternalEventRecord(
            external_event_id=row["external_event_id"],
            task_id=row["task_id"],
            provider=row["provider"],
            kind=row["event_kind"],
            delivery_id=row["delivery_id"],
            dedupe_key=row["dedupe_key"],
            subject=row["subject"],
            facts=json.loads(row["facts_json"]),
            authenticated=bool(row["authenticated"]),
            content_trust=row["content_trust"],
            status=ExternalEventStatus(row["status"]),
            outcome_reason=row["outcome_reason"],
            received_at=row["received_at"],
            processed_at=row["processed_at"],
        )
