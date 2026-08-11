# ADR 0001: Standalone Product

- Status: accepted
- Date: 2026-08-11

## Context

TreePact coordinates repositories, agents, policy, worktrees, resources, and evidence. A plugin or module inside another product would make its state and security lifecycle dependent on that host.

## Decision

TreePact has its own repository, package, database, configuration, release cycle, formats, and documentation. Other portfolio products connect only through optional adapters.

## Consequences

TreePact can run without a local gateway, memory vault, agent runtime, or editor. It must not import their internals or share databases. This adds an independent package to maintain but prevents portfolio coupling.
