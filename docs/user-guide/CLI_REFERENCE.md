# CLI Reference

## Conventions

```text
treepact [global-options] <command> [command-options]
```

Global options:

| Option | Meaning |
|---|---|
| `--config PATH` | Use an explicit user configuration file |
| `--data-dir PATH` | Use an explicit TreePact data root for development or recovery |
| `--log-level LEVEL` | `error`, `warning`, `info`, or `debug` |
| `--json` | Emit machine-readable output where supported |
| `--no-color` | Disable ANSI output |
| `--version` | Print TreePact and schema versions |
| `--help` | Show command help |

Global options may redirect TreePact's own data but cannot expand repository authority.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Command completed successfully |
| 2 | CLI usage or invalid option |
| 10 | Configuration invalid |
| 11 | Pact invalid |
| 12 | Repository or worktree invalid |
| 13 | Policy denied |
| 14 | Resource unavailable without waiting |
| 15 | Runtime or model unavailable |
| 16 | Check or gate failed |
| 17 | Run needs human review |
| 18 | Run cancelled |
| 19 | State conflict or non-resumable run |
| 20 | Evidence or storage failure |
| 21 | Infrastructure failure |

`treepact run` returns 0 only when it completed its requested workflow. An `accepted` decision returns 0. `needs_review` returns 17 and `rejected` due to checks returns 16.

Outcome mapping for `treepact run`:

| Run outcome | Exit selection |
|---|---|
| `accepted` | 0 |
| `rejected` by policy before execution | 13 |
| `rejected` by check or final gate | 16 |
| `needs_review` | 17 |
| `cancelled` | 18 |
| `failed` because runtime/provider is unavailable or failed | 15 |
| `failed` because repository/worktree is invalid | 12 |
| `infrastructure_error` in evidence/storage | 20 |
| Other `infrastructure_error` | 21 |

If final Decision Bundle generation fails after gates were calculated, TreePact stores the calculated result as provisional diagnostic state, sets the run terminal outcome to `infrastructure_error`, and returns 20. It never returns success without durable final evidence.

An unsupported runtime version, missing mandatory capability, unexpected plugin, or invalid controlled provider configuration is `runtime_incompatible`: exit 15. It is not a policy rejection or check failure.

## `treepact init`

Create a conservative Pact draft.

```text
treepact init [--repo PATH] [--project-id ID] [--force]
```

Behavior:

- resolves the Git root;
- refuses to overwrite an existing Pact without `--force`;
- writes no executable check unless explicitly selected by the user;
- records no repository content in TreePact state;
- does not run tests, builds, package managers, or agents.

## `treepact validate`

Validate and compile the repository Pact without executing it.

```text
treepact validate [--repo PATH] [--pact PATH] [--explain] [--print-canonical]
```

`--print-canonical` prints the normalized non-secret policy and hash. It must not expose ignored files or environment values.

## `treepact doctor`

Inspect local TreePact health.

```text
treepact doctor [--repo PATH] [--deep]
```

Default mode is read-only and does not invoke model inference or repository checks. `--deep`, if implemented, may perform safe loopback and filesystem diagnostics but still does not run project commands.

## `treepact run`

Create and execute a supervised run.

```text
treepact run TASK
  [--repo PATH]
  [--mode observe|repair]
  [--runtime native|opencode|claude-code]
  [--model-profile PROFILE]
  [--max-attempts 1..PACT_MAX]
  [--max-minutes N]
  [--wait-for-resources]
  [--label TEXT]
```

Rules:

- CLI limits may lower Pact limits, never raise them.
- `--runtime` must be enabled and compatible.
- `--model-profile` must be allowed by Pact and privacy policy.
- `--wait-for-resources` keeps the run in `waiting_resource`; without it, unavailable resources return 14.
- TASK is stored as operator input and treated as higher authority than repository content but lower than the compiled Pact.

## `treepact status`

Show one run or the latest active run.

```text
treepact status [RUN_ID] [--watch] [--interval SECONDS]
```

`--watch` reads progress events. It does not own the run or alter scheduling.

## `treepact runs`

List runs.

```text
treepact runs
  [--repo PATH]
  [--state STATE]
  [--limit N]
  [--since TIMESTAMP]
```

## `treepact projects`

List registered projects and last known Pact metadata.

```text
treepact projects [--active-only]
```

This command does not scan the entire home directory.

## `treepact logs`

Display redacted operational output.

```text
treepact logs RUN_ID
  [--follow]
  [--events]
  [--attempt N]
  [--check CHECK_ID]
  [--since TIMESTAMP]
```

Raw secrets and full provider payloads are never made available through a debug flag.

## `treepact diff`

Display the run change against its recorded base.

```text
treepact diff RUN_ID [--stat] [--name-only] [--output PATH]
```

`--output` writes a copy of the patch and records that export as an event. It does not apply the patch elsewhere.

## `treepact evidence`

Inspect or export the Decision Bundle.

```text
treepact evidence RUN_ID
  [--format summary|json|markdown]
  [--verify-hashes]
  [--output PATH]
```

`--verify-hashes` recalculates local artifact and event-chain hashes. It does not prove integrity against an attacker controlling the host.

## `treepact cancel`

Cancel an active or waiting run.

```text
treepact cancel RUN_ID [--reason TEXT] [--wait SECONDS]
```

The reason is redacted and stored as operator metadata.

## `treepact resume`

Resume a previously interrupted run.

```text
treepact resume RUN_ID [--wait-for-resources]
```

Resume cannot select a different runtime, Pact, base commit, repository, or model provider. Such changes require a new run.

## `treepact cleanup`

Remove transient run resources.

```text
treepact cleanup RUN_ID [--force]
```

`--force` permits removal of an unreviewed worktree after an explicit warning. It does not bypass repository identity checks.

Version 1 does not expose evidence purge. Designing deletion without breaking append-only history is deferred.

## `treepact provider status`

Show configured model-provider health without sending repository content.

```text
treepact provider status [--provider ID]
```

## `treepact config show`

Show effective non-secret configuration and its sources.

```text
treepact config show [--sources]
```

Secret values appear only as `configured` or `not configured`.

## `treepact verify`

Recalculate deterministic gates for an existing frozen run without invoking a model.

```text
treepact verify RUN_ID [--check-artifacts] [--check-events]
```

This does not rerun repository checks. A future explicit option may create a new verification run, never overwrite existing evidence.

## `treepact eval`

Run a declared evaluation suite during the final verification phase.

```text
treepact eval SUITE_ID [--candidate DIGEST] [--output PATH]
```

This command is not a CI runner. It exists to execute versioned local evaluation campaigns and preserve evidence.

## Commands intentionally absent

TreePact must not implement:

```text
treepact commit
treepact push
treepact merge
treepact pr
treepact release
treepact publish
treepact deploy
treepact sign
treepact notarize
```

If future product direction requires any of these, it requires a new product threat model and explicit architectural decision. They are not ordinary backlog items.

## Accessibility requirements

- `--no-color` removes all semantic dependence on color.
- Progress has a non-animated text mode.
- Machine output is written only to stdout; diagnostics use stderr.
- Every status has a stable textual label in addition to symbols.
- Tables degrade to line-oriented output on narrow terminals.
- Help and errors remain usable with VoiceOver and terminal screen readers.
- Commands must not require an interactive prompt when complete explicit options are supplied.
- Destructive cleanup confirmation has a non-interactive explicit flag but never infers consent.
