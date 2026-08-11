# ADR 0004: SQLite and Filesystem Artifacts

- Status: accepted
- Date: 2026-08-11

## Context

Runs require durable structured state and potentially large stdout, stderr, patches, and reports.

## Decision

Store current state and event metadata in SQLite. Store large artifacts in a content-addressed local filesystem. Use SQL explicitly and numbered forward-only migrations.

## Consequences

No database service is required. SQLite and artifacts must be backed up and restored as one consistent package. Clients must never open the database directly after a daemon is introduced.
