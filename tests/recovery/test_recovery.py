"""Recovery tests (STATE-003..006, RES-005, MASTER_PLAN M7): crash after
effects, cancel, resume rejection, stale locks, corrupted storage."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import PACT_MINIMAL, init_repo

from treepact.adapters.git import GitAdapter
from treepact.application.bootstrap import discover_interrupted_runs, write_run_lock
from treepact.config import Config, ProviderConfig, ResourceConfig, RetentionConfig
from treepact.domain.pact import compile_pact
from treepact.enums import RunMode, RunState, RuntimeId
from treepact.evidence.artifacts import ArtifactStore
from treepact.evidence.events import EventStore
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.recovery import recover_stale_locks, verify_resume_conditions
from treepact.storage.repository import Repository
from treepact.workspace.supervisor import WorkspaceSupervisor


def _cfg(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data", config_path=None, log_level="info",
        provider=ProviderConfig(), resources=ResourceConfig(),
        retention=RetentionConfig(), sources=("test",),
    )


@pytest.fixture
def interrupted_run(tmp_path: Path) -> tuple:
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "r@t"],
        ["git", "config", "user.name", "R"],
    ):
        subprocess.run(command, cwd=repo, check=True)
    base = init_repo(repo, {"src/app.py": "x = 1\n"})
    git = GitAdapter()
    root, common = git.discover_root(repo)
    cfg = _cfg(tmp_path)
    conn = open_connection(cfg.data_dir / "db" / "treepact.sqlite")
    MigrationRunner(conn).migrate()
    repo_store = Repository(conn)
    store = EventStore(conn)
    compiled = compile_pact(
        PACT_MINIMAL.format(project_id="demo", writable="src/", check_id="unit",
                            argv='["python3", "-m", "pytest", "tests"]'),
        source_name="demo",
    )
    conn.execute("BEGIN IMMEDIATE")
    repo_store.register_project("demo", str(root), common, "Demo")
    pact_id = repo_store.store_pact(project_id="demo", schema_version=1,
                                    sha256=compiled.sha256, canonical_json=compiled.canonical_json,
                                    source_path="x")
    task_id = repo_store.create_task(project_id="demo", operator_text="t", mode=RunMode.REPAIR)
    run_id = repo_store.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE,
                                   provider_id=None, model_profile="fast-code",
                                   base_commit=base, assurance_level="TP3",
                                   attempt_limit=3, turn_limit=5,
                                   deadline_at="2026-08-11T00:00:00Z")
    repo_store.create_attempt(run_id, 1)
    repo_store.set_run_state(run_id, RunState.RUNNING)
    conn.execute("COMMIT")
    supervisor = WorkspaceSupervisor(conn, cfg, git=git, store=store, repo=repo_store)
    _, worktree, _ = supervisor.create(run_id, base)
    return cfg, conn, repo_store, store, compiled, run_id, worktree, git


class TestInterruptedDiscovery:
    def test_orphaned_run_marked_interrupted(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        discovered = discover_interrupted_runs(conn, cfg.data_dir)
        assert run_id in discovered
        assert repo_store.run_by_id(run_id)["state"] == "interrupted"
        events = store.events_for_run(run_id)
        assert any(e["event_type"] == "run.interrupted" for e in events)

    def test_live_owner_not_marked(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        write_run_lock(cfg.data_dir, run_id)
        discovered = discover_interrupted_runs(conn, cfg.data_dir)
        assert run_id not in discovered

    def test_stale_lock_recovered(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        write_run_lock(cfg.data_dir, run_id)
        lock = cfg.data_dir / "locks" / f"{run_id}.lock"
        lock.write_text(json.dumps({"pid": 99999999}))
        removed = recover_stale_locks(cfg.data_dir)
        assert run_id in removed
        assert not lock.exists()


class TestResumeConditions:
    def test_resume_rejected_when_uncertain_effect(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        repo_store.set_run_state(run_id, RunState.INTERRUPTED)
        proposal = repo_store.create_proposal(run_id=run_id, attempt_id=repo_store.attempts_for_run(run_id)[0]["attempt_id"],
                                              turn=1, tool_name="apply_patch", schema_version=1,
                                              arguments={"patch": {"sha256": "a" * 64}}, arguments_sha256="a" * 64)
        execution = repo_store.create_execution(proposal)
        repo_store.finish_execution(execution, state="uncertain")
        problems = verify_resume_conditions(conn, cfg, run_id)
        assert "uncertain_effects_present" in problems

    def test_resume_rejected_when_worktree_missing(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        repo_store.set_run_state(run_id, RunState.INTERRUPTED)
        import shutil

        shutil.rmtree(worktree)
        problems = verify_resume_conditions(conn, cfg, run_id)
        assert "worktree_missing" in problems

    def test_resume_conditions_ok(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        repo_store.set_run_state(run_id, RunState.INTERRUPTED)
        assert verify_resume_conditions(conn, cfg, run_id) == []

    def test_cancelled_run_never_resumes(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        repo_store.set_run_state(run_id, RunState.CANCELLED)
        problems = verify_resume_conditions(conn, cfg, run_id)
        assert any("state" in problem for problem in problems)


class TestCancelAndCleanup:
    def test_cancel_preserves_evidence(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        repo_store.set_run_state(run_id, RunState.CANCELLED, reason_code="operator")
        supervisor = WorkspaceSupervisor(conn, cfg, git=git, store=store, repo=repo_store)
        supervisor.remove(run_id)
        assert not worktree.exists()
        # Evidence survives cleanup
        assert store.head_digest(run_id)

    def test_cleanup_refuses_active_run_without_force(self, interrupted_run) -> None:
        """CLI-001/WS-007: cleanup refuses an unreviewed (active) run unless
        the operator explicitly forces it; the worktree survives."""
        from treepact.cli.commands import cleanup_command
        from treepact.errors import StateConflict

        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        with pytest.raises(StateConflict):
            cleanup_command(cfg, run_id, force=False)
        assert worktree.exists()
        cleanup_command(cfg, run_id, force=True)
        assert not worktree.exists()


class TestCorruptionRecovery:
    def test_corrupted_artifact_detected(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        artifacts = ArtifactStore(conn, cfg.data_dir / "artifacts")
        conn.execute("BEGIN IMMEDIATE")
        artifact_id = artifacts.store_bytes(run_id=run_id, kind="stdout", data=b"data",
                                            media_type="text/plain")
        conn.execute("COMMIT")
        row = conn.execute("SELECT sha256 FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        (cfg.data_dir / "artifacts" / row["sha256"]).write_bytes(b"tampered")
        assert not artifacts.verify(artifact_id)

    def test_corrupted_event_chain_detected(self, interrupted_run) -> None:
        cfg, conn, repo_store, store, compiled, run_id, worktree, git = interrupted_run
        conn.execute("UPDATE events SET payload_json = '{}' WHERE run_id = ?", (run_id,))
        ok, message = store.verify_chain(run_id)
        assert not ok
