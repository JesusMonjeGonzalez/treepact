# ADR 0006: Native Loop First

- Status: accepted
- Date: 2026-08-11

## Context

External agents already provide rich loops but also own tools, sessions, permissions, and update cycles. TreePact first needs to prove its policy and evidence model.

## Decision

Implement a minimal TreePact-owned reasoning loop exposing only list, read, search, patch, named check, and finish tools.

## Consequences

TreePact controls the first vertical slice and can evaluate external runtimes later. The loop must remain small and must not grow into a general agent framework.
