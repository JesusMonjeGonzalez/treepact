# ADR 0011: Final Consolidated Verification

- Status: accepted by operator constraint
- Date: 2026-08-11

## Context

The operator wants automated tests and expensive verification deferred to the end and wants to avoid repeated executions.

## Decision

Define requirements, risks, fixtures, events, and oracles from M0, but implement and execute formal suites in M9 against a frozen candidate. M1-M8 checkpoints are `implemented_unverified`.

## Consequences

Late defect and rework risk increases. Scope and contracts must remain narrow. M9 runs full suites once, then reruns only failed scenarios, direct dependencies, and affected critical smoke paths.
