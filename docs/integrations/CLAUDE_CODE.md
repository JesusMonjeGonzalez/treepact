# Claude Code Integration

## Official surfaces considered

As documented on 2026-08-11, Claude Code provides:

- programmatic CLI mode through `claude -p`;
- session continuation and resumption;
- structured output and tool restrictions through CLI options;
- hooks across CLI, IDE extension, desktop, and web environments;
- `PreToolUse`, `PermissionRequest`, `PostToolUse`, session, worktree, subagent, configuration, and stop events;
- an official VS Code extension that bundles its own CLI;
- shared Claude settings and hooks between CLI and the VS Code extension;
- a documented `claudeCode.claudeProcessWrapper` VS Code setting;
- worktree and background-session support;
- an Agent SDK for deeper programmatic integration.

Official references:

- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/vs-code
- https://platform.claude.com/docs/en/agent-sdk/overview

## Integration modes

Claude Code integration is documented for future compatibility but is outside the initial local-only implementation. Enabling it requires an ADR covering remote code egress, provider data handling, credentials, consent, and Pact representation. M8 implements OpenCode first.

### Controlled CLI

TreePact launches a pinned standalone Claude Code CLI inside the TreePact worktree in non-interactive programmatic mode.

Requirements:

- explicit settings file owned by TreePact;
- explicit tool inventory;
- TreePact plugin or hooks supplied explicitly;
- no browser or Chrome integration;
- no remote control;
- no background agent supervisor unless separately designed;
- no extra directories;
- no dynamically discovered MCP servers;
- no bypass-permissions mode;
- sanitized environment;
- provider and authentication handled by Claude Code without exposing credentials to TreePact;
- structured machine-readable output;
- process group controlled by TreePact.

The exact CLI flags must be checked against the pinned Claude Code release during adapter implementation. The adapter must not assume undocumented flags based solely on `--help` output.

### Instrumented CLI or VS Code session

Claude Code hooks call a narrow TreePact hook handler. This allows policy blocking and event capture but does not establish full process control.

### Agent SDK

The Agent SDK is a later option if TreePact needs a deeply embedded Claude runtime with explicit permission callbacks and structured streaming.

It is not selected initially because:

- it introduces provider-specific code into the runtime layer;
- authentication and commercial terms may differ from the official Claude Code experience;
- it risks turning TreePact into a Claude-specific product;
- the native loop and CLI adapter are sufficient to validate the product first.

## Hook strategy

Priority events:

| Hook | TreePact use |
|---|---|
| `SessionStart` | Register external session and verify integration metadata |
| `InstructionsLoaded` | Record that external instructions entered context |
| `PreToolUse` | Deny tools or parameters outside Pact |
| `PermissionRequest` | Provide a deterministic decision when supported |
| `PostToolUse` | Correlate provider-reported tool completion |
| `PostToolUseFailure` | Capture provider tool failure |
| `PostToolBatch` | Reconcile parallel tool batch |
| `SubagentStart` | Record delegation and enforce adapter policy |
| `SubagentStop` | Close delegated event range |
| `WorktreeCreate` | Detect provider-created worktree outside TreePact policy |
| `WorktreeRemove` | Detect provider cleanup |
| `ConfigChange` | Invalidate controlled assumptions when configuration changes |
| `CwdChanged` | Detect movement outside expected cwd |
| `DirectoryAdded` | Deny unexpected additional roots |
| `Stop` | Record turn completion, not run acceptance |
| `StopFailure` | Capture runtime failure |
| `SessionEnd` | Close provider session metadata |

## Hook implementation constraints

- Use command hooks in exec form with explicit `args`, not shell form.
- The hook executable receives JSON on stdin and returns documented JSON.
- Hook timeout is short for `PreToolUse`.
- A TreePact-controlled action fails closed if the policy handler is unavailable.
- Do not use prompt hooks or agent hooks for hard policy.
- Do not rely on the `if` matcher as a security parser; Claude documentation describes it as best-effort filtering.
- Multiple matching hooks may run in parallel, so TreePact must not assume ordering.
- Post hooks are evidence signals, not proof of final filesystem state.

## Tool policy

For a TP3 controlled run, the Claude Code tool inventory must exclude unrestricted Bash, direct writes that bypass the broker, browser, arbitrary MCP, Git publication, and additional directory registration.

Preferred tool surface:

- TreePact read tool;
- TreePact search tool;
- TreePact patch tool;
- TreePact named-check tool;
- finish/report tool.

If Claude Code cannot expose this surface without retaining bypass tools, the integration remains TP2.

## VS Code official extension

The official extension offers its own UI, inline diffs, plans, sessions, permission modes, and a bundled CLI. TreePact must coexist rather than replace it.

### Instrumented coexistence

Claude settings and hooks are shared between CLI and extension. A TreePact hook package can therefore observe and block future tool events in extension sessions.

This mode is TP2 at most because TreePact did not necessarily create the worktree or process.

### Controlled wrapper

The documented `claudeCode.claudeProcessWrapper` setting can launch the bundled CLI through a TreePact wrapper. A future integration may use it to:

- verify the binary version;
- inject controlled settings and hooks;
- sanitize the environment;
- register process lifecycle;
- associate the session with a TreePact worktree.

The wrapper must not inspect or modify private communication between the extension and its bundled CLI.

The feasibility of changing cwd or selecting a TreePact worktree through this wrapper must be proven against the documented extension behavior before promising TP3.

### Decisions remain separate

The extension's edit approval means that Claude may write a proposed edit. TreePact's decision means that the exact final tree satisfied the Pact. One does not imply the other.

## Built-in IDE MCP server

The official extension runs a loopback IDE MCP server for editor integration. TreePact must not:

- read its lock files;
- reuse its authorization token;
- connect through undocumented methods;
- depend on its internal RPC;
- treat editor diagnostics as authoritative checks.

Diagnostics may be useful context through documented Claude behavior, but TreePact checks remain independent.

## Data and authentication

TreePact does not copy Claude credentials. Claude Code retains its own authentication.

The adapter records:

- runtime version;
- authentication mode category when safely exposed;
- model identifier or alias;
- session ID;
- usage and cost metadata when provided;
- tool and hook coverage;
- provider errors.

It does not persist tokens or full Claude transcripts by default.

## Prohibited integration techniques

- Parsing `~/.claude` internal transcripts as an API.
- Modifying provider session files.
- Reading VS Code extension global storage.
- Depending on undocumented VS Code commands.
- Enabling `bypassPermissions` to reduce prompts.
- Granting `--add-dir` outside the TreePact worktree.
- Using MCP alone as enforcement.
- Allowing web or Chrome sessions for controlled local repository runs.
- Asking Claude to declare the TreePact outcome.

## Failure behavior

| Failure | TreePact behavior |
|---|---|
| Unsupported CLI version | Reject before session |
| Required hooks not loaded | Abort TP3 or cap assurance |
| Hook timeout | Deny controlled side effect and record gap |
| Claude config changes | Invalidate assumptions and require review |
| Additional directory added | Deny or terminate controlled run |
| Subagent appears unexpectedly | Deny capability or reduce assurance |
| Process exits without final event | Mark interrupted or runtime failure |
| Transcript and Git differ | Git state wins |
| Official extension session adopted late | Mark pre-adoption history opaque |

## User experience

Controlled CLI:

```bash
treepact run "Fix the timeline regression" --runtime claude-code --mode repair
```

VS Code coexistence:

- TreePact status view identifies the session as external, adopted, instrumented, or controlled.
- Claude's panel remains the conversation UI.
- TreePact displays Pact, gates, independent diff, and evidence.
- TreePact never displays a misleading green status solely because Claude stopped successfully.
