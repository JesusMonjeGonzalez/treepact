# ADR 0007: No Arbitrary Shell

- Status: accepted
- Date: 2026-08-11

## Context

Arbitrary shell collapses filesystem, process, credential, and network controls into prompt-level policy.

## Decision

Runtimes receive no shell tool. Repository checks are selected by ID and executed as exact argv without a shell. Pact semantic validation rejects shell executables, shell `-c`, and interpreter inline evaluation.

## Consequences

Some agent workflows are unavailable. Repositories must expose stable named checks. Ordinary metacharacters in argv remain literal because no shell parses them.
