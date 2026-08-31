# TreePact User Guide

## Audience

This guide is for a developer who wants to delegate bounded changes to coding agents while retaining local control over scope, checks, resources, and evidence.

All commands in this guide describe the intended product. Until implementation and final verification are complete, they are specifications rather than readiness claims.

## Mental model

TreePact has three independent inputs:

1. The task describes what should be achieved.
2. The repository Pact describes what is permitted and what must be verified.
3. The selected runtime proposes how to do the work.

TreePact owns the workspace and checks. The runtime does not.

```text
task + Pact + runtime
        |
        v
supervised run
        |
        v
Decision Bundle
```

## Intended installation

The initial internal installation uses `uv` and Python 3.12:

```bash
uv tool install /path/to/TreePact
treepact doctor
```

A later public distribution may provide a signed standalone macOS binary. That decision is deferred.

TreePact stores its state under:

```text
~/Library/Application Support/TreePact/
```

User configuration is stored under:

```text
~/.config/treepact/config.toml
```

TreePact must not place credentials in either location.

## First-time setup

### 1. Check the environment

```bash
treepact doctor
```

The command reports:

- TreePact and schema versions.
- Python and platform.
- Git availability.
- SQLite state.
- Data-directory permissions.
- Configured runtime and provider.
- local gateway availability when configured.
- Memory pressure.
- Interrupted runs.

`doctor` is read-only unless an explicit future repair option is added.

### 2. Initialize a repository

Run inside the repository:

```bash
treepact init
```

TreePact inspects only basic repository metadata and creates a conservative `.treepact.yaml` draft. It must not infer and authorize arbitrary commands automatically.

The user reviews and edits the Pact manually.

### 3. Validate the Pact

```bash
treepact validate
```

Validation checks syntax, schema, paths, check definitions, limits, gate names, and policy contradictions. It does not execute repository commands.

### 4. Check provider status

```bash
treepact provider status
```

The provider status command may contact only the configured provider endpoint. It does not send repository content.

## The Pact

### Minimal example

```yaml
version: 1
project:
  id: loopback
  toolchain: python

workspace:
  readable:
    - src/
    - tests/
    - pyproject.toml
  writable:
    - src/
    - tests/
  denied:
    - .git/
    - .env
    - credentials/

checks:
  unit:
    argv: ["uv", "run", "pytest", "tests/unit"]
    timeout_seconds: 600
    required: true
    phases: ["attempt", "final"]
    modes: ["observe", "repair"]

gates:
  - required_checks_pass
  - no_denied_paths_changed
  - no_secrets_in_diff
  - worktree_consistent
  - evidence_complete

limits:
  attempts: 3
  turns_per_attempt: 30
  minutes: 30
  context_bytes: 750000
  max_input_tokens: 64000
  max_output_tokens: 8000
  model_profile: fast-code
  memory_mb: 14000
  network:
    runtime: loopback_only
    checks: denied

actions:
  unavailable:
    - commit
    - push
    - merge
    - publish
    - deploy
    - release
    - access_credentials
    - modify_pact
    - destructive_delete
    - external_message
```

`unavailable` is not a feature toggle. Schema version 1 requires every globally prohibited action as an explicit human-readable assertion, and the compiler applies the same prohibitions independently. Removing an item makes the Pact invalid; it never enables that capability.

### Readable, writable, and denied paths

`readable` controls what repository content TreePact may expose through its read tools. `writable` controls what patches may modify. `denied` overrides both.

Paths are relative to the registered repository root. Absolute paths and parent traversal are invalid.

TreePact always protects internal Git metadata and its own state, even if a Pact attempts to allow them.

### Checks

Checks have stable IDs. A runtime asks for `run_check("unit")`; it cannot replace the argv or timeout.

`phases` controls whether a check may run during an attempt, during final verification, or both. `modes` controls whether it is available in `observe`, `repair`, or both.

Commands are arrays rather than shell strings:

```yaml
argv: ["./scripts/test.sh", "--unit"]
```

The following form is invalid:

```yaml
command: "./scripts/test.sh && git push"
```

TreePact also performs semantic validation that JSON Schema cannot express completely. It rejects shell executables such as `sh`, `bash`, and `zsh`, shell `-c` forms, and interpreter inline-evaluation forms. Metacharacters passed to a normal executable are literal argv values because TreePact never invokes a shell.

Checks are run from the worktree unless the Pact declares a safe relative working directory.

### Gates

Gates evaluate captured facts after the runtime finishes. A required check with exit code zero is evidence for `required_checks_pass`. The runtime saying that tests passed is not.

### Limits

Limits are ceilings, not targets. The runtime cannot increase them. A CLI invocation may lower them when supported.

`network.runtime` applies to the selected runtime and its model transport. `loopback_only` permits TreePact-controlled local endpoints such as local gateway and a controlled OpenCode server. `network.checks` applies to repository check processes. Version 1 supports only `denied` and `loopback_only`; it has no arbitrary host allowlist or unrestricted mode.

On the native trusted-harness lane, these fields express required policy but not automatically proven OS-level confinement. If a Pact requires enforceable denial and the selected lane cannot provide it, preflight fails rather than weakening the requirement.

## Running tasks

### Observe mode

```bash
treepact run "Diagnose why the unit test is failing" --mode observe
```

Observe mode may:

- inspect allowed files;
- search allowed content;
- run checks if the Pact permits checks in observe mode;
- produce a diagnosis and evidence.

Observe mode may not apply patches.

### Repair mode

```bash
treepact run "Fix the failing health-check fixture" --mode repair
```

Repair mode may:

- perform observe actions;
- apply patches within writable paths;
- run named checks;
- retry within the configured limit.

Repair mode still cannot commit, push, merge, publish, modify the Pact, or access credentials.

### Selecting a runtime

```bash
treepact run "Fix the parser regression" --mode repair --runtime native
```

Future examples:

```bash
treepact run "Fix the parser regression" --mode repair --runtime opencode
treepact run "Fix the parser regression" --mode repair --runtime claude-code
```

Selecting a runtime does not alter the Pact. If a runtime lacks required capabilities, TreePact rejects the run before creating side effects.

### Selecting a repository

From outside the repository:

```bash
treepact run "Diagnose the export failure" --repo ~/Projects/my-app --mode observe
```

The path is resolved to its canonical Git root. TreePact does not accept an arbitrary subdirectory as a separate authority root.

## What happens during a run

1. TreePact resolves the repository.
2. TreePact loads and compiles `.treepact.yaml`.
3. TreePact records the base commit and Pact hash.
4. TreePact checks resources and acquires a local lease.
5. TreePact creates a detached worktree.
6. TreePact snapshots check scripts and relevant build configuration.
7. TreePact starts the selected runtime.
8. Runtime tool proposals pass through policy.
9. TreePact performs allowed actions.
10. Failed work may start another attempt, up to the limit.
11. TreePact executes required final checks.
12. TreePact calculates gates.
13. TreePact releases resources.
14. TreePact generates the Decision Bundle.
15. The worktree remains available for inspection.

## Monitoring a run

### Current status

```bash
treepact status
treepact status <run-id>
```

Status includes:

- run state;
- current attempt and turn;
- active runtime and model profile;
- elapsed and remaining budget;
- worktree path;
- active check;
- resource wait or pressure;
- last safe event;
- assurance level.

### Logs

```bash
treepact logs <run-id>
treepact logs <run-id> --events
treepact logs <run-id> --check unit
```

Default logs are redacted and human-readable. `--events` shows the append-only event projection, not raw SQLite rows.

### Diff

```bash
treepact diff <run-id>
```

This displays the current worktree patch against the recorded base commit. A final Decision Bundle contains the exact final patch digest.

## Cancellation

```bash
treepact cancel <run-id>
```

Cancellation requests the run loop to stop and terminates only child process groups recorded for that run. Evidence collected before cancellation remains.

Cancellation does not remove the worktree.

## Resuming

```bash
treepact resume <run-id>
```

TreePact resumes only if:

- the run is in a resumable state;
- the repository identity matches;
- the base commit remains available;
- the worktree is present and consistent;
- the Pact snapshot is available;
- no conflicting process or lock exists;
- previous effects have unambiguous outcomes.

If those conditions fail, TreePact returns `needs_review` or requires a new run. It does not guess.

## Reviewing the result

### Integration JSON

`review` is the only stable machine-readable review surface:

```bash
treepact review --schema-version 1 --limit 20
treepact review --schema-version 1 --run-id run_<32-lowercase-hex>
```

The list limit defaults to 20 and must be from 1 through 100. The two forms are
exclusive: `--limit` is not accepted with `--run-id`. Successful stdout is one
JSON document conforming to `schemas/review.schema.json`; diagnostics go to
stderr using the normal human CLI error convention. Invalid input exits 10,
missing storage or run data exits 20, and unexpected failures exit 21.

The projection contains run identity, project identity, state, decision,
reason, assurance and timestamps. Detail adds stored gate results, bundle
availability, event-chain head and artifact metadata limited to ID, kind,
digest, size and media type. It never includes task text, repository or
worktree paths, artifact paths or content, prompts, provider payloads, logs or
diffs. The command opens existing SQLite storage through `mode=ro` plus
`query_only`; it performs no migrations, recovery, run discovery, gate/event
persistence, bundle generation or directory creation.

### Evidence summary

```bash
treepact evidence <run-id>
```

The summary includes:

- task and mode;
- repository and base commit;
- Pact hash;
- runtime, provider, and model profile;
- attempts and termination reasons;
- files changed;
- policy denials;
- checks and exit results;
- gates;
- resource usage;
- assurance level;
- final decision;
- artifact paths and hashes.

### Evidence directory

```text
runs/<run-id>/
├── events.jsonl
├── report.md
├── report.json
├── diff.patch
├── checks/
│   └── <check-id>/
│       ├── result.json
│       ├── stdout.txt
│       └── stderr.txt
├── artifacts/
└── resources.json
```

### Decision interpretation

`accepted` means review the patch. It is not permission to merge automatically.

`needs_review` means TreePact found evidence but could not safely calculate full compliance. Common causes include changed check scripts, flaky results, unsupported toolchain behavior, external workspace modification, or incomplete artifacts.

`rejected` means at least one mandatory policy or gate failed.

## Cleanup

```bash
treepact cleanup <run-id>
```

Cleanup:

- verifies the run is not active;
- confirms or requires `--force` when unreviewed changes exist;
- removes the Git worktree safely;
- removes transient files;
- preserves the Decision Bundle and event history according to retention policy.

Cleanup never deletes the main repository or its branch history.

## Resource behavior

TreePact uses conservative resource scheduling on the 36 GB M4 Max:

- one mutating run at a time;
- one large-model lease at a time;
- reserved memory for macOS and active applications;
- separate estimate for Swift or Gradle builds;
- no new expensive operation under critical pressure;
- no silent swap-heavy attempt;
- no remote fallback without explicit configuration and authorization.

A run can enter `waiting_resource`. This is not a failure and does not consume an attempt.

## Using TreePact from an IDE

The supported initial workflow is to run TreePact in the integrated terminal and open the generated worktree in the editor.

Future TreePact editor integrations will show status, diff, gates, and evidence. They will not replace the daemon or policy engine.

A Claude Code or OpenCode session started independently in an editor may be observed or adopted later, but it cannot receive full historical assurance retroactively.

## Troubleshooting

### Pact validation fails

Run:

```bash
treepact validate --explain
```

Resolve unknown keys, absolute paths, invalid commands, missing required limits, or contradictory path rules.

### local gateway is unavailable

Run:

```bash
treepact provider status
treepact doctor
```

Observe and repository validation remain available. A repair requiring inference pauses or fails explicitly. TreePact never selects a remote provider silently.

### A check is blocked because its script changed

Inspect:

```bash
treepact diff <run-id>
treepact evidence <run-id>
```

Start a new run after manually reviewing the script change or use a check implementation outside the runtime's writable scope. TreePact does not execute a newly modified harness script automatically.

### Run is interrupted

Run:

```bash
treepact status <run-id>
treepact resume <run-id>
```

If resume is rejected, preserve the evidence and create a new run rather than changing SQLite manually.

### Worktree was edited manually

TreePact records an external workspace change and may reduce assurance or invalidate pending verification. Review the diff and start a new verification from the resulting state if needed.

### Memory pressure is high

Close unrelated applications, allow local gateway to unload unused models, or select a smaller profile. Do not override the memory gate merely to start the run.

## Data and privacy

- TreePact stores operational metadata locally.
- Prompts are not persisted in full by default.
- Secrets must not be stored in the Pact.
- Local memory is not queried unless a future explicit adapter is enabled.
- Remote model egress is disabled initially.
- Reports are redacted before display and storage.
- Artifact retention remains configurable and local.

## Safety reminders

- A worktree is not a sandbox.
- Repository checks execute repository code.
- Tests may be insufficient even when they pass.
- Prompt injection is contained by capability limits, not solved by prompts.
- A malicious process already running as the user may bypass local assumptions.
- Keep normal backups of repositories and important data.
