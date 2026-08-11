# ADR 0018: Claude Code Runtime - Egress Decision Gate

- Status: proposed
- Date: 2026-08-11
- Supersedes: none (documents the pending decision referenced by
  INTEGRATION_STRATEGY.md and the implementation constraint "dejar Claude
  Code para una posterior decision de egress")

## Context

The implementation milestone M8 selected OpenCode as the first external
runtime. Claude Code is explicitly deferred to a later decision because it
requires an egress decision: Claude Code operates as a remote agent service
whose traffic exits the operator machine.

TreePact's version 1 security model is loopback-only: no remote provider,
no remote credential, no egress (SECURITY_MODEL.md, ADR 0014). The native
and OpenCode lanes observe this. Claude Code cannot observe it as shipped:
the model conversation is handled by Anthropic's remote API.

## Decision (not yet taken - requires operator authorization)

Adoption of a Claude Code lane requires ALL of:

1. An explicit egress ADR that identifies: the exact traffic (conversation
   content, file paths, diffs, prompts), the destination endpoints, the
   retention and zero-retention commitments of the provider, and the
   jurisdiction of the data path.
2. A documented privacy class for each repository permitted to use the
   lane; `sensitive` repositories are excluded by default.
3. A capability bridge that maps Claude Code's tool set to the TreePact
   tool protocol (or a verifiable proof that TreePact's gates and Git
   reconciliation remain authoritative despite an unrestricted tool
   surface), with assurance ceiling assigned accordingly (likely TP2, never
   TP3 unless the tool surface is verifiably restricted).
4. Version pinning and inventory checks analogous to the OpenCode adapter
   (runtime version, provider endpoints, unexpected plugins).
5. No silent fallback: a Claude Code run that cannot start fails
   explicitly; TreePact never routes a run to Claude Code because another
   lane is down.

Until the operator authorizes this egress decision, the CLI rejects
`--runtime claude-code` with `runtime_invalid`/`not_implemented` and the
runbook does not reference it.

## Consequences

Keeping Claude Code out of M10 keeps the pilot loopback-only and
measurable against the security model. The egress decision, if taken
later, is a product change requiring a new ADR, a new verification pass of
the affected invariants (SE-* and TOOL-*), and an updated threat model.
