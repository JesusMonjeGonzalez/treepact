# Acceptance Catalog

This catalog assigns stable IDs before implementation. The final verification campaign links each ID to one or more tests, expected events, and artifacts.

## Product

| ID | Criterion |
|---|---|
| PRD-001 | TreePact runs without importing another portfolio repository |
| PRD-002 | The core flow works with a local Git repository and CLI only |
| PRD-003 | No merge, push, publish, deploy, or release command exists |
| PRD-004 | `accepted` is described as Pact compliance, not correctness |
| PRD-005 | A Decision Bundle is produced for every terminal run with available storage |
| PRD-006 | A terminal pre-workspace run produces a valid bundle with null final tree and a reason code |

## Pact

| ID | Criterion |
|---|---|
| PACT-001 | Unknown Pact fields are rejected |
| PACT-002 | Absolute and parent-traversal paths are rejected |
| PACT-003 | Denied paths override readable and writable paths |
| PACT-004 | Checks use argv arrays and cannot express shell composition |
| PACT-005 | Attempt maximum cannot exceed three in schema version 1 |
| PACT-006 | Run stores and uses one immutable canonical Pact hash |
| PACT-007 | CLI overrides can reduce but never expand Pact limits |

## Workspace

| ID | Criterion |
|---|---|
| WS-001 | Worktree uses the recorded base commit |
| WS-002 | Runtime-facing paths cannot escape the worktree |
| WS-003 | `.git` remains inaccessible to runtime tools |
| WS-004 | Symlinks resolving outside scope are denied |
| WS-005 | Final changed paths are independently reconciled with Git |
| WS-006 | Worktree remains after a terminal run until explicit cleanup |
| WS-007 | Cleanup cannot remove the main repository |

## Tools and processes

| ID | Criterion |
|---|---|
| TOOL-001 | Runtime receives only the declared typed tool inventory |
| TOOL-002 | No arbitrary shell tool exists |
| TOOL-003 | `run_check` accepts only a declared check ID |
| TOOL-004 | Child environment is built from an allowlist |
| TOOL-005 | SSH and secret variables are absent from child environment |
| TOOL-006 | Modified check executables or protected inputs block execution |
| TOOL-007 | Timeout terminates only the recorded process group |
| TOOL-008 | Output truncation is explicit and does not appear complete |

## Runtime and models

| ID | Criterion |
|---|---|
| RT-001 | Model output cannot set the final run decision |
| RT-002 | Invalid tool proposals fail closed |
| RT-003 | Provider fallback never occurs silently |
| RT-004 | Repository content is marked and treated as untrusted |
| RT-005 | Turns, attempts, time, context, and output are bounded |
| RT-006 | Unknown external runtime version is rejected or capped honestly |
| RT-007 | Final Git reconciliation is independent from runtime transcript |

## Checks and gates

| ID | Criterion |
|---|---|
| GATE-001 | A check passes only after TreePact records its execution and exit result |
| GATE-002 | Every required check has a terminal result before acceptance |
| GATE-003 | A denied-path change prevents acceptance |
| GATE-004 | A secret detected in the diff prevents acceptance |
| GATE-005 | Missing or inconsistent evidence prevents acceptance |
| GATE-006 | Changed test or check inputs are visible in the report |
| GATE-007 | Flaky or unexplained retry outcomes can result in `needs_review` |

## State and recovery

| ID | Criterion |
|---|---|
| STATE-001 | Illegal run and attempt transitions are rejected |
| STATE-002 | Attempt count never exceeds the Pact snapshot |
| STATE-003 | Interrupted runs are discovered after restart |
| STATE-004 | Uncertain mutating effects block automatic resume |
| STATE-005 | Cancellation preserves prior evidence |
| STATE-006 | Resume verifies repository, base, Pact, worktree, and locks |
| STATE-007 | Resource waiting does not consume an attempt |

## Evidence

| ID | Criterion |
|---|---|
| EVID-001 | Event sequence is monotonic per run |
| EVID-002 | Event hash-chain corruption is detectable |
| EVID-003 | Artifacts are addressed and verified by SHA-256 |
| EVID-004 | Report claims trace to events, checks, gates, or artifacts |
| EVID-005 | Full prompts and secrets are absent by default |
| EVID-006 | Evidence export records its own event |
| EVID-007 | Evidence from different candidates is not combined silently |

## Resources

| ID | Criterion |
|---|---|
| RES-001 | Only one mutating run is active initially |
| RES-002 | Only one large-model lease is granted |
| RES-003 | Critical memory pressure blocks new expensive work |
| RES-004 | Insufficient resources produce wait or explicit failure |
| RES-005 | A stale lease can be recovered without granting duplicate authority |

## Security

| ID | Criterion |
|---|---|
| SEC-001 | README or source prompt injection cannot expand authority |
| SEC-002 | Fake secret fixture does not reach provider capture or report |
| SEC-003 | Another repository cannot be read or written |
| SEC-004 | Publication requests cannot reach Git remote or external service |
| SEC-005 | Dynamic skills, MCP, and plugins are absent initially |
| SEC-006 | Native macOS lane is labelled `trusted_harness`, not sandboxed |
| SEC-007 | Runtime configuration drift is detected and affects assurance |

## CLI

| ID | Criterion |
|---|---|
| CLI-001 | Every documented command has stable help and exit behavior |
| CLI-002 | Machine-readable output does not expose secrets |
| CLI-003 | `doctor` default operation is read-only |
| CLI-004 | `validate` does not execute project commands |
| CLI-005 | `verify` does not rerun repository checks implicitly |
| CLI-006 | Resume cannot change runtime, provider, Pact, base, or repository |

## Integrations

| ID | Criterion |
|---|---|
| INT-001 | Provider events normalize without becoming core state authority |
| INT-002 | Missing required hook or plugin lowers assurance or blocks explicitly |
| INT-003 | Provider internal databases and transcripts are not read |
| INT-004 | OpenCode integration does not call shell, share, auth mutation, or dynamic MCP APIs |
| INT-005 | Claude Code integration does not use bypassPermissions or private VS Code storage |
| INT-006 | An adopted session marks prior history opaque |
| INT-007 | The same Pact semantics work with native and first external runtime |
