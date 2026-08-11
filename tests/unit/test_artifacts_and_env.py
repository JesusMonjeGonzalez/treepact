"""Unit: artifact digesting, environment allowlist, secret redaction, and
provider error mapping (EVID-003, TOOL-004/005, SECRET controls)."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from treepact.evidence.artifacts import ArtifactStore
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.workspace.env import TempHome, build_child_env, is_secret_variable


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = open_connection(tmp_path / "db.sqlite")
    MigrationRunner(connection).migrate()
    from treepact.enums import RunMode, RuntimeId
    from treepact.storage.repository import Repository

    repo = Repository(connection)
    connection.execute("BEGIN IMMEDIATE")
    repo.register_project("demo", "/tmp/repo", "common", "Demo")
    pact_id = repo.store_pact(project_id="demo", schema_version=1, sha256="a" * 64,
                              canonical_json="{}", source_path="x")
    task_id = repo.create_task(project_id="demo", operator_text="t", mode=RunMode.OBSERVE)
    repo.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE, provider_id=None,
                    model_profile=None, base_commit="0" * 40, assurance_level="TP0",
                    attempt_limit=3, turn_limit=5, deadline_at="2026-08-11T00:00:00Z")
    connection.execute("COMMIT")
    return connection


class TestArtifacts:
    def test_content_addressed_dedup(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        store = ArtifactStore(conn, tmp_path / "artifacts")
        run_id = conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()["run_id"]
        conn.execute("BEGIN IMMEDIATE")
        first = store.store_bytes(run_id=run_id, kind="stdout", data=b"same bytes", media_type="text/plain")
        second = store.store_bytes(run_id=run_id, kind="stdout", data=b"same bytes", media_type="text/plain")
        conn.execute("COMMIT")
        assert first == second
        assert store.verify(first)

    def test_digest_matches_content(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        store = ArtifactStore(conn, tmp_path / "artifacts")
        run_id = conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()["run_id"]
        conn.execute("BEGIN IMMEDIATE")
        artifact_id = store.store_bytes(run_id=run_id, kind="log", data=b"hello", media_type="text/plain")
        conn.execute("COMMIT")
        row = conn.execute("SELECT sha256 FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        assert row["sha256"] == hashlib.sha256(b"hello").hexdigest()

    def test_oversized_artifact_rejected(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        store = ArtifactStore(conn, tmp_path / "artifacts")
        run_id = conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()["run_id"]
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(Exception):  # noqa: B017 - any rejection acceptable
            store.store_bytes(
                run_id=run_id, kind="log", data=b"x" * (64 * 1024 * 1024 + 1), media_type="text/plain"
            )

    def test_missing_file_detected(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        store = ArtifactStore(conn, tmp_path / "artifacts")
        run_id = conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()["run_id"]
        conn.execute("BEGIN IMMEDIATE")
        artifact_id = store.store_bytes(run_id=run_id, kind="log", data=b"x", media_type="text/plain")
        conn.execute("COMMIT")
        row = conn.execute("SELECT sha256 FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        (tmp_path / "artifacts" / row["sha256"]).unlink(missing_ok=True)
        assert not store.verify(artifact_id)


class TestEnvironmentAllowlist:
    def test_ssh_absent(self, tmp_path: Path) -> None:
        env = build_child_env(tmp_path)
        assert "SSH_AUTH_SOCK" not in env
        assert "SSH_AGENT_PID" not in env

    def test_secret_variables_absent(self, tmp_path: Path) -> None:
        os.environ["OPENAI_API_KEY"] = "fixture-value"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "fixture-value"
        os.environ["GITHUB_TOKEN"] = "fixture-value"
        os.environ["DATABASE_URL"] = "fixture-value"
        try:
            env = build_child_env(tmp_path)
        finally:
            del os.environ["OPENAI_API_KEY"]
            del os.environ["AWS_SECRET_ACCESS_KEY"]
            del os.environ["GITHUB_TOKEN"]
            del os.environ["DATABASE_URL"]
        for name in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "DATABASE_URL"):
            assert name not in env

    def test_home_points_to_temp(self, tmp_path: Path) -> None:
        env = build_child_env(tmp_path)
        assert env["HOME"] == str(tmp_path)

    @pytest.mark.parametrize(
        "name",
        ["SSH_AUTH_SOCK", "AWS_ACCESS_KEY_ID", "MY_TOKEN", "DB_PASSWORD", "API_KEY", "LOCAL_MEMORY_TOKEN"],
    )
    def test_secret_detector(self, name: str) -> None:
        assert is_secret_variable(name)

    @pytest.mark.parametrize("name", ["PATH", "LANG", "TERM", "PYTHONUTF8", "HOME"])
    def test_benign_variables(self, name: str) -> None:
        assert not is_secret_variable(name)

    def test_temp_home_cleanup(self, tmp_path: Path) -> None:
        home = TempHome(tmp_path)
        path = home.path()
        assert path.exists()
        home.cleanup()
        assert not path.exists()


class TestEventPayloadRedaction:
    def test_patch_payload_stored_as_digest(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        from treepact.workspace.tools import _redact_arguments

        redacted = _redact_arguments("apply_patch", {"patch": "diff --git a/x b/x\n"})
        assert "patch" in redacted
        assert isinstance(redacted["patch"], dict)
        assert "sha256" in redacted["patch"]
        assert "diff --git" not in json_dumps(redacted)

    def test_search_expression_redacted(self) -> None:
        from treepact.workspace.tools import _redact_arguments

        redacted = _redact_arguments("search_text", {"expression": "secret-pattern"})
        assert isinstance(redacted["expression"], dict)
        assert "secret-pattern" not in json_dumps(redacted)


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value)
