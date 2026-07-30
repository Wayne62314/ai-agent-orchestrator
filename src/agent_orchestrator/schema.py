"""SQLite schema migrations."""

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        objective TEXT NOT NULL,
        workspace_path TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN (
            'DRAFT', 'READY', 'RUNNING', 'WAITING_FOR_SIGNAL',
            'WAITING_FOR_APPROVAL', 'VERIFYING', 'NEEDS_ATTENTION',
            'SUCCEEDED', 'CANCELLED'
        )),
        permissions_policy_json TEXT NOT NULL,
        acceptance_policy_json TEXT NOT NULL,
        retry_policy_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0)
    );

    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        engine TEXT NOT NULL,
        state TEXT NOT NULL,
        input_checkpoint_id TEXT,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        exit_reason TEXT,
        result_summary TEXT,
        UNIQUE(task_id, attempt)
    );

    CREATE TABLE IF NOT EXISTS checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
        run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
        workspace_revision TEXT,
        payload_path TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(task_id, sequence)
    );

    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
        event_type TEXT NOT NULL,
        source TEXT NOT NULL,
        dedupe_key TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        processed_at TEXT,
        outcome TEXT,
        outcome_reason TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_events_task_occurred
        ON events(task_id, occurred_at);

    CREATE TABLE IF NOT EXISTS approvals (
        approval_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
        requested_action TEXT NOT NULL,
        action_hash TEXT NOT NULL,
        risk_summary TEXT NOT NULL,
        status TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        decided_at TEXT,
        decided_by TEXT,
        expires_at TEXT
    );

    CREATE TABLE IF NOT EXISTS verifications (
        verification_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
        run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
        check_name TEXT NOT NULL,
        required INTEGER NOT NULL CHECK (required IN (0, 1)),
        status TEXT NOT NULL,
        exit_code INTEGER,
        summary TEXT,
        log_path TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS audit_log (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        audit_id TEXT NOT NULL UNIQUE,
        task_id TEXT REFERENCES tasks(task_id) ON DELETE RESTRICT,
        run_id TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        previous_hash TEXT NOT NULL,
        entry_hash TEXT NOT NULL UNIQUE
    );

    CREATE INDEX IF NOT EXISTS idx_audit_task_sequence
        ON audit_log(task_id, sequence);
    """,
    """
    ALTER TABLE runs ADD COLUMN provider_run_id TEXT;
    ALTER TABLE runs ADD COLUMN thread_id TEXT;
    ALTER TABLE runs ADD COLUMN lease_owner TEXT;
    ALTER TABLE runs ADD COLUMN lease_expires_at TEXT;
    ALTER TABLE runs ADD COLUMN heartbeat_at TEXT;

    CREATE INDEX IF NOT EXISTS idx_runs_active_lease
        ON runs(state, lease_expires_at);
    CREATE INDEX IF NOT EXISTS idx_runs_thread
        ON runs(thread_id);

    ALTER TABLE checkpoints ADD COLUMN status TEXT NOT NULL DEFAULT 'READY';
    ALTER TABLE checkpoints ADD COLUMN error TEXT;

    CREATE INDEX IF NOT EXISTS idx_checkpoints_ready
        ON checkpoints(task_id, status, sequence);
    """,
    """
    ALTER TABLE verifications ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE verifications ADD COLUMN command_json TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE verifications ADD COLUMN timed_out INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE verifications ADD COLUMN output_truncated INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE verifications ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE verifications ADD COLUMN started_at TEXT NOT NULL DEFAULT '';
    ALTER TABLE verifications ADD COLUMN ended_at TEXT NOT NULL DEFAULT '';

    CREATE INDEX IF NOT EXISTS idx_verifications_task_attempt
        ON verifications(task_id, attempt, created_at);
    """,
)
