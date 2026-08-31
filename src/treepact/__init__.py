"""TreePact: local, verifiable change control for coding agents.

TreePact supervises coding-agent runs inside isolated Git worktrees. A
repository declares an executable pact, TreePact constrains the available
actions, executes the declared checks itself, and produces an evidence
bundle for human review.
"""

__version__ = "0.2.0"

PACT_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
BUNDLE_SCHEMA_VERSION = 1
RUNTIME_MANIFEST_SCHEMA_VERSION = 1
