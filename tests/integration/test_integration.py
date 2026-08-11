"""Integration: SQLite migrations, real Git worktree lifecycle, patch apply,
named checks, bundle generation (PRD-002, WS-001..007, TOOL-003/006/007)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import PACT_MINIMAL, init_repo

from treepact.adapters.git import GitAdapter
from treepact.checks.executor import CheckExecutor
from treepact.config import Config, ProviderConfig, ResourceConfig, RetentionConfig
from treepact.domain.pact import compile_pact
from treepact.enums import CheckPhase, CheckState, RunMode, RunState, RuntimeId
from treepact.evidence.artifacts import ArtifactStore
from treepact.evidence.bundle import generate_bundle
from treepact.evidence.events import EventStore
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.repository import Repository
from treepact.workspace.env import TempHome
from treepact.workspace.supervisor import WorkspaceSupervisor


def _cfg(tmp_path: Path, endpoint: str | None = None) -> Config:
    return Config(
        data_dir=tmp_path / "data", config_path=None, log_level="info",
        provider=ProviderConfig(endpoint=endpoint), resources=ResourceConfig(),
        retention=RetentionConfig(), sources=("test",),
    )


def _compiled(writable: str = "src/") -> object:
    return compile_pact(
        PACT_MINIMAL.format(project_id="demo", writable=writable, check_id="unit",
                            argv='["python3", "-m", "pytest", "tests"]'),
        source_name="demo",
    )


class TestMigrations:
    def test_migrate_from_empty(self, tmp_path: Path) -> None:
        conn = open_connection(tmp_path / "db.sqlite")
        runner = MigrationRunner(conn)
        applied = runner.migrate()
        assert applied == [1]
        assert runner.head_version() == 1
        row = conn.execute("SELECT version, name, script_sha256 FROM schema_migrations").fetchone()
        assert row["version"] == 1
        assert len(row["script_sha256"]) == 64

    def test_idempotent_second_run(self, tmp_path: Path) -> None:
        conn = open_connection(tmp_path / "db.sqlite")
        runner = MigrationRunner(conn)
        runner.migrate()
        assert runner.migrate() == []

    def test_newer_schema_refused(self, tmp_path: Path) -> None:
        conn = open_connection(tmp_path / "db.sqlite")
        MigrationRunner(conn).migrate()
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at, script_sha256) "
            "VALUES (999, 'future', '2026-08-11T00:00:00Z', 'x' * 64)"
        )
        with pytest.raises(Exception) as info:  # noqa: B017
            MigrationRunner(conn).refuse_newer_schema()
        assert "newer" in str(info.value)

    def test_foreign_keys_enforced(self, tmp_path: Path) -> None:
        conn = open_connection(tmp_path / "db.sqlite")
        MigrationRunner(conn).migrate()
        with pytest.raises(Exception):  # noqa: B017 - any rejection acceptable
            conn.execute("INSERT INTO runs (run_id, task_id, pact_id, state) VALUES ('r', 't', 'p', 'created')")


class TestWorkspaceLifecycle:
    def _setup(self, tmp_path: Path, files: dict[str, str]) -> tuple:
        repo = tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "c@t"],
            ["git", "config", "user.name", "C"],
        ):
            subprocess.run(command, cwd=repo, check=True)
        base = init_repo(repo, files)
        git = GitAdapter()
        root, common = git.discover_root(repo)
        cfg = _cfg(tmp_path)
        conn = open_connection(cfg.data_dir / "db" / "treepact.sqlite")
        MigrationRunner(conn).migrate()
        repo_store = Repository(conn)
        store = EventStore(conn)
        compiled = _compiled()
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
        conn.execute("COMMIT")
        return git, cfg, conn, repo_store, store, compiled, run_id, base

    def test_worktree_created_at_base_and_isolated(self, tmp_path: Path) -> None:
        git, cfg, conn, repo_store, store, compiled, run_id, base = self._setup(
            tmp_path, {"src/app.py": "x = 1\n"}
        )
        supervisor = WorkspaceSupervisor(conn, cfg, git=git, store=store, repo=repo_store)
        worktree_id, path, digest = supervisor.create(run_id, base)
        assert path.is_dir()
        assert (path / "src" / "app.py").read_text() == "x = 1\n"
        assert not (path / ".git").is_dir()  # gitfile, not a directory copy
        workspace = repo_store.workspace_by_run(run_id)
        assert workspace["lock_owner"] == run_id
        assert workspace["base_commit"] == base
        assert len(digest) == 64

    def test_patch_apply_and_reconcile(self, tmp_path: Path) -> None:
        git, cfg, conn, repo_store, store, compiled, run_id, base = self._setup(
            tmp_path, {"src/app.py": "x = 1\n"}
        )
        supervisor = WorkspaceSupervisor(conn, cfg, git=git, store=store, repo=repo_store)
        _, path, _ = supervisor.create(run_id, base)
        patch = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        git.apply_check(path, patch)
        git.apply(path, patch)
        reconciled = supervisor.reconcile(run_id)
        assert reconciled["changed_paths"] == ["src/app.py"]
        assert "+x = 2" in reconciled["patch"]
        assert len(reconciled["tree_digest"]) == 64

    def test_reject_unresolved_git_operation(self, tmp_path: Path) -> None:
        git, cfg, conn, repo_store, store, compiled, run_id, base = self._setup(
            tmp_path, {"src/app.py": "x = 1\n"}
        )
        root = Path(repo_store.project_by_id("demo")["canonical_root"])
        common_dir = git._run(["rev-parse", "--git-common-dir"], cwd=root).stdout.strip()
        (Path(root) / common_dir / "MERGE_HEAD").write_text("deadbeef\n")
        with pytest.raises(Exception) as info:  # noqa: B017
            git.discover_root(root)
        assert "unresolved" in str(info.value)

    def test_cleanup_never_touches_main_repo(self, tmp_path: Path) -> None:
        git, cfg, conn, repo_store, store, compiled, run_id, base = self._setup(
            tmp_path, {"src/app.py": "x = 1\n"}
        )
        main_repo = Path(repo_store.project_by_id("demo")["canonical_root"])
        supervisor = WorkspaceSupervisor(conn, cfg, git=git, store=store, repo=repo_store)
        _, path, _ = supervisor.create(run_id, base)
        supervisor.remove(run_id)
        assert not path.exists()
        assert main_repo.is_dir()
        assert (main_repo / "src" / "app.py").exists()


class TestCheckExecution:
    def _setup_check(self, tmp_path: Path, script: str) -> tuple:
        repo = tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "c@t"],
            ["git", "config", "user.name", "C"],
        ):
            subprocess.run(command, cwd=repo, check=True)
        base = init_repo(repo, {"checks/verify.py": script})
        git = GitAdapter()
        root, common = git.discover_root(repo)
        cfg = _cfg(tmp_path)
        conn = open_connection(cfg.data_dir / "db" / "treepact.sqlite")
        MigrationRunner(conn).migrate()
        repo_store = Repository(conn)
        store = EventStore(conn)
        text = PACT_MINIMAL.format(project_id="demo", writable="checks/", check_id="unit",
                                   argv='["python3", "checks/verify.py"]')
        compiled = compile_pact(text, source_name="demo")
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
        conn.execute("COMMIT")
        supervisor = WorkspaceSupervisor(conn, cfg, git=git, store=store, repo=repo_store)
        _, worktree, _ = supervisor.create(run_id, base)
        temp_home = TempHome(cfg.data_dir / "tmp")
        executor = CheckExecutor(conn, compiled, worktree, cfg, temp_home, git)
        executor.capture_baseline(run_id)
        return conn, repo_store, store, executor, run_id, worktree, compiled

    def test_passing_check_records_artifacts(self, tmp_path: Path) -> None:
        conn, repo_store, store, executor, run_id, worktree, compiled = self._setup_check(
            tmp_path, 'print("ok")\n'
        )
        result = executor.run(run_id=run_id, attempt_id=None, check_id="unit",
                              phase=CheckPhase.FINAL, mode=RunMode.REPAIR)
        assert result["state"] == CheckState.PASSED.value
        assert result["exit_code"] == 0
        row = repo_store._conn.execute(
            "SELECT * FROM checks WHERE check_execution_id = ?", (result["result_artifact_id"],)
        ).fetchone() if False else repo_store._conn.execute(
            "SELECT stdout_artifact_id, stderr_artifact_id FROM checks WHERE check_id='unit' "
            "AND state='passed' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        artifacts = ArtifactStore(conn, _cfg(tmp_path).data_dir / "artifacts")
        stdout = artifacts.read_bytes(row["stdout_artifact_id"])
        assert b"ok" in stdout

    def test_failing_check_has_exit_code_and_output(self, tmp_path: Path) -> None:
        conn, repo_store, store, executor, run_id, worktree, compiled = self._setup_check(
            tmp_path, 'import sys\nprint("failure detail")\nsys.exit(3)\n'
        )
        result = executor.run(run_id=run_id, attempt_id=None, check_id="unit",
                              phase=CheckPhase.FINAL, mode=RunMode.REPAIR)
        assert result["state"] == CheckState.FAILED.value
        assert result["exit_code"] == 3
        row = repo_store._conn.execute(
            "SELECT stdout_artifact_id FROM checks WHERE state='failed' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        artifacts = ArtifactStore(conn, _cfg(tmp_path).data_dir / "artifacts")
        assert b"failure detail" in artifacts.read_bytes(row["stdout_artifact_id"])

    def test_modified_script_blocks_execution(self, tmp_path: Path) -> None:
        conn, repo_store, store, executor, run_id, worktree, compiled = self._setup_check(
            tmp_path, 'print("ok")\n'
        )
        (worktree / "checks" / "verify.py").write_text('print("tampered")\n')
        result = executor.run(run_id=run_id, attempt_id=None, check_id="unit",
                              phase=CheckPhase.FINAL, mode=RunMode.REPAIR)
        assert result["state"] == CheckState.BLOCKED_INPUT_CHANGED.value

    def test_undeclared_check_denied(self, tmp_path: Path) -> None:
        conn, repo_store, store, executor, run_id, worktree, compiled = self._setup_check(
            tmp_path, 'print("ok")\n'
        )
        with pytest.raises(Exception) as info:  # noqa: B017
            executor.run(run_id=run_id, attempt_id=None, check_id="not-declared",
                         phase=CheckPhase.FINAL, mode=RunMode.REPAIR)
        assert info.value.code == "check_undeclared"


class TestBundleGeneration:
    def test_bundle_manifest_and_markdown(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "c@t"],
            ["git", "config", "user.name", "C"],
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
        compiled = _compiled()
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
        repo_store.set_run_state(run_id, RunState.REJECTED, reason_code="test", decision="rejected")
        store.append(run_id=run_id, event_type="run.created", actor="operator",
                     correlation_id=run_id, pact_sha256=compiled.sha256,
                     payload={"task_id": task_id, "mode": "repair", "runtime_id": "native",
                              "model_profile": "fast-code", "attempt_limit": 3,
                              "turn_limit": 5, "deadline_at": "2026-08-11T00:00:00Z"})
        conn.execute("COMMIT")
        generate_bundle(conn, cfg, run_id)
        bundle_dir = cfg.data_dir / "runs" / run_id
        manifest = json.loads((bundle_dir / "report.json").read_text())
        assert manifest["run_id"] == run_id
        assert manifest["decision"] == "rejected"
        assert manifest["workspace_state"] == "not_created"
        assert manifest["final_tree_digest"] is None
        assert manifest["event_chain_head"]
        assert "TreePact run" in (bundle_dir / "report.md").read_text()
        ok, message = store.verify_chain(run_id)
        assert ok, message
