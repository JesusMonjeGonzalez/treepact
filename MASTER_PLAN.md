# TreePact Master Plan

## Authority and use

This is the authoritative implementation plan. An implementation agent must follow the milestones in order, preserve the non-negotiable invariants, update this document when a decision changes, and record deviations in an ADR.

Formal automated tests and the consolidated evaluation campaign are intentionally implemented and executed in the final verification milestone. Acceptance criteria, threat scenarios, fixtures, expected events, and oracles are designed before implementation.

Milestone exit gates M1-M8 are implementation checkpoints, not verified readiness gates. They may be marked `implemented_unverified`; only M9 can mark their acceptance criteria `verified`.

This plan does not authorize CI/CD work.

## Global constraints

1. Do not implement merge, push, publish, deploy, release, signing, or notarization actions.
2. Do not add a general shell tool.
3. Do not make any specific local gateway, agent runtime, editor, or container tooling mandatory.
4. Do not use another product's database or internal session files.
5. Do not create shared portfolio infrastructure.
6. Do not add a daemon, GUI, Telegram, browser, MCP, plugins, or remote providers before their milestone gate.
7. Do not report a check as passed based on model output.
8. Do not broaden a repository path because a runtime requests it.
9. Do not silently fall back to another model or provider.
10. Do not claim release readiness before final verification.

## Planned technology

- Python 3.12.
- `uv` for environment and lockfile.
- Typer for CLI.
- Pydantic v2 for strict schemas.
- PyYAML SafeLoader for Pact input.
- SQLite through the standard library.
- Numbered forward-only SQL migrations.
- HTTPX for loopback model APIs.
- `asyncio` and `subprocess` for process control.
- `psutil` for resource observation and process trees.
- `pathspec` for compiled path rules.
- SHA-256 for artifact IDs and local event hash chains.
- Git CLI through fixed argv calls.

## Milestone M0: freeze the design

### Goal

Create a complete, internally consistent specification before implementation.

### Deliverables

- Product charter.
- Architecture and component boundaries.
- State machines.
- Data model and migration rules.
- Pact schema design.
- Runtime and provider protocols.
- Security model and threat register.
- Evidence model.
- User guide and CLI reference.
- Integration specifications.
- Acceptance catalog and final campaign.
- Architectural decision records.
- Implementation handoff prompt.

### Exit gate

- Every user-visible command has defined behavior and exit codes.
- Every state transition has an owner and persisted event.
- Every integration has a trust level and failure behavior.
- Every security claim has a future verification criterion.
- No unresolved decision blocks repository foundation.

## Milestone M1: repository foundation

### Goal

Establish the standalone product without implementing agent execution.

### Deliverables

- Python package and `uv.lock`.
- Source tree matching the architecture.
- Typed IDs, UTC clock, enums, error taxonomy, and configuration loading.
- macOS data directories through `platformdirs`.
- CLI command surfaces with clear not-implemented failures where required.
- SQLite connection policy and migration runner.
- Documentation linked from the root README.

### Constraints

- No model calls.
- No worktree creation.
- No checks.
- No external runtime.
- No tests yet; maintain the verification traceability document.

### Exit gate

- All public modules and ownership boundaries exist.
- Configuration precedence is documented.
- There is no import from another portfolio repository.

## Milestone M2: durable state and evidence primitives

### Goal

Persist runs and append-only evidence independently from any agent.

### Deliverables

- Numbered SQL migrations.
- Tables for application commands, projects, Pact versions, tasks, runs, attempts, workspaces, runtime sessions, tool proposals, policy decisions, tool executions, checks, gates, model invocations, leases, artifacts, and events.
- Run and attempt state machines.
- Transaction boundary per application command.
- Monotonic event sequence per run.
- Event hash chain.
- Event-type-specific payload schema registry; the generic event envelope is not sufficient payload validation.
- Content-addressed artifact store.
- Human and JSON report projectors.
- Interrupted-run discovery on startup.

### Exit gate

- Current state can be reconstructed from tables and correlated with event history.
- No credentials or full model prompts are persisted.
- An unknown future schema is rejected without writing.

## Milestone M3: Pact compiler

### Goal

Convert `.treepact.yaml` into an immutable, deterministic run policy.

### Deliverables

- Strict schema version 1.
- Unknown-field rejection.
- Safe YAML parsing.
- Canonical serialization and hash.
- Canonical project-root resolution.
- Compiled read, write, and deny path rules.
- Named checks represented as argv arrays.
- Limits for attempts, turns, time, context, output, memory, runtime network, and check-process network.
- Explicit input-token and output-token ceilings.
- Check phases (`attempt`, `final`) and allowed modes (`observe`, `repair`).
- Semantic command validation rejecting shell executables and inline interpreter evaluation.
- Gate declarations.
- Clear validation diagnostics with source locations where practical.

### Exit gate

- Pact semantics do not depend on a runtime.
- A run stores a Pact snapshot and never reloads it mid-run.
- Shell strings, interpolation, pipes, redirects, and heredocs cannot be represented as check commands.

## Milestone M4: workspace and tool broker

### Goal

Control the actual change surface before adding model autonomy.

### Deliverables

- Git repository discovery.
- Base commit capture.
- Detached worktree creation under TreePact data storage.
- Worktree lock and lifecycle.
- Canonical path broker.
- Symlink and escape rejection.
- Bounded `list_files`, `read_file`, and `search_text` operations.
- Unified-diff validation and application through fixed Git argv.
- Named-check process runner.
- Sanitized environment and temporary HOME.
- Process groups, timeout, cancellation, stdout/stderr capture, and exit status.
- Initial hashes for scripts and build configuration used by checks.
- Block checks when their executable inputs were modified by the agent.

### Exit gate

- The model-facing surface contains no raw process or Git tool.
- Worktree changes can be represented as a deterministic final patch.
- TreePact distinguishes a worktree from a process sandbox in all messaging.

## Milestone M5: native runtime and loopback provider

### Goal

Complete one bounded agent loop under TreePact control.

### Deliverables

- `ModelProvider` protocol.
- Minimal non-streaming OpenAI-compatible provider.
- Local OpenAI-compatible gateway adapter over loopback HTTP.
- Model profiles rather than hardcoded model IDs.
- Native tool loop exposing only TreePact tools.
- Structured tool proposals and observations.
- Turn, context, token, and wall-clock limits.
- Maximum three attempts.
- No silent provider fallback.
- Explicit handling of unavailable models and malformed tool calls.
- Prompt sections marking repository content as untrusted data.

### Exit gate

- The runtime cannot directly execute commands or access arbitrary paths.
- TreePact remains functional for validation, checks, and evidence when local gateway is unavailable; repair pauses or fails explicitly.
- The model cannot set the final run decision.

## Milestone M6: resource scheduling and gates

### Goal

Protect the Mac and calculate acceptance deterministically.

### Deliverables

- One global mutating-run lease.
- Model-size and build-memory estimates.
- macOS memory pressure observation.
- Conservative local resource provider.
- Optional local gateway lease adapter only if local gateway exposes a stable API.
- Waiting-resource state.
- Required-check gate.
- Denied-path gate.
- secret-in-diff gate.
- worktree-consistency gate.
- evidence-completeness gate.
- `accepted`, `rejected`, and `needs_review` calculation.

### Exit gate

- Two large models cannot be scheduled simultaneously through TreePact.
- Critical memory pressure prevents a new expensive action.
- A gate references captured facts, never an agent statement.

## Milestone M7: operation and recovery

### Goal

Make runs operable without turning TreePact into a general platform.

### Deliverables

- Cancel command.
- Resume command for valid interrupted states.
- Cleanup command preserving evidence.
- Stale-lock recovery.
- Orphan-process detection limited to recorded child process groups.
- Retention configuration.
- `doctor` diagnostics.
- Clear recovery runbooks.
- Run listing, status, logs, diff, and evidence commands.

### Exit gate

- Recovery never assumes an uncertain side effect succeeded.
- A changed base, Pact, or worktree causes invalidation or review, not silent continuation.

## Milestone M8: first external runtime adapter

### Goal

Demonstrate that TreePact is agent-neutral.

### Selection rule

Implement OpenCode as the first external runtime because it can use local providers while preserving the initial no-remote-egress boundary. Claude Code remains a documented future adapter and requires a separate privacy/egress ADR before implementation.

### Deliverables

- Runtime capability negotiation.
- Version compatibility declaration.
- Session creation, cancellation, and event correlation.
- Provider-specific events translated into TreePact events.
- Final Git reconciliation independent of provider claims.
- Assurance level assigned to the run.
- No use of provider internal databases or transcript formats.

### Exit gate

- The same `.treepact.yaml` works with native and external runtimes.
- Provider absence or version mismatch fails clearly.
- The adapter does not weaken the TreePact policy core.

## Milestone M9: consolidated verification

### Goal

Implement and execute the complete verification catalog against one frozen candidate, without CI/CD and without redundant runs.

### Deliverables

- Unit, property, integration, security, resource, recovery, and multi-repository test suites.
- Frozen synthetic fixtures.
- Ten defined evaluation tasks.
- One consolidated failure-injection campaign.
- One backup/corruption/recovery sequence.
- One verification run per representative repository.
- Traceability from requirement to evidence.
- Defect and exception register.
- Go, narrow, or no-go decision.

### Execution policy

- Run full suites once against the frozen candidate.
- After a fix, rerun only the failed scenario, its direct dependencies, and affected critical smoke path.
- Preserve initial failures and all repeats.
- A code, dependency, schema, or configuration change creates a new candidate or requires documented impact analysis.

## Milestone M10: internal pilot

### Goal

Measure whether the product deserves continued development.

### Repositories

- A Python repository.
- A Swift repository.
- A Kotlin Multiplatform repository.
- One Kotlin/Compose repository.

### Duration and targets

- Six weeks.
- At least thirty real runs.
- At least three repositories used repeatedly.
- Median supervision reduction at least 30 percent.
- No critical security incident.
- False acceptance below 5 percent.
- At least five correctly blocked prohibited actions.

### Decision

- Continue as standalone product if use and savings repeat.
- Narrow to evidence tooling if orchestration adds little value.
- Narrow to an agent plugin if one runtime dominates.
- Keep internal if external demand is absent.
- Stop if it does not save time or creates unjustified trust.

## Milestone M11: optional product surfaces

These are separate decisions, not automatic next steps.

- Local daemon supervised by launchd.
- Read-only memory context adapter.
- Chat status channel.
- TreePact VS Code extension.
- SwiftUI desktop client.
- Rust process/filesystem helper.
- Stronger native macOS runner using a dedicated user or VM.
- Additional agent adapters.
- Public evidence-bundle schema.

## Estimated effort

| Outcome | Solo effort |
|---|---:|
| Planning package | 3-5 focused days |
| Vertical internal prototype | 4-6 weeks |
| Reliable internal tool and final campaign | 8-12 weeks total |
| Two-runtime public experimental product | 4-6 months total |

The schedule assumes scope discipline and excludes a large desktop UI, CI/CD, SaaS, multiuser support, and enterprise governance.
