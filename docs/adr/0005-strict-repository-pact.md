# ADR 0005: Strict Repository Pact

- Status: accepted
- Date: 2026-08-11

## Context

Provider instruction files are useful context but are not deterministic capability contracts.

## Decision

Each registered repository uses a strict, versioned `.treepact.yaml`. Unknown fields fail. TreePact canonicalizes and hashes the Pact once per run. JSON Schema is supplemented by semantic validation for commands and paths.

## Consequences

The contract is portable between runtimes. Pact changes require a new run. Repository onboarding requires explicit review rather than automatic authorization of discovered commands.
