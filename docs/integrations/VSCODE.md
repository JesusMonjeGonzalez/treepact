# VS Code Integration

## Role

The future TreePact VS Code extension is a client for the TreePact local service. It is not a second policy engine, runtime, or evidence store.

## Gate before implementation

Do not create the extension until:

- at least thirty real CLI runs exist;
- at least three repositories have stable Pacts;
- the run state machine is stable;
- a daemon or local application API is justified and versioned;
- users need long-running status and evidence review inside the editor;
- native and at least one external runtime work through the same core.

## Technology

- TypeScript.
- Official VS Code Extension API.
- Minimal bundled dependencies.
- No Electron application.
- Communication with TreePact daemon through authenticated local API.
- No SQLite access.
- No direct model-provider access.

## Views

### Run explorer

Displays active, waiting, interrupted, completed, and review-needed runs for the current registered repository.

### Run detail

Displays:

- task;
- state;
- attempt and turn;
- runtime and assurance;
- elapsed and remaining budget;
- current action;
- memory pressure;
- policy denials.

### Pact view

Displays canonical Pact, validation diagnostics, effective limits, and Pact hash. Editing opens `.treepact.yaml` as a normal file and requires explicit revalidation. The extension does not auto-rewrite the Pact.

### Diff view

Uses VS Code diff APIs to display the exact TreePact worktree patch. Approval controls, if later added, bind to TreePact's canonical hashes rather than editor buffer state.

### Evidence view

Displays gates, checks, attempts, artifacts, and discrepancies. It clearly distinguishes provider-reported events from TreePact-captured execution evidence.

## Commands

Potential Command Palette entries:

```text
TreePact: Initialize Repository
TreePact: Validate Pact
TreePact: Start Observe Run
TreePact: Start Repair Run
TreePact: Open Run
TreePact: Cancel Run
TreePact: Resume Run
TreePact: Open Worktree
TreePact: Show Diff
TreePact: Show Evidence
TreePact: Verify Evidence Hashes
TreePact: Open User Guide
```

No extension command may merge, push, publish, deploy, or release.

## Coexistence with Claude Code extension

- Do not import Anthropic extension internals.
- Do not read Anthropic global storage.
- Do not connect to its hidden IDE MCP server.
- Do not intercept private extension protocol.
- Use documented settings and hooks only.
- Show TreePact runs in a separate view.
- Do not duplicate Claude chat UI.
- Do not label Claude edit acceptance as TreePact acceptance.
- A deep link may open an official session only when Anthropic documents the URI and the workspace matches.

## Coexistence with OpenCode

- Do not control OpenCode's official extension through undocumented commands.
- TreePact may start a controlled OpenCode server through its daemon.
- Future ACP support can power an editor session while TreePact retains policy authority.
- OpenCode server and session IDs remain adapter metadata.

## Workspace Trust

In VS Code Restricted Mode, the TreePact extension must operate read-only:

- display existing TreePact evidence;
- display documentation;
- display Pact text without executing it;
- refuse to launch agents;
- refuse to run checks;
- refuse to activate repository hooks or plugins;
- refuse to create a worktree from untrusted configuration.

The extension declares limited Workspace Trust support and explains why an action is unavailable.

## Remote environments

For Remote SSH, Dev Containers, or other remote workspaces:

- the TreePact daemon must run where the repository and worktree live;
- process execution and policy remain on that host;
- the UI extension may run locally or remotely according to VS Code extension-host rules;
- source code is not copied to the local client merely for TreePact;
- remote support is a future product decision and not implied by local macOS support.

## Authentication

The extension connects only to a TreePact instance it launched, paired with, or explicitly selected. Authentication uses a short-lived local token or OS-protected socket semantics. Session IDs are not credentials.

## Failure behavior

- Daemon unavailable: show disconnected; do not fall back to direct execution.
- API version mismatch: show upgrade requirement; do not issue mutating calls.
- Workspace differs from run: show evidence read-only.
- Editor buffer differs from worktree: show stale warning.
- Extension reload: reconnect and rebuild views from daemon state.
- Missing runtime integration: allow native TreePact run or explain incompatibility.

## Non-goals

- Chat.
- Agent marketplace.
- Model configuration UI.
- Terminal replacement.
- Git client.
- CI dashboard.
- Remote fleet management.
- Browser automation.
- Editing provider settings.

## Accessibility

- All views and commands are operable by keyboard.
- Tree items and status icons have accessible names and text equivalents.
- Gate and decision state never depends on color alone.
- Focus order is deterministic after refresh or reconnect.
- Progress announcements are rate-limited for screen readers.
- Diff and evidence views preserve copyable plain-text alternatives.
- Zoom, high-contrast themes, and reduced motion follow VS Code settings.
