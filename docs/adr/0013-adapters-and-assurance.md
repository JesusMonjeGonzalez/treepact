# ADR 0013: Runtime Adapters and Assurance Levels

- Status: accepted
- Date: 2026-08-11

## Context

OpenCode, Claude Code, and future agents expose different session, tool, and hook interfaces. Instrumentation does not provide equal control.

## Decision

Normalize runtime behavior through adapter ports and assign TP0 through TP3 assurance based on observed control. Provider session IDs and events remain external metadata.

## Consequences

The same Pact and gate engine can supervise multiple runtimes. Missing hooks, unknown versions, event gaps, or external sessions reduce assurance honestly rather than being hidden.
