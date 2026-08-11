# ADR 0014: No Publication Actions

- Status: accepted
- Date: 2026-08-11

## Context

Push, merge, release, signing, and deployment create irreversible or externally visible effects and require credentials.

## Decision

TreePact version 1 has no commit, push, merge, PR, publish, deploy, release, signing, or notarization tools or CLI commands.

## Consequences

The final product output is a worktree, patch, and Decision Bundle for human review. These actions cannot be enabled by Pact configuration because the capabilities do not exist.
