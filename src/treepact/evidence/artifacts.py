"""Content-addressed artifact store (STATE_AND_DATA_MODEL.md).

Files are stored once per SHA-256 digest under the data root; the `artifacts`
table maps (run_id, sha256, kind) to artifact rows. Large outputs never live
inside SQLite.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from treepact.clock import rfc3339
from treepact.errors import EvidenceError
from treepact.ids import new_id

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024  # bounded output and artifact size


class ArtifactStore:
    def __init__(self, conn: sqlite3.Connection, artifacts_root: Path) -> None:
        self._conn = conn
        self._root = artifacts_root

    def store_bytes(
        self,
        *,
        run_id: str,
        kind: str,
        data: bytes,
        media_type: str,
        redaction_state: str = "none",
    ) -> str:
        """Write content-addressed bytes and register the artifact row inside
        the caller's transaction. Returns the artifact ID."""
        if len(data) > MAX_ARTIFACT_BYTES:
            raise EvidenceError(
                f"artifact of {len(data)} bytes exceeds the {MAX_ARTIFACT_BYTES}-byte bound",
                code="artifact_too_large",
            )
        digest = hashlib.sha256(data).hexdigest()
        existing = self._conn.execute(
            "SELECT artifact_id FROM artifacts WHERE run_id = ? AND sha256 = ? AND kind = ?",
            (run_id, digest, kind),
        ).fetchone()
        if existing is not None:
            return existing["artifact_id"]
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / digest
        if not target.exists():
            tmp = self._root / f".tmp-{new_id('w')}"
            tmp.write_bytes(data)
            tmp.replace(target)
        artifact_id = new_id("art")
        self._conn.execute(
            "INSERT INTO artifacts (artifact_id, run_id, kind, sha256, relative_path, "
            "size_bytes, media_type, redaction_state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id,
                run_id,
                kind,
                digest,
                str(Path("artifacts") / digest),
                len(data),
                media_type,
                redaction_state,
                rfc3339(),
            ),
        )
        return artifact_id

    def read_bytes(self, artifact_id: str) -> bytes:
        row = self._conn.execute(
            "SELECT sha256, relative_path FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise EvidenceError(f"artifact {artifact_id} not found", code="artifact_missing")
        path = self._root / row["sha256"]
        if not path.exists():
            raise EvidenceError(
                f"artifact {artifact_id} content missing on disk", code="artifact_missing"
            )
        return path.read_bytes()

    def verify(self, artifact_id: str) -> bool:
        row = self._conn.execute(
            "SELECT sha256 FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            return False
        path = self._root / row["sha256"]
        if not path.exists():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]

    def rows_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at ASC", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
