"""State enums from STATE_AND_DATA_MODEL.md.

No state transition is ever performed with ad hoc strings. Every persisted
state value is an Enum member with a stable serialized name.
"""

from __future__ import annotations

from enum import Enum


class RunState(str, Enum):
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    WAITING_RESOURCE = "waiting_resource"
    VERIFYING = "verifying"
    INTERRUPTED = "interrupted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"

    @property
    def terminal(self) -> bool:
        return self in {
            RunState.ACCEPTED,
            RunState.REJECTED,
            RunState.NEEDS_REVIEW,
            RunState.CANCELLED,
            RunState.FAILED,
            RunState.INFRASTRUCTURE_ERROR,
        }

    @property
    def resumable(self) -> bool:
        return self in {RunState.INTERRUPTED}


class AttemptState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    ACTING = "acting"
    CHECKING = "checking"
    PASSED = "passed"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class CheckState(str, Enum):
    DECLARED = "declared"
    QUEUED = "queued"
    STARTED = "started"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    BLOCKED_INPUT_CHANGED = "blocked_input_changed"


class ToolProposalState(str, Enum):
    PROPOSED = "proposed"
    ALLOWED = "allowed"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    DENIED = "denied"
    STALE = "stale"


class LeaseState(str, Enum):
    REQUESTED = "requested"
    GRANTED = "granted"
    ACTIVE = "active"
    RELEASED = "released"
    QUEUED = "queued"
    EXPIRED = "expired"
    DENIED = "denied"
    REVOKED = "revoked"


class CommandState(str, Enum):
    REQUESTED = "requested"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class WorkspaceState(str, Enum):
    NOT_CREATED = "not_created"
    ACTIVE = "active"
    RECONCILED = "reconciled"
    REMOVED = "removed"


class GateState(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class GateId(str, Enum):
    REQUIRED_CHECKS_PASS = "required_checks_pass"
    NO_DENIED_PATHS_CHANGED = "no_denied_paths_changed"
    NO_SECRETS_IN_DIFF = "no_secrets_in_diff"
    WORKTREE_CONSISTENT = "worktree_consistent"
    EVIDENCE_COMPLETE = "evidence_complete"


class AssuranceLevel(str, Enum):
    TP0 = "TP0"
    TP1 = "TP1"
    TP2 = "TP2"
    TP3 = "TP3"


class RunMode(str, Enum):
    OBSERVE = "observe"
    REPAIR = "repair"


class CheckPhase(str, Enum):
    ATTEMPT = "attempt"
    FINAL = "final"


class NetworkMode(str, Enum):
    DENIED = "denied"
    LOOPBACK_ONLY = "loopback_only"


class RuntimeId(str, Enum):
    NATIVE = "native"
    OPENCODE = "opencode"
    CLAUDE_CODE = "claude-code"


class PrivacyClass(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
