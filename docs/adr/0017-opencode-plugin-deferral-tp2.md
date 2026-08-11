# ADR 0017: OpenCode Defensive Plugin Deferral and TP2 Ceiling

- Status: accepted
- Date: 2026-08-11
- Supersedes: none (refines ADR 0016)

## Context

ADR 0016 and OPENCODE.md specify a pinned TypeScript defensive plugin for
the first OpenCode adapter. Loading OpenCode plugins requires bun, which is
not installed on the operator machine. Installing bun is a toolchain
installation from an external repository, which the implementation policy
(ADR 0011 and MEGA_PROMPT_IMPLEMENTATION.txt) prohibits before M9.

Without a loaded plugin, OpenCode's own tool inventory cannot be shown to
pass through the TreePact broker, so the documented TP3 condition ("If
OpenCode cannot be configured to remove bypassing tools reliably, the
adapter assurance ceiling is TP2") applies.

## Decision

- Ship the pinned defensive plugin as reviewed source under
  `integrations/opencode/treepact-plugin.ts`; it is not loaded in M8.
- The M8 adapter uses: dedicated server process, loopback-only binding,
  mDNS off, empty CORS, run-specific isolated XDG_CONFIG_HOME and
  XDG_CACHE_HOME, generated configuration that enables only a loopback
  provider and disables every bypass tool (bash, write, edit, patch, web,
  http, task, subagents), startup inventory that rejects any remote
  provider, remote credential, unexpected plugin, MCP server, or skill,
  and configuration-drift detection.
- The adapter assurance ceiling is TP2 for all controlled OpenCode runs in
  M8. TreePact's final gates and Git reconciliation remain independent.
- Loading the plugin (with bun) is a post-M9 decision and requires the
  plugin's capability manifest and a focused verification pass.

## Follow-up 2026-08-11 (executed by operator order)

bun 1.3.14 was installed and the plugin was verified: its hooks execute
(bash and mcp__* denied, list_files allowed) and OpenCode loads it. The
adapter now includes the plugin in the generated configuration when bun is
available (`_plugin_path`), pins the model explicitly in session creation
(`treepact-local/local-model` via ModelRef; API-created sessions do not
inherit the config default model), and the Stage 8 runtime comparison
passed with the plugin active (verification/stage_runtime.json). The TP2
ceiling stays until a dedicated TP3 verification pass measures plugin
coverage of the full tool inventory.

## Consequences

OpenCode runs are instrumented, not TP3. A controlled session cannot
perform repairs through TreePact tools until the plugin loads; read-only
diagnosis through the controlled configuration remains the verified path.
The version compatibility and event correlation machinery is complete and
validated, so enabling the plugin later does not change the adapter
contract.
