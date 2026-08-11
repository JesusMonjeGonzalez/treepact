"""State machines from STATE_AND_DATA_MODEL.md.

Every transition is validated against the documented diagram. Illegal
transitions raise StateConflict; no transition is ever performed with an ad
hoc string. Any non-terminal state may enter `failed` or
`infrastructure_error` when appropriate.
"""

from __future__ import annotations

from treepact.enums import (
    AttemptState,
    CheckState,
    LeaseState,
    RunState,
    ToolProposalState,
)
from treepact.errors import StateConflict

_ANY_FAILED = (RunState.FAILED, RunState.INFRASTRUCTURE_ERROR)

_RUN_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.PREPARING, RunState.REJECTED} | set(_ANY_FAILED),
    RunState.PREPARING: {RunState.READY, RunState.WAITING_RESOURCE, RunState.CANCELLED, RunState.REJECTED} | set(_ANY_FAILED),
    RunState.READY: {RunState.RUNNING, RunState.WAITING_RESOURCE, RunState.CANCELLED, RunState.REJECTED} | set(_ANY_FAILED),
    RunState.WAITING_RESOURCE: {RunState.READY, RunState.CANCELLED} | set(_ANY_FAILED),
    RunState.RUNNING: {RunState.VERIFYING, RunState.CANCELLED, RunState.INTERRUPTED} | set(_ANY_FAILED),
    RunState.INTERRUPTED: {RunState.RUNNING, RunState.CANCELLED} | set(_ANY_FAILED),
    RunState.VERIFYING: {RunState.ACCEPTED, RunState.REJECTED, RunState.NEEDS_REVIEW, RunState.CANCELLED} | set(_ANY_FAILED),
    RunState.ACCEPTED: set(),
    RunState.REJECTED: set(),
    RunState.NEEDS_REVIEW: set(),
    RunState.CANCELLED: set(),
    RunState.FAILED: set(),
    RunState.INFRASTRUCTURE_ERROR: set(),
}

_ATTEMPT_TRANSITIONS: dict[AttemptState, set[AttemptState]] = {
    AttemptState.CREATED: {AttemptState.PLANNING, AttemptState.CANCELLED, AttemptState.INTERRUPTED, AttemptState.FAILED},
    AttemptState.PLANNING: {AttemptState.ACTING, AttemptState.CANCELLED, AttemptState.INTERRUPTED, AttemptState.FAILED},
    AttemptState.ACTING: {AttemptState.CHECKING, AttemptState.CANCELLED, AttemptState.INTERRUPTED, AttemptState.FAILED},
    AttemptState.CHECKING: {AttemptState.PASSED, AttemptState.FAILED, AttemptState.EXHAUSTED, AttemptState.CANCELLED, AttemptState.INTERRUPTED},
    AttemptState.PASSED: set(),
    AttemptState.FAILED: set(),
    AttemptState.EXHAUSTED: set(),
    AttemptState.CANCELLED: set(),
    AttemptState.INTERRUPTED: set(),
}

_CHECK_TRANSITIONS: dict[CheckState, set[CheckState]] = {
    CheckState.DECLARED: {CheckState.QUEUED},
    CheckState.QUEUED: {CheckState.STARTED, CheckState.CANCELLED, CheckState.BLOCKED_INPUT_CHANGED},
    CheckState.STARTED: {CheckState.PASSED, CheckState.FAILED, CheckState.TIMED_OUT, CheckState.CANCELLED, CheckState.INFRASTRUCTURE_ERROR},
    CheckState.PASSED: set(),
    CheckState.FAILED: set(),
    CheckState.TIMED_OUT: set(),
    CheckState.CANCELLED: set(),
    CheckState.INFRASTRUCTURE_ERROR: set(),
    CheckState.BLOCKED_INPUT_CHANGED: set(),
}

_PROPOSAL_TRANSITIONS: dict[ToolProposalState, set[ToolProposalState]] = {
    ToolProposalState.PROPOSED: {ToolProposalState.ALLOWED, ToolProposalState.DENIED, ToolProposalState.STALE},
    ToolProposalState.ALLOWED: {ToolProposalState.STARTED, ToolProposalState.STALE},
    ToolProposalState.STARTED: {ToolProposalState.COMPLETED, ToolProposalState.FAILED, ToolProposalState.UNCERTAIN},
    ToolProposalState.COMPLETED: set(),
    ToolProposalState.FAILED: set(),
    ToolProposalState.UNCERTAIN: set(),
    ToolProposalState.DENIED: set(),
    ToolProposalState.STALE: set(),
}

_LEASE_TRANSITIONS: dict[LeaseState, set[LeaseState]] = {
    LeaseState.REQUESTED: {LeaseState.GRANTED, LeaseState.QUEUED, LeaseState.DENIED, LeaseState.REVOKED, LeaseState.EXPIRED},
    LeaseState.GRANTED: {LeaseState.ACTIVE, LeaseState.RELEASED, LeaseState.EXPIRED, LeaseState.REVOKED},
    LeaseState.ACTIVE: {LeaseState.RELEASED, LeaseState.EXPIRED, LeaseState.REVOKED},
    LeaseState.QUEUED: {LeaseState.GRANTED, LeaseState.DENIED, LeaseState.REVOKED},
    LeaseState.RELEASED: set(),
    LeaseState.EXPIRED: set(),
    LeaseState.DENIED: set(),
    LeaseState.REVOKED: set(),
}

_TABLES = {
    RunState: _RUN_TRANSITIONS,
    AttemptState: _ATTEMPT_TRANSITIONS,
    CheckState: _CHECK_TRANSITIONS,
    ToolProposalState: _PROPOSAL_TRANSITIONS,
    LeaseState: _LEASE_TRANSITIONS,
}


def allowed_transitions(state: object) -> set[object]:
    for enum_type, table in _TABLES.items():
        if isinstance(state, enum_type):
            return set(table[state])  # type: ignore[index]
    raise ValueError(f"unknown state type: {type(state).__name__}")


def validate_transition(from_state: object, to_state: object) -> None:
    for enum_type, table in _TABLES.items():
        if isinstance(from_state, enum_type):
            if not isinstance(to_state, enum_type):
                raise StateConflict(
                    f"cannot transition {from_state.value} to {to_state}",  # type: ignore[attr-defined]
                    code="transition_type_mismatch",
                )
            if to_state not in table[from_state]:  # type: ignore[index]
                raise StateConflict(
                    f"illegal transition {from_state.value} -> {to_state.value}",  # type: ignore[attr-defined]
                    code="illegal_transition",
                )
            return
    raise StateConflict(
        f"cannot transition from unknown state type {type(from_state).__name__}",
        code="transition_type_mismatch",
    )
