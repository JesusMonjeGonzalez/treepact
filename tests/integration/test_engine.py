"""Integration: full engine loop with a controlled fake provider
(RT-001..007, TOOL-001, EVAL-008, PRD-004/005/006)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import PACT_MINIMAL, FakeModelServer, init_repo

from treepact.adapters.git import GitAdapter
from treepact.application.run_engine import RunEngine
from treepact.config import Config, ProviderConfig, ResourceConfig, RetentionConfig
from treepact.domain.pact import compile_pact
from treepact.enums import RunMode, RunState, RuntimeId
from treepact.evidence.events import EventStore
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.repository import Repository


def _cfg(tmp_path: Path, endpoint: str) -> Config:
    return Config(
        data_dir=tmp_path / "data", config_path=None, log_level="info",
        provider=ProviderConfig(endpoint=endpoint), resources=ResourceConfig(),
        retention=RetentionConfig(), sources=("test",),
    )


def _make_run(tmp_path: Path, repo: Path, fake: FakeModelServer, *,
              task: str = "explore", mode: RunMode = RunMode.OBSERVE,
              writable: str = "src/", argv: str = '["python3", "-m", "pytest", "tests"]',
              check_id: str = "unit", run_mode: RunMode = RunMode.OBSERVE) -> tuple:
    git = GitAdapter()
    root, common = git.discover_root(repo)
    base = git.head_commit(root)
    cfg = _cfg(tmp_path, endpoint=f"http://127.0.0.1:{fake.port}/v1")
    conn = open_connection(cfg.data_dir / "db" / "treepact.sqlite")
    MigrationRunner(conn).migrate()
    repo_store = Repository(conn)
    store = EventStore(conn)
    compiled = compile_pact(
        PACT_MINIMAL.format(project_id="demo", writable=writable, check_id=check_id, argv=argv),
        source_name="demo",
    )
    conn.execute("BEGIN IMMEDIATE")
    repo_store.register_project("demo", str(root), common, "Demo")
    pact_id = repo_store.store_pact(project_id="demo", schema_version=1,
                                    sha256=compiled.sha256, canonical_json=compiled.canonical_json,
                                    source_path="x")
    task_id = repo_store.create_task(project_id="demo", operator_text=task, mode=run_mode)
    run_id = repo_store.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE,
                                   provider_id="loopback", model_profile="fast-code",
                                   base_commit=base, assurance_level="TP3",
                                   attempt_limit=3, turn_limit=10,
                                   deadline_at="2026-08-11T00:00:00Z")
    conn.execute("COMMIT")
    from treepact.adapters.http_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(endpoint=f"http://127.0.0.1:{fake.port}/v1",
                                        profiles={"fast-code": "local-model"})
    engine = RunEngine(conn, cfg, provider=provider, git=git)
    return cfg, conn, repo_store, store, compiled, run_id, engine


class TestEngineFlow:
    def test_full_observe_flow_creates_bundle(self, tmp_path: Path, fake_model: FakeModelServer) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "e@t"],
            ["git", "config", "user.name", "E"],
        ):
            subprocess.run(command, cwd=repo, check=True)
        init_repo(repo, {"src/app.py": "x = 1\n", "README.md": "demo\n"})
        fake_model.scenario = {"kind": "narrative", "text": "The repository is minimal."}
        cfg, conn, repo_store, store, compiled, run_id, engine = _make_run(
            tmp_path, repo, fake_model, argv='["python3", "-m", "compileall", "-q", "src"]'
        )
        final = engine.execute(run_id, "explore the repo")
        assert final == RunState.ACCEPTED.value
        run = repo_store.run_by_id(run_id)
        assert run["decision"] == "accepted"
        assert run["terminal_reason_code"] == "all_gates_passed"
        bundle = json.loads((cfg.data_dir / "runs" / run_id / "report.json").read_text())
        assert bundle["decision"] == "accepted"
        assert bundle["event_chain_head"]
        assert bundle["pact_sha256"] == compiled.sha256
        ok, message = store.verify_chain(run_id)
        assert ok, message

    def test_attempt_retry_until_limit(self, tmp_path: Path, fake_model: FakeModelServer) -> None:
        """EVAL-008 oracle: exactly three attempts at maximum, prior failures
        retained, no fourth attempt."""
        repo = tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "e@t"],
            ["git", "config", "user.name", "E"],
        ):
            subprocess.run(command, cwd=repo, check=True)
        init_repo(repo, {"src/app.py": "x = 1\n"})
        fake_model.scenario = {"kind": "tool_call", "tool": "run_check",
                               "arguments": {"check_id": "unit"}}
        cfg2, conn2, repo_store2, store2, compiled2, run_id2, engine2 = _make_run(
            tmp_path, repo, fake_model,
            argv='["python3", "-m", "nonexistent_module_xyz"]',
            task="fix the failing check", mode=RunMode.REPAIR, run_mode=RunMode.REPAIR,
        )
        from treepact.enums import AttemptState

        final = engine2.execute(run_id2, "fix the failing check")
        attempts = repo_store2.attempts_for_run(run_id2)
        assert len(attempts) == 3
        assert attempts[0]["state"] == AttemptState.EXHAUSTED.value
        assert attempts[1]["state"] == AttemptState.EXHAUSTED.value
        assert attempts[2]["state"] == AttemptState.EXHAUSTED.value
        assert final in (RunState.REJECTED.value, RunState.NEEDS_REVIEW.value)
        assert final != RunState.ACCEPTED.value
        # no fourth attempt
        assert repo_store2._conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id = ?", (run_id2,)
        ).fetchone()[0] == 3

    def test_provider_unavailable_fails_explicitly(self, tmp_path: Path) -> None:
        """RT-003: no silent provider fallback; a dead provider fails the run
        explicitly and the run ends failed."""
        repo = tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "e@t"],
            ["git", "config", "user.name", "E"],
        ):
            subprocess.run(command, cwd=repo, check=True)
        init_repo(repo, {"src/app.py": "x = 1\n"})
        cfg = _cfg(tmp_path, endpoint="http://127.0.0.1:1/v1")
        git = GitAdapter()
        root, common = git.discover_root(repo)
        conn = open_connection(cfg.data_dir / "db" / "treepact.sqlite")
        MigrationRunner(conn).migrate()
        repo_store = Repository(conn)
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
        task_id = repo_store.create_task(project_id="demo", operator_text="t", mode=RunMode.OBSERVE)
        run_id = repo_store.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE,
                                       provider_id="loopback", model_profile="fast-code",
                                       base_commit=git.head_commit(root), assurance_level="TP3",
                                       attempt_limit=3, turn_limit=5,
                                       deadline_at="2026-08-11T00:00:00Z")
        conn.execute("COMMIT")
        from treepact.adapters.http_provider import OpenAICompatibleProvider
        from treepact.errors import ProviderUnavailable

        provider = OpenAICompatibleProvider(endpoint="http://127.0.0.1:1/v1")
        engine = RunEngine(conn, cfg, provider=provider, git=git)
        with pytest.raises(ProviderUnavailable):
            engine.execute(run_id, "explore")
        assert repo_store.run_by_id(run_id)["state"] == RunState.FAILED.value
        assert repo_store.run_by_id(run_id)["terminal_reason_code"] == "provider_unavailable"

    def test_worktree_remains_after_terminal_run(self, tmp_path: Path, fake_model: FakeModelServer) -> None:
        """WS-006: the worktree survives a terminal run until cleanup."""
        repo = tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "e@t"],
            ["git", "config", "user.name", "E"],
        ):
            subprocess.run(command, cwd=repo, check=True)
        init_repo(repo, {"src/app.py": "x = 1\n"})
        fake_model.scenario = {"kind": "narrative", "text": "done"}
        cfg, conn, repo_store, store, compiled, run_id, engine = _make_run(tmp_path, repo, fake_model)
        engine.execute(run_id, "explore")
        worktree = cfg.data_dir / "worktrees" / run_id
        assert worktree.is_dir()
        assert (worktree / "src" / "app.py").exists()

    def test_model_cannot_set_final_decision(self, tmp_path: Path, fake_model: FakeModelServer) -> None:
        """RT-001: the model's claimed status is context; TreePact decides
        from captured facts. Here the model claims success while the
        required check genuinely fails: the run must be rejected."""
        repo = tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "e@t"],
            ["git", "config", "user.name", "E"],
        ):
            subprocess.run(command, cwd=repo, check=True)
        init_repo(repo, {"src/app.py": "x = 1\n"})
        fake_model.scenario = {"kind": "tool_call", "tool": "finish",
                               "arguments": {"summary": "all fixed", "claimed_status": "success"}}
        cfg, conn, repo_store, store, compiled, run_id, engine = _make_run(
            tmp_path, repo, fake_model,
            argv='["python3", "-m", "nonexistent_module_xyz"]',
            task="fix everything", mode=RunMode.REPAIR, run_mode=RunMode.REPAIR,
        )
        final = engine.execute(run_id, "fix everything")
        run = repo_store.run_by_id(run_id)
        # The check failed and no patch was applied; the model's success
        # claim cannot turn that into acceptance.
        assert final == RunState.REJECTED.value
        assert run["decision"] == "rejected"
        assert run["terminal_reason_code"] == "required_check_failed"
