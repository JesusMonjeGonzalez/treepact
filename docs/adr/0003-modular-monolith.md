# ADR 0003: Modular Monolith

- Status: accepted
- Date: 2026-08-11

## Context

TreePact has several security and lifecycle concerns but one user, one machine, and no need for distributed scaling.

## Decision

Use one modular Python application with explicit domain, application, port, and adapter boundaries. Do not introduce microservices, queues, Redis, or network APIs initially.

## Consequences

Transactions, debugging, installation, and recovery remain simple. A daemon can later host the same application layer if foreground runs prove inadequate.
