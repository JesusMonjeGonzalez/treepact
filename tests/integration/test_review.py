"""Read-only JSON review contract for Hearthia integrations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from typer.testing import CliRunner

from treepact.cli.app import app
from treepact.review import RunSummary, open_review_connection
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner

RUN_ID = "run_00000000000000000000000000000001"
SENSITIVE_CANARIES = {
    "TASK_TEXT_CANARY",
    "CANONICAL_ROOT_CANARY",
    "WORKTREE_PATH_CANARY",
    "ARTIFACT_PATH_CANARY",
    "ARTIFACT_CONTENT_CANARY",
    "PROMPT_CANARY",
    "PROVIDER_PAYLOAD_CANARY",
    "LOG_CANARY",
    "DIFF_CANARY",
}
FORBIDDEN_FIELDS = {
    "operator_text",
    "task",
    "canonical_root",
    "path",
    "relative_path",
    "content",
    "prompt",
    "payload",
    "payload_json",
    "logs",
    "diff",
}


def _seed_review_data(data_dir: Path, count: int = 25) -> Path:
    db_dir = data_dir / "db"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "treepact.sqlite"
    conn = open_connection(db_path)
    MigrationRunner(conn).migrate()
    conn.execute(
        "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
        (
            "project-one",
            "/CANONICAL_ROOT_CANARY",
            "git-id",
            "Project",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO pact_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "pact-one",
            "project-one",
            1,
            "a" * 64,
            '{"prompt":"PROMPT_CANARY"}',
            "PROMPT_CANARY",
            "2026-01-01T00:00:00Z",
        ),
    )
    for index in range(1, count + 1):
        run_id = f"run_{index:032x}"
        task_id = f"task-{index}"
        created_at = (
            "2026-08-30T12:00:00Z" if index >= count - 1 else f"2026-08-{index:02d}T12:00:00Z"
        )
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
            (task_id, "project-one", f"TASK_TEXT_CANARY {index}", "repair", created_at),
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                task_id,
                "pact-one",
                "accepted",
                "native",
                "provider",
                "PROVIDER_PAYLOAD_CANARY",
                "b" * 40,
                None,
                "TP3",
                3,
                10,
                "2026-08-31T12:00:00Z",
                created_at,
                created_at,
                None if index == 1 else "checks_passed",
                "accepted" if index != 1 else None,
            ),
        )
    conn.execute(
        "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "wt-one",
            RUN_ID,
            "/WORKTREE_PATH_CANARY",
            "b" * 40,
            "c" * 64,
            None,
            "owner",
            "active",
            "2026-08-01T00:00:00Z",
            None,
        ),
    )
    conn.execute(
        "INSERT INTO gates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "gate-result",
            RUN_ID,
            "required_checks_pass",
            "d" * 64,
            "passed",
            "checks_passed",
            '["artifact-one"]',
            "2026-08-30T12:01:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "artifact-one",
            RUN_ID,
            "check_result",
            "f" * 64,
            "ARTIFACT_PATH_CANARY",
            42,
            "application/json",
            "clean",
            "2026-08-30T12:01:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "event-one",
            RUN_ID,
            1,
            "runtime.finished",
            1,
            "2026-08-30T12:02:00Z",
            "runtime",
            RUN_ID,
            None,
            "a" * 64,
            '{"provider":"PROVIDER_PAYLOAD_CANARY","log":"LOG_CANARY","diff":"DIFF_CANARY"}',
            None,
            "e" * 64,
        ),
    )
    conn.close()
    bundle = data_dir / "runs" / RUN_ID
    bundle.mkdir(parents=True)
    (bundle / "report.json").write_text("ARTIFACT_CONTENT_CANARY", encoding="utf-8")
    return db_path


def _invoke(data_dir: Path, *args: str):
    return CliRunner().invoke(app, ["--data-dir", str(data_dir), "review", *args])


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


def test_review_list_order_default_limit_and_schema(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _seed_review_data(data_dir)
    result = _invoke(data_dir, "--schema-version", "1")
    assert result.exit_code == 0
    document = json.loads(result.stdout)
    assert result.stdout.count("\n") == 1
    assert document["schema"] == "treepact.review"
    assert document["schema_version"] == 1
    assert document["kind"] == "run_list"
    assert len(document["runs"]) == 20
    ids = [run["run_id"] for run in document["runs"]]
    assert ids[:2] == [f"run_{25:032x}", f"run_{24:032x}"]
    schema = json.loads((Path(__file__).parents[2] / "schemas" / "review.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)


def test_review_limit_and_detail_are_exact_minimized_contracts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _seed_review_data(data_dir)
    limited = _invoke(data_dir, "--schema-version", "1", "--limit", "1")
    assert limited.exit_code == 0
    assert len(json.loads(limited.stdout)["runs"]) == 1

    result = _invoke(data_dir, "--schema-version", "1", "--run-id", RUN_ID)
    assert result.exit_code == 0
    document = json.loads(result.stdout)
    assert document["kind"] == "run_detail"
    run = document["run"]
    assert run["decision"] is None
    assert run["reason_code"] is None
    assert run["gates"] == [
        {
            "gate_id": "required_checks_pass",
            "state": "passed",
            "reason_code": "checks_passed",
            "evidence_refs": ["artifact-one"],
            "calculated_at": "2026-08-30T12:01:00Z",
        }
    ]
    assert run["evidence"] == {
        "bundle_available": True,
        "event_chain_head": "e" * 64,
        "artifacts": [
            {
                "artifact_id": "artifact-one",
                "kind": "check_result",
                "sha256": "f" * 64,
                "size_bytes": 42,
                "media_type": "application/json",
            }
        ],
    }
    serialized = json.dumps(document)
    assert not (
        SENSITIVE_CANARIES & {canary for canary in SENSITIVE_CANARIES if canary in serialized}
    )
    assert not (_all_keys(document) & FORBIDDEN_FIELDS)
    schema = json.loads((Path(__file__).parents[2] / "schemas" / "review.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ((), "schema_version_required"),
        (("--schema-version", "2"), "schema_version_invalid"),
        (("--schema-version", "1", "--limit", "0"), "limit_invalid"),
        (("--schema-version", "1", "--limit", "101"), "limit_invalid"),
        (("--schema-version", "1", "--run-id", "run_BAD"), "run_id_invalid"),
        (("--schema-version", "1", "--run-id", RUN_ID, "--limit", "20"), "review_form_invalid"),
    ],
)
def test_review_invalid_input_exits_10(tmp_path: Path, args: tuple[str, ...], code: str) -> None:
    data_dir = tmp_path / "data"
    _seed_review_data(data_dir, count=1)
    result = _invoke(data_dir, *args)
    assert result.exit_code == 10
    assert result.stdout == ""
    assert f"[{code}]" in result.stderr


def test_review_missing_run_and_storage_are_redacted(tmp_path: Path) -> None:
    data_dir = tmp_path / "private-storage-canary"
    result = _invoke(data_dir, "--schema-version", "1")
    assert result.exit_code == 20
    assert result.stdout == ""
    assert str(data_dir) not in result.stderr
    assert not data_dir.exists()

    _seed_review_data(data_dir, count=1)
    missing = "run_ffffffffffffffffffffffffffffffff"
    result = _invoke(data_dir, "--schema-version", "1", "--run-id", missing)
    assert result.exit_code == 20
    assert result.stdout == ""
    assert "[run_not_found]" in result.stderr
    assert missing not in result.stderr


def test_review_rejects_incompatible_schema_without_exception_or_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "schema-path-canary"
    db_path = _seed_review_data(data_dir, count=1)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO schema_migrations VALUES (999, 'future', '2026-01-01T00:00:00Z', ?)",
        ("0" * 64,),
    )
    conn.commit()
    conn.close()
    result = _invoke(data_dir, "--schema-version", "1")
    assert result.exit_code == 20
    assert result.stdout == ""
    assert "[storage_incompatible]" in result.stderr
    assert str(data_dir) not in result.stderr
    assert "sqlite" not in result.stderr.lower()


def test_review_connection_is_query_only(tmp_path: Path) -> None:
    db_path = _seed_review_data(tmp_path / "data", count=1)
    conn = open_review_connection(db_path)
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 250
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE runs SET state = 'failed'")
    conn.close()


def test_review_does_not_mutate_database_events_gates_or_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _seed_review_data(data_dir)
    before_files = _snapshot(data_dir)
    conn = sqlite3.connect(db_path)
    before_counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM events), (SELECT COUNT(*) FROM gates)"
    ).fetchone()
    conn.close()

    assert _invoke(data_dir, "--schema-version", "1", "--limit", "10").exit_code == 0
    assert _invoke(data_dir, "--schema-version", "1", "--run-id", RUN_ID).exit_code == 0

    conn = sqlite3.connect(db_path)
    after_counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM events), (SELECT COUNT(*) FROM gates)"
    ).fetchone()
    conn.close()
    assert after_counts == before_counts
    assert _snapshot(data_dir) == before_files


def test_review_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RunSummary(
            run_id=RUN_ID,
            project_id="project-one",
            state="accepted",
            decision="accepted",
            reason_code=None,
            assurance_level="TP3",
            created_at="2026-08-30T00:00:00Z",
            updated_at="2026-08-30T00:00:00Z",
            operator_text="forbidden",
        )
