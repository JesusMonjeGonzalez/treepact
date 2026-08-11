# ADR 0002: Python 3.12 Core

- Status: accepted
- Date: 2026-08-11

## Context

The first product needs rapid implementation, SQLite, process control, HTTP model integration, and compatibility with the existing local Python stack.

## Decision

Use Python 3.12 for the core and `uv` for environment and dependency locking.

## Consequences

Development and local gateway integration are direct. Initial distribution uses a managed Python tool environment. Rust may later implement a narrow helper, and Swift may later implement a client, without replacing the core by default.
