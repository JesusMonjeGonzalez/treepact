# ADR 0016: OpenCode First External Runtime

- Status: accepted
- Date: 2026-08-11

## Context

M8 needs a second runtime to prove Pact neutrality. The initial product permits local provider traffic through local gateway but does not authorize remote code egress.

## Decision

Implement OpenCode first through its documented loopback server, events, and a pinned defensive plugin while using a local provider. Keep Claude Code documented but unimplemented until a separate privacy and remote-egress ADR is approved.

## Consequences

M8 remains compatible with the local-only security boundary. Claude Code support cannot be added opportunistically and must define consent, provider handling, credentials, and Pact egress semantics first.
