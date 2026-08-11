// TreePact defensive OpenCode plugin (pinned source).
//
// Defense in depth, never the final authority. TreePact's policy core and
// tool broker remain the enforcement point; this plugin only denies bypass
// tools early and reports provider events for correlation.
//
// Loading this plugin requires bun, which is a toolchain installation
// deferred to M9 (per ADR 0017). Until then the adapter runs with
// --pure-style config restriction and an assurance ceiling of TP2.
//
// Hook contract per https://opencode.ai/docs/plugins/ (2026-08-11):
// each hook receives typed input and returns an object with a `result`
// property.

// Tools that must never reach the model in a controlled run. OpenCode
// built-ins that bypass the TreePact broker are denied here even when a
// future configuration bug re-enables them.
const BYPASS_TOOLS = new Set([
  "bash",
  "write",
  "edit",
  "patch",
  "webfetch",
  "http",
  "task",
  "subagents",
  "kill",
  "stash",
  "browser",
  "mcp__*",
]);

export async function toolExecuteBefore(input) {
  const toolName = input?.tool?.name ?? "";
  if (BYPASS_TOOLS.has(toolName) || toolName.startsWith("mcp__")) {
    return {
      result: {
        type: "error",
        error: `denied by TreePact plugin: tool ${toolName} bypasses the TreePact broker`,
      },
    };
  }
  return { result: { type: "allow" } };
}

export async function toolExecuteAfter(input) {
  // Provider-reported completion is a signal for TreePact correlation; the
  // authoritative execution record is written by the TreePact broker.
  return { result: { type: "allow" } };
}

export async function permissionAsked(input) {
  return { result: { type: "allow" } };
}

export async function sessionCreated(input) {
  return { result: { type: "allow" } };
}

export async function sessionIdle(input) {
  return { result: { type: "allow" } };
}

export async function sessionError(input) {
  return { result: { type: "allow" } };
}

export async function sessionStatus(input) {
  return { result: { type: "allow" } };
}

export async function fileEdited(input) {
  // Signals a potential filesystem change for TreePact reconciliation.
  return { result: { type: "allow" } };
}
