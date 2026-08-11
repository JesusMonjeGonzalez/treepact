"""Startup recovery: interrupted-run discovery (STATE_AND_DATA_MODEL.md).

Version 1 runs are owned by one foreground TreePact process that writes a
lock file with its PID under the data root. On any startup the discovery
passes over non-terminal runs and marks them `interrupted` (emitting
`run.interrupted`) when no live owning process exists. A missing lock file
also counts as interrupted: without a live owner the run cannot continue.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import psutil

from treepact.enums import RunState
from treepact.evidence.events import EventStore
from treepact.storage.repository import Repository

LOCK_DIR_NAME = "locks"


def lock_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / LOCK_DIR_NAME / f"{run_id}.lock"


def write_run_lock(data_dir: Path, run_id: str) -> None:
    lock_dir = data_dir / LOCK_DIR_NAME
    lock_dir.mkdir(parents=True, exist_ok=True)
    tmp = lock_dir / f".tmp-{run_id}"
    tmp.write_text(json.dumps({"pid": os.getpid()}, sort_keys=True))
    tmp.replace(lock_path(data_dir, run_id))


def release_run_lock(data_dir: Path, run_id: str) -> None:
    try:
        lock_path(data_dir, run_id).unlink(missing_ok=True)
    except OSError:
        pass


def read_lock_owner(data_dir: Path, run_id: str) -> int | None:
    path = lock_path(data_dir, run_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload["pid"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def discover_interrupted_runs(conn: sqlite3.Connection, data_dir: Path) -> list[str]:
    """Mark runs that cannot own a live process as `interrupted`.

    Runs already in `interrupted`, `waiting_resource` (no process needed),
    or any terminal state are left untouched. Returns the runs newly
    marked interrupted.
    """
    repo = Repository(conn)
    store = EventStore(conn)
    discovered: list[str] = []
    for run in repo.active_runs():
        run_id = run["run_id"]
        state = RunState(run["state"])
        if state in (RunState.WAITING_RESOURCE, RunState.INTERRUPTED):
            continue
        owner = read_lock_owner(data_dir, run_id)
        if _pid_is_alive(owner):
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = repo.run_by_id(run_id)
            if current is None:
                conn.execute("ROLLBACK")
                continue
            if RunState(current["state"]) in (RunState.WAITING_RESOURCE, RunState.INTERRUPTED):
                conn.execute("ROLLBACK")
                continue
            if RunState(current["state"]).terminal:
                conn.execute("ROLLBACK")
                continue
            repo.set_run_state(run_id, RunState.INTERRUPTED, reason_code="owner_process_gone")
            store.append(
                run_id=run_id,
                event_type="run.interrupted",
                actor="treepact",
                correlation_id=f"discovery-{run_id}",
                pact_sha256=_pact_sha_or_empty(conn, current),
                payload={
                    "from_state": current["state"],
                    "to_state": RunState.INTERRUPTED.value,
                    "reason_code": "owner_process_gone",
                },
            )
            conn.execute("COMMIT")
            discovered.append(run_id)
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return discovered


def _pact_sha_or_empty(conn: sqlite3.Connection, run: dict[str, object]) -> str:
    if not run.get("pact_id"):
        return ""
    row = conn.execute(
        "SELECT sha256 FROM pact_versions WHERE pact_id = ?", (run["pact_id"],)
    ).fetchone()
    return row["sha256"] if row else ""
