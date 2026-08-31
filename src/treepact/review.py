"""Strict, read-only JSON projection for external review integrations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from treepact.clock import rfc3339
from treepact.config import Config, database_path
from treepact.errors import EvidenceError

REVIEW_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 250

_REQUIRED_COLUMNS = {
    "schema_migrations": {"version"},
    "projects": {"project_id"},
    "tasks": {"task_id", "project_id"},
    "runs": {
        "run_id",
        "task_id",
        "state",
        "decision",
        "terminal_reason_code",
        "assurance_level",
        "created_at",
        "updated_at",
    },
    "gates": {
        "run_id",
        "gate_id",
        "state",
        "reason_code",
        "evidence_refs_json",
        "calculated_at",
    },
    "artifacts": {"artifact_id", "run_id", "kind", "sha256", "size_bytes", "media_type"},
    "events": {"run_id", "sequence", "event_sha256"},
}


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunSummary(ReviewModel):
    run_id: str = Field(pattern=r"^run_[a-f0-9]{32}$")
    project_id: str = Field(min_length=1, max_length=128)
    state: str = Field(min_length=1, max_length=64)
    decision: str | None = Field(default=None, max_length=64)
    reason_code: str | None = Field(default=None, max_length=128)
    assurance_level: str = Field(min_length=1, max_length=16)
    created_at: datetime
    updated_at: datetime


class ReviewGate(ReviewModel):
    gate_id: str = Field(min_length=1, max_length=128)
    state: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=128)
    evidence_refs: list[str]
    calculated_at: datetime


class ReviewArtifact(ReviewModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=255)


class ReviewEvidence(ReviewModel):
    bundle_available: bool
    event_chain_head: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifacts: list[ReviewArtifact]


class RunDetail(RunSummary):
    gates: list[ReviewGate]
    evidence: ReviewEvidence


class RunListDocument(ReviewModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["treepact.review"] = Field(default="treepact.review", alias="schema")
    schema_version: Literal[1] = 1
    kind: Literal["run_list"] = "run_list"
    generated_at: datetime
    runs: list[RunSummary]


class RunDetailDocument(ReviewModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["treepact.review"] = Field(default="treepact.review", alias="schema")
    schema_version: Literal[1] = 1
    kind: Literal["run_detail"] = "run_detail"
    generated_at: datetime
    run: RunDetail


def open_review_connection(db_path: Path) -> sqlite3.Connection:
    """Open an existing database without permitting SQLite or TreePact writes."""
    if not db_path.is_file():
        raise EvidenceError("TreePact review storage is unavailable", code="storage_unavailable")
    try:
        resolved = db_path.resolve(strict=True)
        uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
            check_same_thread=False,
            timeout=_BUSY_TIMEOUT_MS / 1000,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            conn.close()
            raise EvidenceError(
                "TreePact review storage is not read-only", code="storage_incompatible"
            )
        _validate_schema(conn)
        return conn
    except EvidenceError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise EvidenceError(
            "TreePact review storage is unavailable or incompatible",
            code="storage_incompatible",
        ) from exc


def _validate_schema(conn: sqlite3.Connection) -> None:
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
        }
        if not set(_REQUIRED_COLUMNS).issubset(tables):
            raise EvidenceError(
                "TreePact review storage schema is incompatible", code="storage_incompatible"
            )
        versions = [
            int(row[0])
            for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        if versions != [1]:
            raise EvidenceError(
                "TreePact review storage schema is incompatible", code="storage_incompatible"
            )
        for table, required in _REQUIRED_COLUMNS.items():
            columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
            if not required.issubset(columns):
                raise EvidenceError(
                    "TreePact review storage schema is incompatible", code="storage_incompatible"
                )
    except EvidenceError:
        raise
    except (TypeError, ValueError, sqlite3.Error) as exc:
        raise EvidenceError(
            "TreePact review storage schema is incompatible", code="storage_incompatible"
        ) from exc


_RUN_COLUMNS = """
    r.run_id, t.project_id, r.state, r.decision,
    r.terminal_reason_code AS reason_code, r.assurance_level,
    r.created_at, r.updated_at
"""


def review_runs(cfg: Config, limit: int) -> RunListDocument:
    conn = open_review_connection(database_path(cfg))
    try:
        rows = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM runs r "
            "JOIN tasks t ON t.task_id = r.task_id "
            "ORDER BY r.created_at DESC, r.run_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return RunListDocument(
            generated_at=datetime.fromisoformat(rfc3339()),
            runs=[RunSummary(**dict(row)) for row in rows],
        )
    except (sqlite3.Error, ValidationError) as exc:
        raise EvidenceError(
            "TreePact review storage contains incompatible data", code="storage_incompatible"
        ) from exc
    finally:
        conn.close()


def review_run(cfg: Config, run_id: str) -> RunDetailDocument:
    conn = open_review_connection(database_path(cfg))
    try:
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM runs r "
            "JOIN tasks t ON t.task_id = r.task_id WHERE r.run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise EvidenceError("TreePact run was not found", code="run_not_found")

        gates = []
        for gate_row in conn.execute(
            "SELECT gate_id, state, reason_code, evidence_refs_json, calculated_at "
            "FROM gates WHERE run_id = ? ORDER BY calculated_at ASC, gate_id ASC",
            (run_id,),
        ):
            refs = json.loads(str(gate_row["evidence_refs_json"]))
            if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
                raise ValueError("invalid evidence references")
            gates.append(
                ReviewGate(
                    gate_id=gate_row["gate_id"],
                    state=gate_row["state"],
                    reason_code=gate_row["reason_code"],
                    evidence_refs=refs,
                    calculated_at=gate_row["calculated_at"],
                )
            )

        artifacts = [
            ReviewArtifact(**dict(artifact_row))
            for artifact_row in conn.execute(
                "SELECT artifact_id, kind, sha256, size_bytes, media_type "
                "FROM artifacts WHERE run_id = ? ORDER BY artifact_id ASC",
                (run_id,),
            )
        ]
        event_row = conn.execute(
            "SELECT event_sha256 FROM events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        detail = RunDetail(
            **dict(row),
            gates=gates,
            evidence=ReviewEvidence(
                bundle_available=(cfg.data_dir / "runs" / run_id / "report.json").is_file(),
                event_chain_head=str(event_row["event_sha256"]) if event_row else None,
                artifacts=artifacts,
            ),
        )
        return RunDetailDocument(generated_at=datetime.fromisoformat(rfc3339()), run=detail)
    except EvidenceError:
        raise
    except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError, ValidationError) as exc:
        raise EvidenceError(
            "TreePact review storage contains incompatible data", code="storage_incompatible"
        ) from exc
    finally:
        conn.close()
