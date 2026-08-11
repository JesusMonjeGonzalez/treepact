# Integration Strategy

## Purpose

TreePact integrates with agents and editors without transferring policy authority to them. An integration may provide reasoning, session lifecycle, user interface, or notifications. It may not decide whether a run is accepted.

## Integration layers

| Layer | Purpose | Trust |
|---|---|---|
| Model provider | Inference for the native loop | Untrusted output |
| Runtime adapter | Session and proposal lifecycle | Untrusted actor under supervision |
| Hook or plugin | Provider event visibility and early blocking | Defense in depth |
| Editor integration | User interface and context | No enforcement authority |
| Messaging channel | Submit, inspect, cancel, notify | Authenticated transport only |
| Repository Pact | Capabilities and required evidence | Trusted after explicit local review |
| TreePact core | Policy, effects, gates, evidence | Root of application authority |

## Assurance levels

Assurance is assigned to a run or ChangeSet, not to an installed product.

### TP0: observed

TreePact sees a final repository state or diff produced elsewhere. It can run deterministic gates, but cannot establish how earlier effects occurred.

### TP1: adopted

TreePact captures a repository checkpoint and observes future changes. Events before adoption are opaque. Final checks and policy apply to the exact final tree.

### TP2: instrumented

A supported provider plugin or hook was active from a known checkpoint. TreePact receives lifecycle and tool events. Provider instrumentation may still be bypassed by external processes or incompatible updates.

### TP3: controlled runtime

TreePact created the worktree, launched the pinned runtime with a known configuration, restricted the capability surface, owned cancellation, monitored state independently, and executed final gates itself.

Rules:

- A plugin installation alone does not grant TP2; load and version must be observed.
- ACP, HTTP, SDK, hooks, or MCP alone do not grant TP3.
- Any unexplained filesystem mutation may reduce assurance.
- A changed base commit, Pact, policy, or final tree invalidates prior gate results.
- The final assurance is the lowest established level across process control, tool coverage, workspace integrity, and evidence completeness.

## Adapter contract

Every runtime adapter declares:

```text
adapter_id
adapter_version
supported_runtime_versions
capabilities
required_configuration
available_tools
cancellation_semantics
event_coverage
known_limitations
assurance_ceiling
```

The core asks the adapter to:

```text
probe
start_session
send_task
receive_event
respond_to_permission
cancel_session
close_session
collect_metadata
```

The core never asks an adapter to calculate gates or write evidence directly.

## Capability negotiation

Before creating a worktree, TreePact compares:

- Pact-required capabilities;
- adapter-declared capabilities;
- runtime version;
- active plugin or hook version;
- model availability;
- network and privacy policy;
- cancellation support.

Missing mandatory capability or an incompatible runtime version fails preflight with `runtime_incompatible`. If a run record already exists, it terminates as `failed`, produces a pre-workspace Decision Bundle, and returns exit code 15. TreePact does not improvise a weaker workflow silently.

## Event normalization

Provider events are translated into TreePact event types:

| Provider event | Normalized event |
|---|---|
| Session created | `runtime.session_started` |
| Tool requested | `tool.proposed` |
| Permission requested | `runtime.permission_requested` |
| Tool completed | `runtime.tool_reported` |
| File edited | `workspace.change_signalled` |
| Session idle | `runtime.idle` |
| Session error | `runtime.failed` |
| Session stopped | `runtime.session_finished` |

Provider events are signals. TreePact records its own execution and filesystem facts separately.

## Interfaces that must remain decoupled

- Provider session IDs are external correlations, not TreePact primary keys.
- Provider transcripts are not the TreePact event ledger.
- Provider permission decisions are not TreePact policy decisions.
- Editor approvals are not TreePact final decisions.
- ACP is not the Pact format.
- MCP is not an enforcement boundary.
- OpenCode or Claude hooks are not the final source of Git state.
- UI clients never open TreePact SQLite directly.
- local gateway provider profiles are not TreePact domain models.

## Update policy

- Pin a supported runtime version or bounded compatibility range.
- Disable automatic runtime updates for controlled runs where possible.
- Probe version and capabilities before each controlled run.
- Maintain provider-specific compatibility fixtures in the final verification phase.
- Treat an unknown major or incompatible minor version as unsupported.
- Promote support only after the adapter's focused compatibility campaign passes.
- Keep the previous supported adapter and runtime combination available for rollback.

## Session adoption

TreePact may adopt an external session only after explicit operator action.

Adoption captures:

- repository identity;
- canonical root;
- base HEAD;
- index and working-tree status;
- untracked-file inventory;
- current tree or patch digest;
- runtime and observable version;
- active integration state;
- adoption timestamp.

All earlier activity is marked opaque. Hooks added after adoption cover only future events. Adoption never becomes TP3 without restarting under TreePact control.

## Integration order

1. Native TreePact loop.
2. One external runtime, selected after M7.
3. Claude Code only after a separate privacy and remote-egress ADR authorizes it.
4. VS Code UI after daemon/API stabilization.
5. Memory vault read-only adapter.
6. Messaging status channel.
7. Additional runtimes only after repeated demand.
