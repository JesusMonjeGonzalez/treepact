"""Recovery primitives (MASTER_PLAN M7).

- Stale-lock recovery: a run lock whose owner PID is dead is stale and can
  be claimed by the next startup.
- Orphan-process cleanup is limited to recorded child process groups from
  tool_executions that never reached a terminal state.
- Retention: temporary HOME dirs and stale locks older than the configured
  retention are swept on command startup; Decision Bundles, referenced
  artifacts, and event history are never purged in version 1.
- Recovery never assumes an uncertain side effect succeeded.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import time
from pathlib import Path

import psutil

from treepact.application.bootstrap import read_lock_owner
from treepact.config import Config

STALE_AFTER_SECONDS = 24 * 3600


def is_pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def recover_stale_locks(data_dir: Path) -> list[str]:
    """Remove lock files whose owner process is dead or older than the stale
    threshold. Returns the run IDs whose locks were removed."""
    removed: list[str] = []
    lock_dir = data_dir / "locks"
    if not lock_dir.is_dir():
        return removed
    for path in sorted(lock_dir.glob("*.lock")):
        run_id = path.name[:-5]
        owner = read_lock_owner(data_dir, run_id)
        if is_pid_alive(owner):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if time.time() - mtime > STALE_AFTER_SECONDS or not is_pid_alive(owner):
            try:
                path.unlink()
                removed.append(run_id)
            except OSError:
                continue
    for path in sorted(lock_dir.glob("*.cancel")):
        run_id = path.name[:-7]
        owner = read_lock_owner(data_dir, run_id)
        if not is_pid_alive(owner):
            try:
                path.unlink()
            except OSError:
                pass
    return removed


def terminate_recorded_groups(conn: sqlite3.Connection, run_id: str) -> list[int]:
    """Terminate child process groups recorded for a run that never reached a
    terminal execution state. Only recorded groups are touched; TreePact
    never searches broadly for similarly named processes."""
    terminated: list[int] = []
    rows = conn.execute(
        "SELECT process_group FROM tool_executions WHERE process_group IS NOT NULL "
        "AND state NOT IN ('completed', 'failed', 'uncertain')"
    ).fetchall()
    seen: set[int] = set()
    for row in rows:
        pgid = int(row["process_group"])
        if pgid in seen:
            continue
        seen.add(pgid)
        try:
            os.killpg(pgid, signal.SIGTERM)
            terminated.append(pgid)
        except (ProcessLookupError, PermissionError, OSError):
            continue
    return terminated


def sweep_retention(cfg: Config) -> int:
    """Remove transient temporary HOME directories older than the retention
    window. Operational state (bundles, events, artifacts) is preserved."""
    removed = 0
    tmp_root = cfg.data_dir / "tmp"
    if not tmp_root.is_dir():
        return 0
    cutoff = time.time() - cfg.retention.logs_days * 86400
    for entry in tmp_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                import shutil

                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def verify_resume_conditions(conn: sqlite3.Connection, cfg: Config, run_id: str) -> list[str]:
    """Return the list of conditions that block resume. Empty list means the
    run is resumable. Recovery never guesses: any uncertain effect blocks."""
    from treepact.storage.repository import Repository

    repo = Repository(conn)
    run = repo.run_by_id(run_id)
    if run is None:
        return ["run_not_found"]
    if run["state"] != "interrupted":
        return [f"state_{run['state']}"]

    problems: list[str] = []
    workspace = repo.workspace_by_run(run_id)
    if workspace is None:
        problems.append("workspace_missing")
        return problems
    worktree_path = Path(workspace["path"])
    if not worktree_path.is_dir():
        problems.append("worktree_missing")
    else:
        try:
            status = _git_status(worktree_path)
            if status is None:
                problems.append("worktree_not_git")
        except Exception:
            problems.append("worktree_unreadable")

    if is_pid_alive(read_lock_owner(cfg.data_dir, run_id)):
        problems.append("lock_owner_alive")

    uncertain = conn.execute(
        "SELECT execution_id FROM tool_executions WHERE proposal_id IN "
        "(SELECT proposal_id FROM tool_proposals WHERE run_id = ?) AND state = 'uncertain'",
        (run_id,),
    ).fetchall()
    if uncertain:
        problems.append("uncertain_effects_present")

    pact = repo.pact_by_id(run["pact_id"])
    if pact is None:
        problems.append("pact_snapshot_missing")
    return problems


def _git_status(worktree_path: Path) -> str | None:
    import subprocess

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(worktree_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout
