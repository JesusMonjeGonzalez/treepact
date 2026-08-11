-- Migration 001: initial authoritative schema per STATE_AND_DATA_MODEL.md.
-- All timestamps are UTC RFC 3339 strings. IDs are sortable random IDs.

CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    canonical_root TEXT NOT NULL UNIQUE,
    git_common_dir_identity TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE pact_versions (
    pact_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    schema_version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, sha256)
);

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    operator_text TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE application_commands (
    command_id TEXT PRIMARY KEY,
    command_type TEXT NOT NULL,
    idempotency_key TEXT,
    actor TEXT NOT NULL,
    run_id TEXT,
    request_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(command_type, idempotency_key)
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    pact_id TEXT NOT NULL REFERENCES pact_versions(pact_id),
    state TEXT NOT NULL,
    runtime_id TEXT NOT NULL,
    provider_id TEXT,
    model_profile TEXT,
    base_commit TEXT NOT NULL,
    worktree_id TEXT,
    assurance_level TEXT NOT NULL,
    attempt_limit INTEGER NOT NULL,
    turn_limit INTEGER NOT NULL,
    deadline_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_reason_code TEXT,
    decision TEXT
);

CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    attempt_number INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    termination_code TEXT,
    UNIQUE(run_id, attempt_number)
);

CREATE TABLE workspaces (
    worktree_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    path TEXT NOT NULL UNIQUE,
    base_commit TEXT NOT NULL,
    initial_tree_digest TEXT NOT NULL,
    current_tree_digest TEXT,
    lock_owner TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE runtime_sessions (
    session_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    external_session_id TEXT,
    runtime_version TEXT,
    state TEXT NOT NULL,
    coverage_started_at TEXT,
    coverage_gap INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL
);

CREATE TABLE model_invocations (
    invocation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
    turn INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    model_ref TEXT NOT NULL,
    privacy_class TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    input_units INTEGER,
    output_units INTEGER,
    latency_ms INTEGER,
    outcome TEXT NOT NULL,
    error_code TEXT
);

CREATE TABLE tool_proposals (
    proposal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
    turn INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    arguments_json TEXT NOT NULL,
    arguments_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE policy_decisions (
    decision_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES tool_proposals(proposal_id),
    policy_sha256 TEXT NOT NULL,
    outcome TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE tool_executions (
    execution_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES tool_proposals(proposal_id),
    state TEXT NOT NULL,
    process_pid INTEGER,
    process_group INTEGER,
    started_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    result_json TEXT,
    error_code TEXT
);

CREATE TABLE checks (
    check_execution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    attempt_id TEXT REFERENCES attempts(attempt_id),
    check_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    argv_sha256 TEXT NOT NULL,
    cwd_relative TEXT NOT NULL,
    input_snapshot_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    stdout_artifact_id TEXT,
    stderr_artifact_id TEXT,
    result_artifact_id TEXT
);

CREATE TABLE gates (
    gate_result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    gate_id TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    UNIQUE(run_id, gate_id, policy_sha256)
);

CREATE TABLE resource_leases (
    lease_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    provider TEXT NOT NULL,
    profile TEXT NOT NULL,
    estimated_memory_mb INTEGER NOT NULL,
    state TEXT NOT NULL,
    granted_at TEXT,
    expires_at TEXT,
    released_at TEXT
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    redaction_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sha256, kind)
);

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    pact_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    previous_event_sha256 TEXT,
    event_sha256 TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);

CREATE INDEX idx_events_run_sequence ON events(run_id, sequence);
CREATE INDEX idx_events_run_type ON events(run_id, event_type);
CREATE INDEX idx_runs_state ON runs(state);
CREATE INDEX idx_runs_project ON runs(task_id);
CREATE INDEX idx_checks_run ON checks(run_id);
CREATE INDEX idx_gates_run ON gates(run_id);
CREATE INDEX idx_attempts_run ON attempts(run_id);
CREATE INDEX idx_tool_proposals_run ON tool_proposals(run_id);
CREATE INDEX idx_leases_run ON resource_leases(run_id);
CREATE INDEX idx_commands_key ON application_commands(command_type, idempotency_key);
