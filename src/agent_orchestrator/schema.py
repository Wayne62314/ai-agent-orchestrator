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
    """
    ALTER TABLE approvals ADD COLUMN action_type TEXT NOT NULL DEFAULT '';
    ALTER TABLE approvals ADD COLUMN parameters_json TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE approvals ADD COLUMN rollback_plan TEXT NOT NULL DEFAULT '';
    ALTER TABLE approvals ADD COLUMN request_key TEXT;
    ALTER TABLE approvals ADD COLUMN consumed_at TEXT;

    CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_request_key
        ON approvals(request_key);
    CREATE INDEX IF NOT EXISTS idx_approvals_task_status
        ON approvals(task_id, status, requested_at);
    CREATE INDEX IF NOT EXISTS idx_approvals_action_hash
        ON approvals(task_id, action_hash, status);

    CREATE TABLE IF NOT EXISTS side_effects (
        effect_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
        approval_id TEXT REFERENCES approvals(approval_id) ON DELETE RESTRICT,
        idempotency_key TEXT NOT NULL UNIQUE,
        logical_step TEXT NOT NULL,
        action_type TEXT NOT NULL,
        parameters_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'PENDING', 'SUCCEEDED', 'UNKNOWN', 'FAILED'
        )),
        external_result_id TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_side_effects_task_status
        ON side_effects(task_id, status, created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_waits (
        wait_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
        provider TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        condition_json TEXT NOT NULL,
        timeout_behavior TEXT NOT NULL CHECK (timeout_behavior IN ('attention')),
        status TEXT NOT NULL CHECK (status IN (
            'ACTIVE', 'SATISFIED', 'EXPIRED', 'CANCELLED'
        )),
        created_at TEXT NOT NULL,
        deadline_at TEXT NOT NULL,
        satisfied_by TEXT REFERENCES external_events(external_event_id)
    );

    CREATE INDEX IF NOT EXISTS idx_signal_waits_active
        ON signal_waits(status, deadline_at);
    CREATE INDEX IF NOT EXISTS idx_signal_waits_match
        ON signal_waits(task_id, provider, event_kind, subject, status);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_waits_one_active
        ON signal_waits(task_id, provider, event_kind, subject)
        WHERE status = 'ACTIVE';

    CREATE TABLE IF NOT EXISTS external_events (
        external_event_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
        provider TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        delivery_id TEXT NOT NULL,
        dedupe_key TEXT NOT NULL UNIQUE,
        subject TEXT NOT NULL,
        facts_json TEXT NOT NULL,
        authenticated INTEGER NOT NULL CHECK (authenticated IN (0, 1)),
        content_trust TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'RECEIVED', 'CONSUMED', 'IGNORED', 'REJECTED'
        )),
        outcome_reason TEXT,
        received_at TEXT NOT NULL,
        processed_at TEXT,
        UNIQUE(provider, delivery_id)
    );

    CREATE INDEX IF NOT EXISTS idx_external_events_task_received
        ON external_events(task_id, received_at);
    """,
    """
    CREATE TABLE tasks_v6 (
        task_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        objective TEXT NOT NULL,
        workspace_path TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN (
            'DRAFT', 'READY', 'RUNNING', 'PAUSED', 'WAITING_FOR_SIGNAL',
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

    INSERT INTO tasks_v6(
        task_id, title, objective, workspace_path, state,
        permissions_policy_json, acceptance_policy_json, retry_policy_json,
        created_at, updated_at, version
    )
    SELECT
        task_id, title, objective, workspace_path, state,
        permissions_policy_json, acceptance_policy_json, retry_policy_json,
        created_at, updated_at, version
    FROM tasks;

    DROP TABLE tasks;
    ALTER TABLE tasks_v6 RENAME TO tasks;

    CREATE TABLE task_worktrees (
        task_id TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
        repository_path TEXT NOT NULL,
        worktree_path TEXT NOT NULL UNIQUE,
        branch_name TEXT NOT NULL,
        base_revision TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN (
            'CREATING', 'ACTIVE', 'RETAINED', 'NEEDS_ATTENTION', 'REMOVED'
        )),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        removed_at TEXT
    );

    CREATE INDEX idx_task_worktrees_repository
        ON task_worktrees(repository_path, state);

    CREATE TABLE active_task_lease (
        slot INTEGER PRIMARY KEY CHECK (slot = 1),
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id) ON DELETE RESTRICT,
        owner TEXT NOT NULL,
        acquired_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE app_settings (
        setting_key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
)
