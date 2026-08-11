# State and Data Model

## Principles

- SQLite stores current authoritative state.
- Events provide an append-only operational history but do not replace relational state.
- Large outputs live in a content-addressed artifact store.
- One application command uses one transaction for its database effects.
- External process effects cannot share a database transaction and therefore use explicit started, completed, uncertain, and reconciled states.
- Unknown future schema versions fail without writing.
- Timestamps use UTC and RFC 3339 serialization.
- IDs are locally generated UUIDv7 or another sortable random identifier selected in an ADR.

## Run state machine

```text
created -> preparing -> ready -> running -> verifying -> accepted
              |          |         |           |-----> rejected
              |          |         |           `-----> needs_review
              |          |         |
              |          |         |-----> cancelled
              |          |         |-----> failed
              |          |         |-----> infrastructure_error
              |          |         `-----> interrupted -> running
              |          |
              |          `-----> waiting_resource -> ready | cancelled
              |
              `-----> rejected | failed | infrastructure_error

Any non-terminal state may enter `failed` or `infrastructure_error` when appropriate. `cancelled` is terminal and never resumes. Manual-review requirements discovered during execution terminate as `needs_review`; version 1 has no resumable approval state because high-impact actions are unavailable rather than approval-gated.

A cancellation request is valid from `preparing`, `waiting_resource`, `ready`, `running`, `interrupted`, or `verifying`. Cancellation during a non-interruptible database commit completes that transaction first and then transitions. Terminal states reject cancellation as a state conflict.
```

### Terminal states

- `accepted`
- `rejected`
- `needs_review`
- `cancelled`
- `failed`
- `infrastructure_error`

Terminal state does not imply worktree deletion.

## Attempt state machine

```text
created -> planning -> acting -> checking -> passed
                              |          -> failed
                              |          -> exhausted
                              -> cancelled
                              -> interrupted
```

An attempt belongs to exactly one run. Attempt numbers are monotonic and cannot exceed the immutable Pact limit.

## Check state machine

```text
declared -> queued -> started -> passed
                            -> failed
                            -> timed_out
                            -> cancelled
                            -> infrastructure_error
                            -> blocked_input_changed
```

Only TreePact can create `started` and terminal check evidence. A runtime request creates or references `queued` but cannot set the result.

## Tool proposal state

```text
proposed -> allowed -> started -> completed
         |                   -> failed
         |                   -> uncertain
         -> denied
         -> stale
```

`uncertain` is used when TreePact cannot prove whether an external effect completed. An uncertain mutating effect blocks automatic resume.

## Resource lease state

```text
requested -> granted -> active -> released
          -> queued            -> expired
          -> denied            -> revoked
```

## Core tables

### `schema_migrations`

```text
version INTEGER PRIMARY KEY
name TEXT NOT NULL
applied_at TEXT NOT NULL
script_sha256 TEXT NOT NULL
```

### `projects`

```text
project_id TEXT PRIMARY KEY
canonical_root TEXT NOT NULL UNIQUE
git_common_dir_identity TEXT NOT NULL
display_name TEXT NOT NULL
created_at TEXT NOT NULL
last_seen_at TEXT NOT NULL
```

Paths are local operational metadata and are redacted from exported public reports by default.

### `pact_versions`

```text
pact_id TEXT PRIMARY KEY
project_id TEXT NOT NULL
schema_version INTEGER NOT NULL
sha256 TEXT NOT NULL
canonical_json TEXT NOT NULL
source_path TEXT NOT NULL
created_at TEXT NOT NULL
UNIQUE(project_id, sha256)
```

### `tasks`

```text
task_id TEXT PRIMARY KEY
project_id TEXT NOT NULL
operator_text TEXT NOT NULL
mode TEXT NOT NULL
created_at TEXT NOT NULL
```

Task text is sensitive internal content. Export requires explicit inclusion.

### `application_commands`

```text
command_id TEXT PRIMARY KEY
command_type TEXT NOT NULL
idempotency_key TEXT
actor TEXT NOT NULL
run_id TEXT
request_sha256 TEXT NOT NULL
state TEXT NOT NULL
result_json TEXT
error_code TEXT
created_at TEXT NOT NULL
completed_at TEXT
UNIQUE(command_type, idempotency_key)
```

Commands without an idempotency key are never replayed automatically. A repeated idempotency key with a different request digest is a state conflict.

`command_id` identifies one persisted command attempt. `idempotency_key` identifies the operator intention that may be retried safely. Replay behavior for the same command type, key, and request digest is:

| Existing state | Replay result |
|---|---|
| `requested` or `running` | Return state conflict/in-progress; do not execute again |
| `completed` | Return the stored result without new effects |
| `failed` before any external effect | Return the stored failure; an explicit retry creates a new intention and key |
| `failed` after a known rolled-back effect | Return the stored failure; an explicit retry creates a new intention and key |
| `uncertain` or interrupted after an external effect began | Refuse replay and require reconciliation or human review |
| Missing `result_json` in a terminal state | Treat as storage corruption/infrastructure error |

Application commands persist `requested` before work, `running` before an external effect, and a terminal state afterwards. External tool effects also use their own proposal and execution records. No automatic recovery converts `running` to retryable without reconciling those records.

### `runs`

```text
run_id TEXT PRIMARY KEY
task_id TEXT NOT NULL
pact_id TEXT NOT NULL
state TEXT NOT NULL
runtime_id TEXT NOT NULL
provider_id TEXT
model_profile TEXT
base_commit TEXT NOT NULL
worktree_id TEXT
assurance_level TEXT NOT NULL
attempt_limit INTEGER NOT NULL
turn_limit INTEGER NOT NULL
deadline_at TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
terminal_reason_code TEXT
decision TEXT
```

### `attempts`

```text
attempt_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
attempt_number INTEGER NOT NULL
state TEXT NOT NULL
started_at TEXT
finished_at TEXT
termination_code TEXT
UNIQUE(run_id, attempt_number)
```

### `workspaces`

```text
worktree_id TEXT PRIMARY KEY
run_id TEXT NOT NULL UNIQUE
path TEXT NOT NULL UNIQUE
base_commit TEXT NOT NULL
initial_tree_digest TEXT NOT NULL
current_tree_digest TEXT
lock_owner TEXT NOT NULL
state TEXT NOT NULL
created_at TEXT NOT NULL
removed_at TEXT
```

### `runtime_sessions`

```text
session_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
adapter_id TEXT NOT NULL
adapter_version TEXT NOT NULL
external_session_id TEXT
runtime_version TEXT
state TEXT NOT NULL
coverage_started_at TEXT
coverage_gap INTEGER NOT NULL DEFAULT 0
metadata_json TEXT NOT NULL
```

### `model_invocations`

```text
invocation_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
attempt_id TEXT NOT NULL
turn INTEGER NOT NULL
provider_id TEXT NOT NULL
model_ref TEXT NOT NULL
privacy_class TEXT NOT NULL
started_at TEXT NOT NULL
finished_at TEXT
input_units INTEGER
output_units INTEGER
latency_ms INTEGER
outcome TEXT NOT NULL
error_code TEXT
```

Full prompts and responses are not stored by default. Optional diagnostic capture requires a separate redacted artifact and explicit configuration.

### `tool_proposals`

```text
proposal_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
attempt_id TEXT NOT NULL
turn INTEGER NOT NULL
tool_name TEXT NOT NULL
schema_version INTEGER NOT NULL
arguments_json TEXT NOT NULL
arguments_sha256 TEXT NOT NULL
state TEXT NOT NULL
created_at TEXT NOT NULL
```

Sensitive tool arguments are stored redacted or as digest plus minimal reconstruction metadata.

### `policy_decisions`

```text
decision_id TEXT PRIMARY KEY
proposal_id TEXT NOT NULL UNIQUE
policy_sha256 TEXT NOT NULL
outcome TEXT NOT NULL
rule_id TEXT NOT NULL
reason_code TEXT NOT NULL
details_json TEXT NOT NULL
created_at TEXT NOT NULL
```

### `tool_executions`

```text
execution_id TEXT PRIMARY KEY
proposal_id TEXT NOT NULL UNIQUE
state TEXT NOT NULL
process_pid INTEGER
process_group INTEGER
started_at TEXT
finished_at TEXT
exit_code INTEGER
result_json TEXT
error_code TEXT
```

### `checks`

```text
check_execution_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
attempt_id TEXT
check_id TEXT NOT NULL
phase TEXT NOT NULL
argv_sha256 TEXT NOT NULL
cwd_relative TEXT NOT NULL
input_snapshot_sha256 TEXT NOT NULL
state TEXT NOT NULL
started_at TEXT
finished_at TEXT
exit_code INTEGER
stdout_artifact_id TEXT
stderr_artifact_id TEXT
result_artifact_id TEXT
```

### `gates`

```text
gate_result_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
gate_id TEXT NOT NULL
policy_sha256 TEXT NOT NULL
state TEXT NOT NULL
reason_code TEXT NOT NULL
evidence_refs_json TEXT NOT NULL
calculated_at TEXT NOT NULL
UNIQUE(run_id, gate_id, policy_sha256)
```

### `resource_leases`

```text
lease_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
provider TEXT NOT NULL
profile TEXT NOT NULL
estimated_memory_mb INTEGER NOT NULL
state TEXT NOT NULL
granted_at TEXT
expires_at TEXT
released_at TEXT
```

### `artifacts`

```text
artifact_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
kind TEXT NOT NULL
sha256 TEXT NOT NULL
relative_path TEXT NOT NULL
size_bytes INTEGER NOT NULL
media_type TEXT NOT NULL
redaction_state TEXT NOT NULL
created_at TEXT NOT NULL
UNIQUE(run_id, sha256, kind)
```

### `events`

```text
event_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
sequence INTEGER NOT NULL
event_type TEXT NOT NULL
schema_version INTEGER NOT NULL
occurred_at TEXT NOT NULL
actor TEXT NOT NULL
correlation_id TEXT NOT NULL
causation_id TEXT
pact_sha256 TEXT NOT NULL
payload_json TEXT NOT NULL
previous_event_sha256 TEXT
event_sha256 TEXT NOT NULL
UNIQUE(run_id, sequence)
```

## Event catalog

Each event type and schema version has a dedicated strict payload schema registered in code and exported under `schemas/events/` during M2. The generic event envelope schema validates only common metadata. Unknown event types or payload fields cannot enter the authoritative ledger.

Events use exact state-specific names rather than overloading `failed` with timeout, cancellation, or infrastructure semantics. Each transition event payload contains `from_state`, `to_state`, and a stable `reason_code` where applicable.

### Run lifecycle

- `run.created`
- `run.preparing`
- `run.ready`
- `run.waiting_resource`
- `run.started`
- `run.verifying`
- `run.interrupted`
- `run.resumed`
- `run.cancel_requested`
- `run.cancelled`
- `run.accepted`
- `run.rejected`
- `run.needs_review`
- `run.failed`
- `run.infrastructure_error`

### Pact and workspace

- `pact.loaded`
- `pact.compiled`
- `workspace.created`
- `workspace.locked`
- `workspace.change_signalled`
- `workspace.reconciled`
- `workspace.invalidated`
- `workspace.removed`

### Attempts and runtime

- `attempt.created`
- `attempt.started`
- `attempt.planning`
- `attempt.acting`
- `attempt.checking`
- `attempt.failed`
- `attempt.completed`
- `attempt.exhausted`
- `attempt.interrupted`
- `attempt.cancelled`
- `runtime.session_started`
- `runtime.permission_requested`
- `runtime.tool_reported`
- `runtime.coverage_gap`
- `runtime.idle`
- `runtime.failed`
- `runtime.session_finished`

### Models and resources

- `resource.requested`
- `resource.granted`
- `resource.activated`
- `resource.queued`
- `resource.denied`
- `resource.released`
- `resource.expired`
- `resource.revoked`
- `resource.pressure_detected`
- `model.invocation_started`
- `model.invocation_finished`
- `model.invocation_failed`

### Tools, checks, and gates

- `tool.proposed`
- `policy.allowed`
- `policy.denied`
- `tool.started`
- `tool.finished`
- `tool.failed`
- `tool.uncertain`
- `tool.stale`
- `check.queued`
- `check.started`
- `check.passed`
- `check.failed`
- `check.timed_out`
- `check.cancelled`
- `check.infrastructure_error`
- `check.blocked`
- `gate.calculated`

### Evidence

- `artifact.created`
- `artifact.redacted`
- `report.generated`
- `evidence.verified`
- `evidence.exported`

## Event hash

The hash input is the UTF-8 bytes of canonical event JSON containing every envelope field except `event_sha256`. `previous_event_sha256` is included exactly once as an ordinary field and is `null` for sequence 1. Canonical JSON uses sorted keys, no insignificant whitespace, UTF-8 without ASCII escaping, and prohibits floating-point payload values. The digest is `SHA-256(canonical_json_bytes)`. Canonicalization is versioned by `schema_version`.

The chain detects accidental corruption and later local modification. It is not described as tamper-proof against an attacker controlling the account or database.

## Decision Bundle

Machine-readable manifest:

```text
bundle_schema_version
run_id
project_id
repository_identity
base_commit
workspace_state
final_tree_digest
final_tree_reason_code
pact_sha256
runtime
assurance_level
attempts
checks
gates
policy_denials
resource_summary
decision
artifacts
event_chain_head
generated_at
```

The human report is a projection of this manifest and evidence. It must not add unsupported claims.

For a terminal run that ends before worktree creation, `workspace_state` is `not_created`, `final_tree_digest` is `null`, and `final_tree_reason_code` explains why no final tree exists. Such a bundle still contains Pact, task, resource, state, and event evidence.

## Retention

Default proposed policy:

- Worktrees: retained until explicit cleanup.
- Decision Bundles: retained indefinitely in version 1.
- Check stdout/stderr referenced by a Decision Bundle: retained with that bundle in version 1.
- Operational logs: 14 days.
- Full diagnostic model content: disabled.
- Temporary HOME and process files: removed after terminal run state.
- Evidence purge: unavailable in schema and CLI version 1; retention reduction requires a later ledger-preserving design.

Retention of operational logs and unreferenced temporary outputs is configurable locally. Decision Bundles, their referenced artifacts, and event history cannot be purged in version 1. Consequently, `evidence --verify-hashes` never encounters an intentionally expired referenced artifact.

## Backup and recovery contract

Version 1 backup is an offline, consistent TreePact state package used for migration and final recovery verification. It contains:

- SQLite backup created through the SQLite backup API or an exclusive stopped-state copy;
- artifact manifest with every retained artifact digest and size;
- copied artifact files;
- schema and application versions;
- event-chain head per run;
- package manifest digest;
- creation timestamp and source data-root identity.

Backup rules:

- write into a new temporary directory;
- complete SQLite and artifact copy before writing the final manifest;
- fsync files and directory where supported;
- atomically rename to the final backup path;
- reject symlinks and paths outside the selected backup root;
- mark incomplete temporary packages invalid;
- never include provider credentials or temporary run HOME directories.

Restore rules:

- restore only while no TreePact process owns the target data root;
- restore into a new empty directory;
- verify manifest, database schema, event-chain heads, artifact counts, sizes, and digests;
- never merge a restore into live state;
- switch data roots only after full verification;
- preserve the failed or prior data root for manual recovery;
- report missing artifacts as recovery failure, not partial success.

The initial CLI need not expose general user backup commands. M9 may use an internal recovery harness against a dedicated test data root. A user-facing backup command requires a later CLI and retention decision.

## Migration policy

- Forward-only numbered migrations.
- Migration script digest recorded.
- Backup database before a migration that changes existing structures.
- Transactional migration where SQLite allows it.
- Copy-and-swap for destructive table changes.
- Refuse newer schema without writes.
- No in-place downgrade.
- Restore prior backup for rollback.
- External bundle schemas remain independently versioned.
