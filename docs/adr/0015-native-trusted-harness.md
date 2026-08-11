# ADR 0015: Native Trusted-Harness Lane

- Status: accepted
- Date: 2026-08-11

## Context

Some native macOS toolchains that cannot run correctly in ordinary Linux containers.

## Decision

Run approved native checks with fixed argv, sanitized environment, temporary HOME, timeouts, protected input hashes, and resource budgets. Label the lane `trusted_harness`, never `sandboxed`.

## Consequences

TreePact cannot prove complete host filesystem or network confinement for these checks. Stronger isolation through a dedicated user or VM remains future work.
