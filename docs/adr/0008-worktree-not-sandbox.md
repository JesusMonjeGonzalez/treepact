# ADR 0008: Worktree Is Not a Sandbox

- Status: accepted
- Date: 2026-08-11

## Context

Git worktrees isolate file state for parallel changes but share Git metadata and do not constrain processes.

## Decision

Use one detached worktree per run for change isolation. Never describe that boundary as process, filesystem, or network sandboxing.

## Consequences

TreePact independently brokers runtime file tools and protects `.git`. Native checks remain trusted repository code with host authority and require explicit disclosure.
