"""Domain and policy.

Ownership boundary: this package contains Pact semantics, state machines,
policy decisions, gates, and the run engine. Domain modules never import
Typer, HTTPX, SQLite, Git, provider SDKs, OpenCode, Claude Code, Loopback,
or VS Code. Adapters implement ports defined here.
"""
