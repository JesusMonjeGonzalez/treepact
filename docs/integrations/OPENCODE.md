# OpenCode Integration

## Official surfaces considered

As documented on 2026-08-11, OpenCode provides:

- `opencode serve`, a loopback HTTP server with OpenAPI 3.1.
- Session, message, abort, diff, permission, file, and event APIs.
- Server-Sent Events for session and global events.
- A generated TypeScript SDK.
- `opencode acp`, using JSON-RPC over stdio.
- JavaScript or TypeScript plugins with lifecycle and tool hooks.
- Custom tools through the plugin API.

Official references:

- https://opencode.ai/docs/server/
- https://opencode.ai/docs/sdk/
- https://opencode.ai/docs/acp/
- https://opencode.ai/docs/plugins/

## Recommended integration

TreePact starts a dedicated OpenCode server inside the TreePact worktree and communicates through the documented HTTP API.

```text
TreePact Run Engine
        |
        | loopback HTTP + SSE
        v
Pinned OpenCode server
        |
        v
TreePact-controlled worktree
```

TreePact's Python core uses the OpenAPI HTTP surface directly through HTTPX. It does not add Node or the TypeScript SDK as a core dependency.

## Server startup

Conceptual launch:

```text
opencode serve --hostname 127.0.0.1 --port <ephemeral-port>
```

Requirements:

- random available port selected by TreePact;
- random ephemeral server password;
- basic-auth credentials passed through a protected environment or process channel;
- loopback only;
- mDNS disabled;
- no CORS origins;
- cwd set to the TreePact worktree;
- sanitized environment;
- run-specific `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` so user/global OpenCode configuration is not inherited;
- generated configuration containing only the pinned TreePact plugin and the selected loopback OpenAI-compatible provider;
- no remote-provider credentials in the child environment;
- startup inventory proving the effective provider, tools, MCP set, and loaded plugins;
- runtime version checked before session creation;
- process group owned by the run;
- server disposed and terminated after final event collection.

TreePact never connects to an arbitrary OpenCode port discovered on the machine.

## Allowed API usage

TreePact may use documented endpoints for:

- global health and version;
- current project and path confirmation;
- session creation and status;
- async prompts;
- session messages and structured parts;
- session abort;
- permission response;
- diff observation;
- event streams;
- instance disposal.

TreePact must not call:

- the session shell endpoint;
- auth mutation endpoints;
- session sharing endpoints;
- dynamic MCP installation;
- configuration mutation unrelated to the ephemeral controlled session;
- experimental tools as a stable contract;
- undocumented routes.

OpenCode's diff endpoint is useful context but does not replace TreePact's independent Git reconciliation.

## Tool restriction

The controlled profile must remove or deny OpenCode tools that bypass TreePact, particularly shell and direct mutation tools. The exact mechanism must be verified against the pinned OpenCode version.

Preferred controlled surface:

- repository reads through TreePact tools;
- repository searches through TreePact tools;
- patches through TreePact `apply_patch`;
- checks through TreePact `run_check`;
- no OpenCode shell;
- no OpenCode Git publication tools;
- no dynamic MCP;
- no browser;
- no session sharing.

If OpenCode cannot be configured to remove bypassing tools reliably, the adapter assurance ceiling is TP2 and cannot be used as the first TP3 external runtime.

## TreePact OpenCode plugin

M8 includes a project-scoped, pinned TypeScript plugin providing defense in depth and provider events. It is not deferred beyond the adapter milestone.

Expected hooks:

| Hook | Use |
|---|---|
| `tool.execute.before` | Reject disallowed built-in tools and validate expected session/run correlation |
| `tool.execute.after` | Record provider-reported completion |
| `permission.asked` | Correlate permission events |
| `permission.replied` | Correlate provider permission result |
| `file.edited` | Signal potential filesystem change |
| `session.created` | Confirm session start |
| `session.status` | Observe progress |
| `session.idle` | Detect natural pause or finish |
| `session.error` | Capture runtime error |
| `session.diff` | Signal provider diff update |

The plugin may expose custom TreePact tools backed by a local authenticated broker. The plugin does not implement policy itself.

## Plugin risks

- Plugins run inside OpenCode and receive powerful context.
- Other plugins may run before or after the TreePact plugin.
- npm plugins are installed through Bun and add supply-chain risk.
- A local or global configuration can add unexpected plugins.
- A process may write files without a plugin event.
- Plugin event schemas can evolve.

Controls:

- use a local pinned plugin bundled with TreePact;
- no automatic npm installation during a run;
- isolate OpenCode configuration for the controlled run;
- inventory loaded plugins and reject unexpected ones;
- independently inspect final Git state;
- cap assurance when expected hooks are absent or events have gaps.
- reject startup if any provider endpoint is non-loopback, any remote credential is visible, MCP is enabled, or an unexpected plugin/tool is present;
- record that loopback configuration prevents intended remote use but does not prove process-level egress denial on the native lane.

## ACP

OpenCode ACP runs as a subprocess over JSON-RPC stdio and is useful for future editor integrations.

Good ACP uses:

- prompt and session lifecycle;
- progress display;
- cancellation;
- capability negotiation;
- editor-mediated permission UX.

ACP is not selected as the initial TreePact runtime transport because it is primarily an editor-agent protocol and OpenCode retains its built-in tool environment. ACP does not prove that all filesystem effects passed through TreePact.

The future VS Code extension may use ACP for UI while the TreePact daemon remains the authority.

## Failure behavior

| Failure | TreePact behavior |
|---|---|
| Server fails health probe | Fail before runtime session |
| Version unsupported | Fail preflight as `runtime_incompatible`, return 15, and produce a pre-workspace bundle if a run record exists |
| Event stream disconnects | Attempt bounded reconnect; mark coverage gap |
| Plugin not loaded | Abort TP3 run or reduce assurance according to explicit policy |
| Unexpected plugin loaded | Abort controlled run |
| OpenCode requests shell | Deny and record |
| Session abort fails | Kill recorded OpenCode process group |
| Provider reports idle with active process | Continue observing until explicit bounded resolution |
| Final OpenCode diff differs from Git | Git state wins; mark discrepancy |

## User experience

```bash
treepact run "Fix the parser regression" --runtime opencode --mode repair
```

TreePact displays:

- OpenCode version;
- session ID as external metadata;
- integration assurance;
- current provider event;
- policy denials;
- independent worktree state;
- final gate results.

The user should not need to configure OpenCode global plugins for a controlled TreePact run.
