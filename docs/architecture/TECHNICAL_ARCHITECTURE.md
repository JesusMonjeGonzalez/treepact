# Technical Architecture

## Architectural style

TreePact is a modular monolith with explicit internal boundaries and replaceable external adapters. It is not a collection of services.

The dependency direction is:

```text
CLI and future clients
        |
        v
Application commands and queries
        |
        v
Domain and policy
        |
        v
Ports defined by TreePact
        |
        v
Git, SQLite, filesystem, models, and runtime adapters
```

Domain modules do not import Typer, HTTPX, SQLite, Git, provider SDKs, OpenCode, Claude Code, local gateway, or VS Code.

## Components

### CLI

Parses operator intent, displays progress, and calls application commands. It does not manipulate the database or worktree directly.

### Application service

Owns transaction boundaries and coordinates domain operations. Each mutating command receives a command ID for correlation and idempotency.

The command ID is unique per persisted attempt. An optional idempotency key represents a replayable operator intention. The data model defines replay outcomes; command IDs themselves are never reused.

### Run engine

Drives state transitions, attempts, model turns, cancellation, verification, and finalization. It never executes a runtime tool without the tool broker.

### Pact compiler

Loads `.treepact.yaml`, validates strict schema, resolves canonical paths, compiles path and action rules, and produces an immutable Pact snapshot.

### Policy engine

Evaluates typed actions against the compiled Pact, current run state, actor, assurance level, data classification, and resource state. Decisions are deterministic and persisted.

### Workspace supervisor

Discovers the repository, captures the base commit, creates and locks the worktree, tracks Git state, and removes the worktree only through explicit cleanup.

### Tool broker

Exposes a small typed capability surface to runtimes. It validates inputs, requests a policy decision, performs the action through a trusted adapter, and records observations.

### Check executor

Executes only checks declared by ID in the Pact. It resolves an exact argv, uses a sanitized environment, captures process identity and output, enforces timeout, and records the exit result.

Pact compilation applies semantic command validation beyond JSON Schema. Shell executables and shell `-c` forms are rejected. Interpreter inline-evaluation flags are rejected while normal module or script execution may be allowed. TreePact always uses exec-style argv; metacharacters in ordinary arguments are literal and never parsed as shell syntax.

### Runtime adapter

Turns a task and observations into typed tool proposals. The native loop is first. OpenCode and Claude Code are later adapters.

### Model provider

Provides inference to the native runtime. The initial implementation supports a minimal OpenAI-compatible HTTP contract and local gateway configuration.

### Resource scheduler

Serializes mutating runs, observes memory pressure, grants local leases, and coordinates optional local gateway resource leases.

### Evidence service

Writes append-only events, stores artifacts by digest, captures the final patch and check outputs, and generates Decision Bundles.

### Storage

SQLite stores authoritative current state and event metadata. Filesystem storage contains potentially large artifacts.

## Runtime process model

### Initial mode

One foreground TreePact process owns one run. Check commands are child process groups. Model calls are HTTP requests made by the parent.

### Cancellation

Cancellation is cooperative at the run loop and forceful for recorded process groups:

1. Persist cancellation request.
2. Stop issuing model calls and tools.
3. Send termination to the active process group.
4. Wait for a bounded grace period.
5. Force kill the same recorded process group if necessary.
6. Persist child exit evidence.
7. Mark the run cancelled.

TreePact never searches broadly for similarly named processes to kill.

### Future daemon

A daemon may be introduced behind a local API after foreground use proves insufficient. The daemon owns runs; CLI, desktop, and editor integrations become clients.

Preferred future transport:

- Unix domain socket on macOS for local clients.
- Versioned HTTP/JSON semantics over that socket or a narrow loopback endpoint.
- Per-installation authentication if loopback TCP is used.
- Server-sent events or a local event stream for progress.

The SQLite file remains private to the daemon. Clients never query it directly.

## Tool protocol

Every proposal contains:

```text
proposal_id
run_id
attempt_id
turn
tool_name
schema_version
arguments
runtime_session_id
```

Every decision contains:

```text
decision_id
proposal_id
policy_hash
outcome: allow | deny
rule_id
reason_code
human_message
```

Every observation contains:

```text
execution_id
proposal_id
started_at
finished_at
outcome
structured_result
artifact_refs
redacted_message
```

Tools never accept host-absolute paths from a runtime. Paths are repository-relative and canonicalized by TreePact.

## Initial tool set

### `list_files`

- Input: relative path, optional safe glob, bounded result count.
- Effect: read-only.
- Constraints: readable scope, no `.git`, no symlink escape.

### `read_file`

- Input: relative path, byte or line range.
- Effect: read-only.
- Constraints: maximum bytes, binary rejection unless explicitly supported, secret classification.

### `search_text`

- Input: expression, path scope, maximum matches.
- Effect: read-only.
- Implementation: direct process call to a fixed search binary or pure Python adapter; runtime never supplies command-line flags.

### `apply_patch`

- Input: unified diff and expected workspace revision.
- Effect: write inside worktree.
- Constraints: path policy, no `.git`, no mode changes unless allowed, `git apply --check`, final path reconciliation.

### `run_check`

- Input: declared check ID.
- Effect: executes fixed argv from Pact.
- Constraints: cannot override command, cwd, timeout, environment, or the Pact's check-process network requirement.

### `finish`

- Input: runtime summary and claimed status.
- Effect: ends reasoning only.
- Constraint: claimed status is not the TreePact decision.

## Provider protocol

The provider receives a bounded request containing:

- system policy summary;
- task;
- immutable Pact capability summary;
- repository context explicitly read through tools;
- prior typed observations;
- allowed tool schemas;
- token and turn budget.

The provider returns:

- one or more typed tool proposals if parallel read calls are explicitly supported;
- a final narrative;
- usage metadata when available;
- provider error.

The initial implementation is non-streaming to simplify cancellation, logging, redaction, and reproducibility. Streaming may be added later without changing domain semantics.

## Git model

TreePact uses Git through exact argv arrays. It never invokes a shell.

Allowed supervisor operations include:

- repository discovery;
- resolve HEAD and root;
- worktree add, list, lock, unlock, and remove;
- status porcelain;
- diff and diff binary metadata;
- apply check and apply;
- hash-object or tree calculation when needed.

The runtime has no Git tool. It cannot commit, switch branches, modify refs, push, fetch, merge, or rewrite history through TreePact.

## Worktree lifecycle

1. Resolve and register repository root.
2. Reject unresolved Git operations such as merge or rebase.
3. Capture base commit and initial status.
4. Create a detached worktree in TreePact storage.
5. Lock the worktree with the run ID.
6. Snapshot Pact and relevant executable inputs.
7. Run attempts.
8. Reconcile final status and patch.
9. Keep the worktree after completion for human inspection.
10. Remove only through explicit cleanup after evidence is durable.

Untracked files from the original working tree are not copied automatically.

## Native macOS execution lanes

### Portable lane

For compatible Python and JVM checks, a future container adapter may provide stronger process and network isolation. It is optional.

### Native trusted-harness lane

Required for Xcode, AppKit, AVFoundation, Metal, simulators, and some KMP workflows.

Controls:

- named commands;
- sanitized environment;
- temporary HOME;
- no inherited credential agents;
- fixed cwd;
- process group timeout;
- script and build-config hashes;
- explicit resource budget;
- no strong sandbox claim.

Future stronger isolation may use a dedicated macOS user or Apple Silicon VM. `sandbox-exec` is not selected as a product security foundation.

## Configuration

Precedence from lowest to highest:

1. Built-in safe defaults.
2. User configuration in `~/.config/treepact/config.toml`.
3. Repository `.treepact.yaml` for repository capabilities.
4. Explicit safe CLI options such as mode and runtime.

A higher layer may reduce authority but may not expand beyond the Pact. CLI options cannot add paths, commands, network, or publication actions.

Secrets are not stored in configuration. Provider credentials, if remote providers are later supported, use macOS Keychain or provider-owned authentication.

## Error taxonomy

- `ConfigurationError`
- `PactValidationError`
- `RepositoryError`
- `WorkspaceError`
- `PolicyDenied`
- `CheckFailed`
- `RuntimeError`
- `ProviderUnavailable`
- `ResourceUnavailable`
- `CancellationError`
- `EvidenceError`
- `StateConflict`
- `InfrastructureError`

Errors include a stable code, operator message, run correlation ID, retryability, and safe details. Raw provider payloads and environment values are excluded.

## Portability

The first supported platform is macOS Apple Silicon. Platform-dependent functionality remains behind adapters. Core models, Pact semantics, state machines, events, and evidence schemas remain platform-neutral.

TreePact does not promise Linux or Windows support until native checks and path semantics have dedicated adapters and verification.
