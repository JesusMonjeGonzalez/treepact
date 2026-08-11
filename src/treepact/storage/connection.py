"""SQLite connection policy for TreePact storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from treepact.errors import EvidenceError


def open_connection(db_path: Path) -> sqlite3.Connection:
    """Open the authoritative SQLite connection.

    Policy: WAL journal, foreign keys enforced, 5 s busy timeout, and
    synchronous FULL so acknowledged commits survive power loss.
    `isolation_level=None` keeps autocommit with explicit application
    transactions (one application command, one transaction).
    """
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        raise EvidenceError(
            f"cannot open TreePact database {db_path}: {exc}", code="storage_unavailable"
        ) from exc


class Transaction:
    """Context manager for one application-command transaction."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> Transaction:
        self._conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")
