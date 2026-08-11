# ADR 0010: local gateway as Optional Provider Adapter

- Status: accepted
- Date: 2026-08-11

## Context

local gateway already manages local models and exposes an OpenAI-compatible loopback gateway, but TreePact must remain standalone.

## Decision

Integrate local gateway only through documented loopback HTTP and model-profile configuration. Do not import code, read its database, or require it for validation and evidence workflows.

## Consequences

TreePact can use another compatible local provider. local gateway failure pauses or fails model work explicitly and does not corrupt run state. No remote fallback occurs silently.
