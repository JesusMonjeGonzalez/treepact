# Architectural Decision Records

The implementation agent must create one Markdown ADR per decision below using status, context, decision, consequences, alternatives, and supersession fields.

## Accepted decisions

| ADR | Decision |
|---|---|
| 0001 | TreePact is a standalone product and repository |
| 0002 | Python 3.12 is the initial core language |
| 0003 | Use a modular monolith, not services |
| 0004 | Use SQLite plus filesystem artifacts |
| 0005 | Use strict `.treepact.yaml` contracts |
| 0006 | Build a minimal native tool loop first |
| 0007 | Never expose arbitrary shell to runtimes |
| 0008 | Worktrees isolate changes but are not sandboxes |
| 0009 | TreePact calculates gates independently from runtimes |
| 0010 | Loopback OpenAI-compatible gateway adapter |
| 0011 | Formal test implementation and execution are consolidated at the end |
| 0012 | No CI/CD scope |
| 0013 | External runtimes use adapters and assurance levels |
| 0014 | No merge, push, publish, deploy, or release operations |
| 0015 | Native macOS checks use a trusted-harness lane until stronger isolation exists |
| 0016 | OpenCode is the first external runtime; Claude Code requires a later egress ADR |
| [0019](0019-repository-secret-scanning.md) | Repository secret scanning only; narrow exception to ADR 0012, no product CI/CD capability |

## Deferred decisions

- Daemon and launchd supervision.
- Local API transport.
- Privacy and egress expansion required before a Claude Code runtime adapter.
- Rust helper.
- SwiftUI desktop client.
- VS Code extension.
- Local vault adapter.
- Chat channel.
- SQLCipher.
- Evidence signatures.
- Public bundle specification.
- Public license and distribution format.
- Strong native macOS isolation.

Deferred decisions must not be implemented opportunistically. Their documented gate must be met first.
