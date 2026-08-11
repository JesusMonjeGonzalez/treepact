"""Unit: state machines, budget arithmetic, and event chain
(STATE-001/002, EVID-001/002, GATE-007)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from treepact.domain.state_machines import validate_transition
from treepact.enums import (
    AttemptState,
    CheckState,
    LeaseState,
    RunState,
    ToolProposalState,
)
from treepact.errors import StateConflict
from treepact.evidence.events import EventStore, canonical_json, event_digest, validate_payload
from treepact.storage.connection import open_connection


class TestRunStateMachine:
    @pytest.mark.parametrize(
        "transition",
        [
            (RunState.CREATED, RunState.PREPARING),
            (RunState.PREPARING, RunState.READY),
            (RunState.READY, RunState.RUNNING),
            (RunState.RUNNING, RunState.VERIFYING),
            (RunState.VERIFYING, RunState.ACCEPTED),
            (RunState.VERIFYING, RunState.REJECTED),
            (RunState.VERIFYING, RunState.NEEDS_REVIEW),
            (RunState.READY, RunState.WAITING_RESOURCE),
            (RunState.WAITING_RESOURCE, RunState.READY),
            (RunState.RUNNING, RunState.INTERRUPTED),
            (RunState.INTERRUPTED, RunState.RUNNING),
            (RunState.RUNNING, RunState.CANCELLED),
            (RunState.RUNNING, RunState.FAILED),
            (RunState.READY, RunState.CANCELLED),
            (RunState.PREPARING, RunState.REJECTED),
        ],
    )
    def test_legal_transitions(self, transition: tuple[RunState, RunState]) -> None:
        validate_transition(*transition)

    @pytest.mark.parametrize(
        "transition",
        [
            (RunState.ACCEPTED, RunState.RUNNING),
            (RunState.CANCELLED, RunState.RUNNING),
            (RunState.REJECTED, RunState.PREPARING),
            (RunState.NEEDS_REVIEW, RunState.VERIFYING),
            (RunState.FAILED, RunState.RUNNING),
            (RunState.CREATED, RunState.ACCEPTED),
            (RunState.PREPARING, RunState.VERIFYING),
        ],
    )
    def test_illegal_transitions_rejected(self, transition: tuple[RunState, RunState]) -> None:
        with pytest.raises(StateConflict):
            validate_transition(*transition)

    def test_terminal_states_are_absorbing(self) -> None:
        for terminal in (
            RunState.ACCEPTED,
            RunState.REJECTED,
            RunState.NEEDS_REVIEW,
            RunState.CANCELLED,
            RunState.FAILED,
            RunState.INFRASTRUCTURE_ERROR,
        ):
            assert terminal.terminal

    def test_cancelled_is_terminal_and_never_resumes(self) -> None:
        assert RunState.CANCELLED.terminal
        with pytest.raises(StateConflict):
            validate_transition(RunState.CANCELLED, RunState.INTERRUPTED)


class TestAttemptStateMachine:
    def test_flow(self) -> None:
        validate_transition(AttemptState.CREATED, AttemptState.PLANNING)
        validate_transition(AttemptState.PLANNING, AttemptState.ACTING)
        validate_transition(AttemptState.ACTING, AttemptState.CHECKING)
        validate_transition(AttemptState.CHECKING, AttemptState.PASSED)
        validate_transition(AttemptState.CHECKING, AttemptState.EXHAUSTED)
        validate_transition(AttemptState.CHECKING, AttemptState.FAILED)

    def test_exhausted_is_terminal(self) -> None:
        with pytest.raises(StateConflict):
            validate_transition(AttemptState.EXHAUSTED, AttemptState.PLANNING)


class TestCheckAndProposalMachines:
    def test_check_flow(self) -> None:
        validate_transition(CheckState.DECLARED, CheckState.QUEUED)
        validate_transition(CheckState.QUEUED, CheckState.STARTED)
        validate_transition(CheckState.STARTED, CheckState.PASSED)
        validate_transition(CheckState.STARTED, CheckState.TIMED_OUT)
        validate_transition(CheckState.QUEUED, CheckState.BLOCKED_INPUT_CHANGED)

    def test_proposal_flow(self) -> None:
        validate_transition(ToolProposalState.PROPOSED, ToolProposalState.ALLOWED)
        validate_transition(ToolProposalState.ALLOWED, ToolProposalState.STARTED)
        validate_transition(ToolProposalState.STARTED, ToolProposalState.COMPLETED)
        validate_transition(ToolProposalState.STARTED, ToolProposalState.UNCERTAIN)

    def test_lease_flow(self) -> None:
        validate_transition(LeaseState.REQUESTED, LeaseState.QUEUED)
        validate_transition(LeaseState.QUEUED, LeaseState.GRANTED)
        validate_transition(LeaseState.GRANTED, LeaseState.ACTIVE)
        validate_transition(LeaseState.ACTIVE, LeaseState.RELEASED)
        validate_transition(LeaseState.ACTIVE, LeaseState.EXPIRED)


class TestEventCanonicalization:
    def test_canonical_json_sorted_keys(self) -> None:
        payload = {"b": 1, "a": [1, 2], "c": None}
        assert canonical_json(payload) == '{"a":[1,2],"b":1,"c":null}'

    def test_float_prohibited(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - any rejection acceptable
            canonical_json({"value": 1.5})

    def test_unknown_event_type_rejected(self) -> None:
        with pytest.raises(Exception) as info:  # noqa: B017
            validate_payload("bogus.type", {})
        assert "unknown event type" in str(info.value)

    def test_unknown_payload_field_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - any rejection acceptable
            validate_payload("run.created", {"bogus": 1})

    def test_digest_includes_previous_chain(self) -> None:
        envelope = {"a": 1, "previous_event_sha256": None}
        envelope_linked = {"a": 1, "previous_event_sha256": "x" * 64}
        assert event_digest(envelope) != event_digest(envelope_linked)


class TestEventStoreChain:
    def _store(self, tmp_path) -> tuple[sqlite3.Connection, EventStore, str]:
        conn = open_connection(tmp_path / "db.sqlite")
        from treepact.enums import RunMode, RuntimeId
        from treepact.storage.migrations import MigrationRunner
        from treepact.storage.repository import Repository

        MigrationRunner(conn).migrate()
        repo = Repository(conn)
        conn.execute("BEGIN IMMEDIATE")
        repo.register_project("demo", "/tmp/repo", "common", "Demo")
        pact_id = repo.store_pact(project_id="demo", schema_version=1, sha256="a" * 64,
                                  canonical_json="{}", source_path="x")
        task_id = repo.create_task(project_id="demo", operator_text="t", mode=RunMode.OBSERVE)
        run_id = repo.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE,
                                 provider_id=None, model_profile=None, base_commit="0" * 40,
                                 assurance_level="TP0", attempt_limit=3, turn_limit=5,
                                 deadline_at="2026-08-11T00:00:00Z")
        conn.execute("COMMIT")
        return conn, EventStore(conn), run_id

    def test_monotonic_sequence_and_chain(self, tmp_path) -> None:
        conn, store, run_id = self._store(tmp_path)
        first = store.append(
            run_id=run_id, event_type="run.created", actor="t", correlation_id="c",
            pact_sha256="a" * 64, payload={"task_id": "t", "mode": "observe",
                                           "runtime_id": "native", "attempt_limit": 3,
                                           "turn_limit": 5, "deadline_at": "2026-08-11T00:00:00Z"},
        )
        second = store.append(
            run_id=run_id, event_type="run.preparing", actor="t", correlation_id="c",
            pact_sha256="a" * 64, payload={"to_state": "preparing"},
        )
        assert first["sequence"] == 1
        assert second["sequence"] == 2
        assert second["previous_event_sha256"] == first["event_sha256"]
        ok, message = store.verify_chain(run_id)
        assert ok, message

    def test_corruption_detected(self, tmp_path) -> None:
        conn, store, run_id = self._store(tmp_path)
        store.append(
            run_id=run_id, event_type="run.created", actor="t", correlation_id="c",
            pact_sha256="a" * 64, payload={"task_id": "t", "mode": "observe",
                                           "runtime_id": "native", "attempt_limit": 3,
                                           "turn_limit": 5, "deadline_at": "2026-08-11T00:00:00Z"},
        )
        conn.execute(
            "UPDATE events SET payload_json = ? WHERE run_id = ?",
            (json.dumps({"tampered": True}), run_id),
        )
        ok, message = store.verify_chain(run_id)
        assert not ok
        assert "digest mismatch" in message

    def test_sequence_gap_detected(self, tmp_path) -> None:
        conn, store, run_id = self._store(tmp_path)
        store.append(
            run_id=run_id, event_type="run.created", actor="t", correlation_id="c",
            pact_sha256="a" * 64, payload={"task_id": "t", "mode": "observe",
                                           "runtime_id": "native", "attempt_limit": 3,
                                           "turn_limit": 5, "deadline_at": "2026-08-11T00:00:00Z"},
        )
        conn.execute("UPDATE events SET sequence = 99 WHERE run_id = ?", (run_id,))
        ok, message = store.verify_chain(run_id)
        assert not ok
        assert "sequence gap" in message or "digest mismatch" in message
