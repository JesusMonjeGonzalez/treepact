# ADR 0009: Independent Gates

- Status: accepted
- Date: 2026-08-11

## Context

Models and agent runtimes can misreport tests, omit failures, or summarize a different workspace state.

## Decision

TreePact executes checks, captures outputs and exit states, reconciles Git, and calculates every final gate. Runtime summaries are non-authoritative context.

## Consequences

Provider integrations need not agree on transcript format. TreePact may return `needs_review` even when the agent claims success.
