# Security Model

## Security objective

TreePact reduces the authority granted to coding agents and produces reconstructable evidence. It does not claim to isolate an attacker who already controls the macOS user account or operating system.

## Assets

- Source repositories.
- Uncommitted changes.
- Personal files outside repositories.
- SSH keys and agents.
- API tokens and provider sessions.
- macOS Keychain.
- Local memory vault content.
- Model prompts and outputs.
- Pact definitions.
- Run state and evidence.
- Machine availability and memory.

## Trust boundaries

| Boundary | Trust treatment |
|---|---|
| Human operator | Authority for tasks and future manual approvals |
| TreePact core and compiled Pact | Root of application authority |
| Coding model | Untrusted probabilistic component |
| Agent runtime | Untrusted process with bounded capabilities |
| Repository content | Untrusted data that may contain instructions |
| Check command baseline | Explicitly trusted repository code with known hash |
| Check dependencies | Supply-chain risk |
| local gateway | Trusted for local model lifecycle, not policy |
| Memory vault | Sensitive, untrusted context source |
| Runtime plugin or hook | Defense in depth, not sole enforcement |
| MCP server | Untrusted executable integration |
| Messaging gateway | Authenticated input transport, not authority escalation |
| Operating system account | Trusted base for MVP, not hardened tenant boundary |

## Security invariants

1. Content cannot grant authority.
2. A model tool call is a proposal.
3. Policy is deterministic and external to the model.
4. Deny overrides read and write allowlists.
5. Runtime processes do not inherit the full user environment.
6. TreePact has no publication tool.
7. A check command is selected by ID, not composed by the model.
8. A changed check implementation cannot be auto-executed.
9. Provider fallback is explicit.
10. Uncertain effects block automatic continuation.
11. Final Git state is independently reconciled.
12. Evidence reports facts and limitations, not inferred safety.

## Filesystem controls

### Root handling

- Resolve Git root before policy compilation.
- Store canonical root identity.
- Accept repository-relative paths only from runtimes.
- Reject absolute paths, empty ambiguity, NUL, and parent traversal.
- Resolve existing path components and reject symlink escape.
- Reject operations on `.git` and TreePact data irrespective of Pact.
- Recheck target path immediately before mutation.
- Reconcile final changed paths with Git and policy.

### File types

- Reject device files, sockets, and FIFOs.
- Bound file size for reads.
- Treat binary reads as unsupported unless a future typed tool exists.
- Preserve and validate file mode changes.
- Reject submodule mutation initially.
- Detect case-folding collisions on macOS.
- Treat Unicode-confusable paths visibly in reports.

### Patch controls

- Parse paths before applying.
- Require expected workspace revision.
- Run `git apply --check` through fixed argv.
- Reject paths outside writable scope.
- Reject changes to Pact and protected check inputs.
- Capture patch digest before and after apply.
- Reconcile actual changed files after apply.

## Process controls

- No general shell tool.
- Checks are fixed argv arrays.
- Cwd is fixed to a declared relative directory.
- `HOME` points to a run-specific temporary directory.
- Environment is built from an allowlist, not filtered after inheritance.
- `SSH_AUTH_SOCK` is absent.
- Token, key, password, and cloud variables are absent.
- Child process group is recorded.
- Wall-clock timeout is mandatory.
- Output size is bounded with truncation represented explicitly.
- Cancellation targets only the recorded process group.
- No commands run with `sudo` or elevated privileges.
- No installation command during an active run.

## Native macOS limitation

Host-native Xcode, Swift, Metal, AVFoundation, and Gradle checks execute repository code under the user's OS authority. Sanitized environment and named commands reduce exposure but do not create strong confinement.

TreePact must display the native lane as `trusted_harness`, not `sandboxed`.

Stronger future options:

- dedicated standard macOS runner account without the user's Keychain or SSH files;
- ephemeral Apple Silicon VM;
- narrowly designed signed helper;
- policy-enforcing egress layer.

Docker is useful only for compatible Linux workloads and is not a universal macOS build boundary.

## Secret controls

- No secrets in `.treepact.yaml`.
- No secret values in SQLite.
- Provider authentication remains provider-owned or in macOS Keychain.
- TreePact builds an allowlisted child environment.
- Read policy denies common secret paths and extensions.
- Secret scanning occurs before model inclusion where feasible.
- Secret scanning occurs on final diff and reports.
- Logs use allowlisted fields rather than logging then redacting.
- Fake canary secrets are included in final verification fixtures.
- A detector miss remains possible and is documented.

## Prompt injection controls

Repository content, memory fragments, issue text, web pages, logs, tool output, and messages are wrapped as external untrusted content with source metadata.

The runtime policy prompt states the authority hierarchy, but hard enforcement remains in the tool broker.

Untrusted content cannot:

- modify the Pact;
- add a path;
- add or change a check;
- install a skill, plugin, or MCP server;
- choose a new provider;
- enable network;
- access credentials;
- approve a tool;
- increase attempts or budgets;
- change run mode;
- mark a run accepted.

A separate reader runtime may later summarize especially hostile inputs, but this is defense in depth and not part of the first implementation.

## Skills, plugins, and MCP

### Initial policy

- No dynamic skills.
- No MCP servers.
- No runtime plugin installation during runs.
- Provider integration plugins are bundled, pinned, and reviewed.

### Future policy

Every component requires:

- stable ID and version;
- source and license;
- exact hash;
- capability manifest;
- declared network and filesystem needs;
- compatibility range;
- evaluation results;
- human activation;
- rollback path.

MCP sampling and elicitation are disabled unless explicitly required and threat-modeled. MCP outputs are untrusted content. MCP is never the only enforcement layer.

## Egress policy

Modes:

- `denied`
- `loopback_only`
- `allowlisted`
- `unrestricted`, unavailable in the initial product

Initial behavior:

- checks receive no network tools or credentials;
- model provider may use only the configured loopback local gateway endpoint;
- remote model providers are not implemented;
- no web, browser, package installation, analytics, or telemetry;
- artifacts remain local.

The native macOS lane cannot honestly guarantee process-level network denial without stronger OS enforcement. A Pact that requires proven network denial must fail closed on that lane.

Security metrics use observable, scoped wording: TreePact can require zero broker escapes and zero known canary exposures. It cannot prove that arbitrary native repository code made no host-level write or network connection unless that process ran behind an independently verified enforcement boundary.

## Resource-exhaustion controls

- One global mutating run.
- One large-model lease.
- Attempts, turns, tokens, context, output, and time bounded.
- Check output and artifact sizes bounded.
- Memory pressure sampled before expensive actions.
- Critical pressure blocks new model/build work.
- Waiting does not consume an attempt.
- Process timeout and cancellation.
- Stale lease expiration and recovery.
- No unbounded subagents.

## Manual-only or unavailable actions

Always unavailable through the MVP runtime:

- commit;
- push;
- merge;
- publish;
- deploy;
- release;
- notarize;
- sign with distribution credentials;
- read Keychain;
- read SSH keys;
- modify the Pact;
- install or update a runtime integration;
- destructive deletion outside TreePact transient state;
- contact an external person or service.

The product should omit these tools rather than prompt repeatedly for them.

## Threat register

| Threat | Probability | Impact | Prevention | Detection | Recovery | Blocks initial release |
|---|---|---|---|---|---|---|
| Prompt injection in repository | High | Critical | Brokered tools and immutable Pact | Denied proposal events and adversarial fixtures | Cancel, preserve evidence, discard worktree | Yes |
| Malicious skill | Medium | Critical | No dynamic skills | Component inventory | Remove component, invalidate runs | Yes if skills enabled |
| Compromised MCP | Medium | Critical | MCP excluded | Process and egress inventory | Terminate, revoke tokens | Yes if MCP enabled |
| Model fabricates passed tests | High | High | Executor-owned check state | Compare claims with check records | Mark rejected or insufficient | Yes |
| Destructive command | Medium | Critical | No shell; named argv | Proposal and process audit | Kill group, recreate worktree | Yes |
| Infinite loop | High | High | Turn/time/attempt limits | Heartbeats and deadlines | Cancel process and resume safely | Yes |
| Secret leak | High | Critical | Env allowlist, path deny, no remote provider | Canary scan and egress receipts | Revoke exposed credential, quarantine the data root, stop further exports, and follow an explicit incident-recovery procedure; version 1 does not pretend to preserve a valid ledger while purging selected artifacts | Yes |
| Memory personal-data leak | Medium | Critical | Memory excluded initially | Context provenance | Disable adapter, invalidate memory | Yes when memory added |
| Access to another repository | Medium | Critical | Canonical root broker | Path event audit | Cancel and inspect host | Yes |
| Context mix between runs | Medium | High | Separate run/session/worktree IDs | Correlation invariant | Invalidate affected runs | Yes |
| Worktree corruption | Medium | High | Base and tree digests, locks | Git reconciliation | Recreate from base | Yes |
| Exposed local gateway | Medium | Critical | No gateway initially; loopback auth later | Bind and auth diagnostics | Stop service and rotate token | Yes when daemon added |
| Malicious Telegram input | Medium | Critical | Channel excluded initially | Sender and request logs | Revoke bot token | Yes when channel added |
| Concurrent large models freeze Mac | High | High | Global resource lease | local gateway and memory pressure | Release model, cancel run | Yes |
| Integration changes own rules | Medium | Critical | Pinned read-only policy snapshot | Hash and configuration events | Terminate and invalidate | Yes |
| Compromised dependency | Medium | Critical | Lockfile, no runtime installs | SBOM and advisory review | Roll back and rotate credentials | Yes for distribution |
| Check script modified by agent | High | Critical | Snapshot hash and execution block | Input-drift gate | Human review or new run | Yes |
| Native check accesses host data | Medium | Critical | Minimal env and trusted baseline only | Process/effect limitations are partial | Stop, inspect, restore | Must be disclosed; strong claim blocked |

## Security release gates

### S0: design

- Threat model complete.
- Data classes and retention documented.
- Unsupported guarantees stated.
- Tool surface closed.

### S1: filesystem and process

- Escape, symlink, path, and `.git` controls verified.
- No shell path exists.
- Child environment contains only approved fields.
- Cancellation and timeout verified.

### S2: models and content

- Prompt-injection corpus cannot expand capabilities.
- Invalid tool schemas fail closed.
- Secret fixture never reaches model or report.
- Provider fallback is explicit.

### S3: resources and recovery

- Concurrent large-model request is serialized.
- Critical pressure blocks expensive work.
- Interrupted runs reconcile safely.
- Uncertain effects prevent auto-resume.

### S4: integrations

- Runtime versions and plugins are observed.
- Missing hooks lower assurance or block according to policy.
- Final Git state is independent.
- Unknown runtime version fails safely.

No gate is marked passed until the final verification campaign records evidence.
