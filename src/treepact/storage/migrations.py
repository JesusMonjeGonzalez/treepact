"""Forward-only numbered SQL migration runner.

Policy (STATE_AND_DATA_MODEL.md): migrations are numbered, forward-only,
transactional where SQLite allows it, and their script digest is recorded
in `schema_migrations`. A database with a newer schema is refused without
writing. There is no in-place downgrade.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from treepact.clock import rfc3339
from treepact.errors import EvidenceError

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    script_sha256 TEXT NOT NULL
)
"""


class MigrationRunner:
    def __init__(self, conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> None:
        self._conn = conn
        self._dir = migrations_dir

    def applied_versions(self) -> set[int]:
        self._ensure_ledger()
        return {row[0] for row in self._conn.execute("SELECT version FROM schema_migrations")}

    def _ensure_ledger(self) -> None:
        self._conn.execute(_LEDGER_DDL)

    def migrate(self) -> list[int]:
        """Apply all pending migrations in numeric order. Returns the list of
        versions applied by this call."""
        self._ensure_ledger()
        applied = self.applied_versions()
        applied_now: list[int] = []
        for version, name, script in self._pending_migrations(applied):
            digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                # executescript() commits implicitly, which would break the
                # migration transaction; split into individual statements
                # instead so a failure rolls back the whole migration.
                for statement in _split_statements(script):
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at, script_sha256) "
                    "VALUES (?, ?, ?, ?)",
                    (version, name, rfc3339(), digest),
                )
                self._conn.execute("COMMIT")
            except sqlite3.Error as exc:
                self._conn.execute("ROLLBACK")
                raise EvidenceError(
                    f"migration {version:03d} {name} failed: {exc}",
                    code="migration_failed",
                ) from exc
            applied_now.append(version)
        return applied_now

    def head_version(self) -> int:
        rows = self._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(rows[0]) if rows and rows[0] is not None else 0

    def refuse_newer_schema(self) -> None:
        """Refuse a database whose schema is newer than this build, without
        writing anything."""
        if not self._dir.is_dir():
            return
        known = self._known_versions()
        applied = self.applied_versions()
        if applied - known:
            extra = sorted(applied - known)
            raise EvidenceError(
                f"database schema is newer than this TreePact build (versions {extra}); "
                "refusing to operate",
                code="schema_too_new",
            )

    def _pending_migrations(self, applied: set[int]) -> list[tuple[int, str, str]]:
        all_migrations: list[tuple[int, str, str]] = []
        for path in sorted(self._dir.glob("[0-9][0-9][0-9]__*.sql")):
            version = int(path.name[:3])
            name = path.name[5:-3]
            all_migrations.append((version, name, path.read_text(encoding="utf-8")))
        pending = [(v, n, s) for v, n, s in all_migrations if v not in applied]
        return sorted(pending, key=lambda item: item[0])

    def _known_versions(self) -> set[int]:
        return {int(p.name[:3]) for p in self._dir.glob("[0-9][0-9][0-9]__*.sql")}


def _split_statements(script: str) -> list[str]:
    """Split a migration script into individual statements on statement
    terminators. Migrations are TreePact-owned, contain only plain DDL, and
    never embed semicolons inside strings, so a line-oriented split is safe
    and keeps each statement a separate sqlite3.execute() call."""
    statements: list[str] = []
    current: list[str] = []
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        current.append(raw_line)
        if line.endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        raise EvidenceError(
            "migration script contains a statement without a terminator",
            code="migration_malformed",
        )
    return statements
