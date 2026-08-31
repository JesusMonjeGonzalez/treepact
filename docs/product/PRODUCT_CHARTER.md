# TreePact Product Charter

## Decision record

- Date: 2026-08-11
- Product status: standalone alpha with an optional Hearthia host adapter
- Initial operator: one independent developer on one Apple Silicon Mac
- Initial interface: CLI
- Initial repositories: local Git repositories
- Initial modes: `observe` and `repair`
- Initial agent runtime: TreePact native tool loop
- Preferred local model provider: local gateway through its OpenAI-compatible loopback endpoint

## Product definition

TreePact is a local pre-merge control layer for changes produced by coding agents. It accepts a bounded task, creates an isolated worktree, runs one agent within a capability contract, executes repository checks, and returns a decision bundle.

TreePact is independent from the model and agent that generated the change. The agent's transcript and claims are context, not evidence.

## First user

The first real user is the developer maintaining their own local macOS application repositories.

## Painful event

A reproducible problem is delegated to a coding agent. The developer currently has to create or inspect a worktree, manage model availability, supervise edits, rerun tests, determine whether the correct checks ran, inspect the diff, and ensure nothing was published or accessed outside scope.

## Current alternatives

- Run Claude Code, OpenCode, Codex, or another agent directly.
- Create Git worktrees manually.
- Add project instructions such as `AGENTS.md` or `CLAUDE.md`.
- Use hooks and permissions maintained separately for each agent.
- Run repository scripts manually.
- Depend on CI after pushing a branch.
- Trust the agent's summary and inspect the diff afterwards.

## Job to be done

> When a bounded, reproducible defect exists in a local repository, delegate up to three repair attempts and return to a reviewable patch with reproducible evidence, without supervising the session and without giving the agent publication authority.

## Value proposition

> TreePact turns probabilistic coding-agent work into a bounded local run whose declared checks and policy decisions can be independently reconstructed.

## Product vocabulary

| Term | Meaning |
|---|---|
| Pact | Versioned executable contract declared by a repository |
| Task | Original objective and requested mode |
| Run | Complete supervised execution |
| Attempt | One bounded reasoning and editing cycle |
| Tool proposal | Action requested by a runtime |
| Policy decision | Deterministic allow or deny decision |
| Check | Predeclared command executed by TreePact |
| Gate | Required condition calculated by TreePact |
| Evidence | Captured fact linked to the run and exact workspace state |
| Decision bundle | Final machine-readable and human-readable output |

## Outcomes

- `accepted`: every required gate passed for the exact final change.
- `rejected`: policy or required verification failed decisively.
- `needs_review`: evidence is incomplete, ambiguous, stale, or insufficient.
- `failed`: the run could not complete its workflow.
- `cancelled`: the operator cancelled the run.
- `infrastructure_error`: the environment, toolchain, model, or TreePact failed.

`accepted` never means that the code is correct, secure, or ready to release. It means that the exact change satisfied the exact pact snapshot under the recorded environment.

## Initial scope

- Single user.
- Single Mac.
- Local Git repositories.
- CLI operation.
- One mutating run at a time.
- Worktree per run.
- Strict `.treepact.yaml` contract.
- Read, search, patch, named checks, and finish tools.
- Maximum three attempts.
- Local SQLite and artifact storage.
- Cancellation and later resumption.
- Local model integration through local gateway.
- Evidence bundle containing events, diff, checks, resources, and report.

## Explicit exclusions

- Generic conversation assistant.
- Email, calendar, purchases, or finance.
- Browser automation.
- Device control.
- Remote host operation.
- Multiuser or SaaS.
- CI/CD pipelines.
- GitHub or GitLab control plane.
- Automatic commits, pushes, merges, releases, or deployment.
- Marketplace of agents, skills, plugins, or pacts.
- Dynamic skill installation.
- Policy mutation by the model.
- Local memory vault in the first implementation.
- Chat channel in the first implementation.
- Strong macOS native-process sandbox claims.

## Product success metrics

- Median human supervision time falls by at least 30 percent compared with direct agent use.
- At least 70 percent of bounded runs produce a useful decision or patch.
- False accepted outcomes remain below 5 percent in the evaluation corpus.
- No TreePact-brokered write resolves outside the registered worktree; native check code remains a separately disclosed host-risk boundary.
- No known canary secret fixture reaches a model payload, report, or configured external destination.
- At least 30 real runs occur in six weeks across at least three repositories.
- A second runtime can use the same Pact without changing its semantics.

## Kill or narrow criteria

- TreePact saves less than 20 percent of supervision time.
- Review and recovery cost more than direct use of an agent.
- Repository-specific exceptions dominate the core design.
- Most value comes from one provider's existing hooks, making a plugin sufficient.
- Scripts plus worktrees provide equivalent value.
- A competitor provides the same local, agent-neutral decision bundle with better integration.
- A security boundary cannot be enforced without unacceptable host access.
- The operator does not use TreePact at least four times in thirty days after stabilization.

## Positioning boundary

TreePact may claim that it captured checks and policy decisions. It may not claim:

- code correctness;
- complete sandboxing;
- complete prompt-injection prevention;
- complete secret detection;
- regulatory compliance;
- autonomous safe software delivery;
- replacement of human review;
- compatibility with every agent.

## Expansion gates

### Add a second runtime

Only after the native loop completes the first vertical slice and the Pact format is stable enough to compare behavior.

### Add a daemon

Only after foreground runs are repeatedly interrupted or the operator needs to leave the terminal.

### Add memory vault

Only after project decisions demonstrably improve task outcomes and a narrow read-only query contract exists.

### Add chat channel

Only after long-running tasks are used regularly. Messaging may submit, inspect, and cancel, but not approve critical actions.

### Add a desktop or VS Code interface

Only after the run state machine, local API, and evidence bundle are stable. UI remains a client, never an enforcement point.
