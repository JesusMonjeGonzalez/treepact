"""Unit: gate calculation, policy decisions, resource scheduling, command
idempotency (GATE-001..007, RES-001..005, STATE-007)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import PACT_MINIMAL

from treepact.config import Config, ProviderConfig, ResourceConfig, RetentionConfig
from treepact.domain.gates import GateEvaluator, decide
from treepact.domain.pact import compile_pact
from treepact.enums import CheckPhase, CheckState, GateId, GateState, WorkspaceState
from treepact.evidence.events import EventStore
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.repository import Repository


@pytest.fixture
def store(tmp_path: Path) -> tuple[sqlite3.Connection, Repository, EventStore, object]:
    from treepact.enums import RunMode, RuntimeId

    conn = open_connection(tmp_path / "db.sqlite")
    MigrationRunner(conn).migrate()
    repo = Repository(conn)
    store = EventStore(conn)
    text = PACT_MINIMAL.format(
        project_id="demo", writable="src/", check_id="unit",
        argv='["python3", "-m", "pytest", "tests"]',
    )
    compiled = compile_pact(text, source_name="demo")
    conn.execute("BEGIN IMMEDIATE")
    repo.register_project("demo", "/tmp/repo", "common", "Demo")
    pact_id = repo.store_pact(project_id="demo", schema_version=1, sha256=compiled.sha256,
                              canonical_json=compiled.canonical_json, source_path="x")
    task_id = repo.create_task(project_id="demo", operator_text="t", mode=RunMode.REPAIR)
    run_id = repo.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE,
                             provider_id=None, model_profile="fast-code", base_commit="0" * 40,
                             assurance_level="TP3", attempt_limit=3, turn_limit=5,
                             deadline_at="2026-08-11T00:00:00Z")
    conn.execute("COMMIT")
    cfg = Config(data_dir=tmp_path / "data", config_path=None, log_level="info",
                 provider=ProviderConfig(), resources=ResourceConfig(),
                 retention=RetentionConfig(), sources=("test",))
    return conn, repo, store, compiled, run_id, cfg


class _Git:
    def __init__(self, digest: str, changed: list[str]) -> None:
        self._digest = digest
        self._changed = changed

    def tree_digest(self, path: Path) -> str:
        return self._digest

    def diff_name_only(self, path: Path, base: str) -> list[str]:
        return self._changed


class TestGateEvaluation:
    def _gates(self, store, *, required_state: str = "passed", exit_code: int = 0,
               changed_paths: list[str] | None = None, digest: str | None = None,
               with_workspace: bool = True) -> list:
        conn, repo, event_store, compiled, run_id, cfg = store
        if with_workspace:
            repo.create_workspace(run_id=run_id, path="/tmp/wt", base_commit="0" * 40,
                                  initial_tree_digest="a" * 64, lock_owner=run_id)
            repo.set_workspace_state(run_id, WorkspaceState.RECONCILED, tree_digest=digest or ("b" * 64))
        repo.create_check(run_id=run_id, attempt_id=None, check_id="unit", phase=CheckPhase.FINAL,
                          argv_sha256="a" * 64, cwd_relative=".",
                          input_snapshot_sha256="c" * 64, state=CheckState(required_state))
        repo._conn.execute(
            "UPDATE checks SET exit_code = ?, finished_at = '2026-08-11T00:00:00Z' "
            "WHERE run_id = ?",
            (exit_code, run_id),
        )
        evaluator = GateEvaluator(conn, compiled, run_id, Path("/tmp/wt"), _Git(digest or ("b" * 64), changed_paths or ["src/a.py"]), cfg)
        return evaluator.evaluate_all(
            changed_paths=changed_paths or ["src/a.py"],
            patch="--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n",
            tree_digest=digest or ("b" * 64),
        )

    def test_all_passed_accepted(self, store) -> None:
        results = self._gates(store)
        decision, _ = decide(results)
        assert decision == "accepted"
        assert all(gate.state == GateState.PASSED for gate in results)

    def test_required_check_failed_rejected(self, store) -> None:
        results = self._gates(store, required_state="failed", exit_code=1)
        decision, reason = decide(results)
        assert decision == "rejected"
        assert reason == "required_check_failed"

    def test_blocked_input_insufficient(self, store) -> None:
        results = self._gates(store, required_state="blocked_input_changed")
        decision, reason = decide(results)
        assert decision == "needs_review"
        assert reason == "evidence_incomplete"
        gate = next(g for g in results if g.gate_id == GateId.REQUIRED_CHECKS_PASS)
        assert gate.state == GateState.INSUFFICIENT_EVIDENCE
        assert gate.reason_code == "check_inputs_changed"

    def test_declared_rows_are_not_evidence(self, store) -> None:
        conn, repo, event_store, compiled, run_id, cfg = store
        repo.create_check(run_id=run_id, attempt_id=None, check_id="unit", phase=CheckPhase.FINAL,
                          argv_sha256="a" * 64, cwd_relative=".", input_snapshot_sha256="c" * 64,
                          state=CheckState.DECLARED)
        evaluator = GateEvaluator(conn, compiled, run_id, Path("/tmp/wt"), _Git("b" * 64, []), cfg)
        results = evaluator.evaluate_all(changed_paths=[], patch="", tree_digest="b" * 64)
        gate = next(g for g in results if g.gate_id == GateId.REQUIRED_CHECKS_PASS)
        assert gate.state == GateState.INSUFFICIENT_EVIDENCE
        assert gate.reason_code == "no_required_check_executions"

    def test_denied_path_change_rejected(self, store) -> None:
        results = self._gates(store, changed_paths=[".env"])
        decision, reason = decide(results)
        assert decision == "rejected"
        assert reason == "denied_path_changed"

    def test_secret_in_diff_rejected(self, store) -> None:
        conn, repo, event_store, compiled, run_id, cfg = store
        repo.create_check(run_id=run_id, attempt_id=None, check_id="unit", phase=CheckPhase.FINAL,
                          argv_sha256="a" * 64, cwd_relative=".", input_snapshot_sha256="c" * 64,
                          state=CheckState.PASSED)
        repo.create_workspace(run_id=run_id, path="/tmp/wt", base_commit="0" * 40,
                              initial_tree_digest="a" * 64, lock_owner=run_id)
        repo.set_workspace_state(run_id, WorkspaceState.RECONCILED, tree_digest="b" * 64)
        evaluator = GateEvaluator(conn, compiled, run_id, Path("/tmp/wt"), _Git("b" * 64, ["src/a.py"]), cfg)
        results = evaluator.evaluate_all(
            changed_paths=["src/a.py"],
            patch="--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+password = \"hunter2\"\n",
            tree_digest="b" * 64,
        )
        gate = next(g for g in results if g.gate_id == GateId.NO_SECRETS_IN_DIFF)
        assert gate.state == GateState.FAILED

    def test_tree_digest_mismatch_rejected(self, store) -> None:
        results = self._gates(store, digest="b" * 64)  # git returns same digest
        assert all(g.state == GateState.PASSED for g in results)
        # now break the tree
        conn, repo, event_store, compiled, run_id, cfg = store
        evaluator = GateEvaluator(conn, compiled, run_id, Path("/tmp/wt"), _Git("zz" * 32, ["src/a.py"]), cfg)
        results = evaluator.evaluate_all(changed_paths=["src/a.py"], patch="x", tree_digest="b" * 64)
        gate = next(g for g in results if g.gate_id == GateId.WORKTREE_CONSISTENT)
        assert gate.state == GateState.FAILED
        assert gate.reason_code == "tree_digest_mismatch"

    def test_gate_results_persisted_with_events(self, store) -> None:
        conn, repo, event_store, compiled, run_id, cfg = store
        self._gates(store)
        gates = repo.gates_for_run(run_id)
        assert len(gates) == 5
        events = event_store.events_for_run(run_id)
        assert any(e["event_type"] == "gate.calculated" for e in events)


class TestCommandIdempotency:
    def _store(self, tmp_path: Path) -> tuple[sqlite3.Connection, Repository]:
        conn = open_connection(tmp_path / "db.sqlite")
        MigrationRunner(conn).migrate()
        return conn, Repository(conn)

    def test_same_key_same_request_replays_result(self, tmp_path: Path) -> None:
        from treepact.application.commands import begin_command, finish_command

        conn, _ = self._store(tmp_path)
        first = begin_command(conn, command_type="test", idempotency_key="k1", actor="t",
                              run_id=None, request={"a": 1})
        assert not first.replayed
        finish_command(conn, first.command_id, result={"ok": True})
        second = begin_command(conn, command_type="test", idempotency_key="k1", actor="t",
                               run_id=None, request={"a": 1})
        assert second.replayed
        assert second.check_result() == {"ok": True}

    def test_same_key_different_request_conflicts(self, tmp_path: Path) -> None:
        from treepact.application.commands import begin_command, finish_command
        from treepact.errors import StateConflict

        conn, _ = self._store(tmp_path)
        first = begin_command(conn, command_type="test", idempotency_key="k1", actor="t",
                              run_id=None, request={"a": 1})
        finish_command(conn, first.command_id, result={"ok": True})
        with pytest.raises(StateConflict):
            begin_command(conn, command_type="test", idempotency_key="k1", actor="t",
                          run_id=None, request={"a": 2})

    def test_failed_command_never_replays(self, tmp_path: Path) -> None:
        from treepact.application.commands import begin_command, finish_command
        from treepact.errors import StateConflict

        conn, _ = self._store(tmp_path)
        first = begin_command(conn, command_type="test", idempotency_key="k2", actor="t",
                              run_id=None, request={})
        finish_command(conn, first.command_id, error_code="boom")
        with pytest.raises(StateConflict):
            begin_command(conn, command_type="test", idempotency_key="k2", actor="t",
                          run_id=None, request={})

    def test_no_key_means_new_command(self, tmp_path: Path) -> None:
        from treepact.application.commands import begin_command

        conn, _ = self._store(tmp_path)
        first = begin_command(conn, command_type="test", idempotency_key=None, actor="t",
                              run_id=None, request={})
        second = begin_command(conn, command_type="test", idempotency_key=None, actor="t",
                               run_id=None, request={})
        assert not first.replayed
        assert not second.replayed
        assert first.command_id != second.command_id
