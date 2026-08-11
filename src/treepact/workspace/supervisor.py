"""Workspace supervisor (TECHNICAL_ARCHITECTURE.md Git model and worktree
lifecycle).

Lifecycle: discover and register root; reject unresolved Git operations;
capture base commit and initial status; create a detached worktree under
TreePact data storage; lock the worktree with the run ID; reconcile final
status and patch; keep the worktree after completion; remove only through
explicit cleanup after evidence is durable.

A worktree is change isolation, never a process sandbox. All TreePact
messaging distinguishes the two.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from treepact.adapters.git import GitAdapter
from treepact.config import Config
from treepact.enums import WorkspaceState
from treepact.errors import RepositoryError, WorkspaceError
from treepact.evidence.events import EventStore
from treepact.storage.repository import Repository

WORKTREE_DIR_NAME = "worktrees"


class WorkspaceSupervisor:
    def __init__(
        self,
        conn: object,
        cfg: Config,
        git: GitAdapter | None = None,
        store: EventStore | None = None,
        repo: Repository | None = None,
    ) -> None:
        self._cfg = cfg
        self._git = git or GitAdapter()
        self._store = store or EventStore(conn)  # type: ignore[arg-type]
        self._repo = repo or Repository(conn)  # type: ignore[arg-type]

    # ---- discovery ----------------------------------------------------------

    def discover(self, start: Path) -> tuple[Path, str, str]:
        """Return (canonical root, common-dir identity, base commit)."""
        root, common_dir = self._git.discover_root(start)
        base = self._git.head_commit(root)
        return root, common_dir, base

    def worktree_path_for(self, run_id: str) -> Path:
        return self._cfg.data_dir / WORKTREE_DIR_NAME / run_id

    # ---- lifecycle -----------------------------------------------------------

    def create(self, run_id: str, base_commit: str) -> tuple[str, Path, str]:
        """Create a detached worktree at base, lock it, and register it.
        Returns (worktree_id, path, initial_tree_digest)."""
        target = self.worktree_path_for(run_id)
        if target.exists():
            raise WorkspaceError(
                f"worktree path already exists: {target}", code="worktree_exists"
            )
        run = self._repo.run_by_id(run_id)
        if run is None:
            raise RepositoryError(f"run {run_id} not found", code="run_not_found")
        task = self._repo.task_by_id(str(run["task_id"]))
        if task is None:
            raise RepositoryError("task missing", code="task_missing")
        project = self._repo.project_by_id(str(task["project_id"]))
        if project is None:
            raise RepositoryError("run has no project", code="project_missing")
        root = Path(str(project["canonical_root"]))
        self._git.worktree_add(root, target, base_commit)
        try:
            self._git.worktree_lock(target, f"run {run_id}")
        except Exception:
            target_exists = self._git.worktree_exists(target)
            if target_exists:
                self._git.worktree_remove(target, force=True)
            raise
        digest = self._git.tree_digest(target)
        worktree_id = self._repo.create_workspace(
            run_id=run_id,
            path=str(target),
            base_commit=base_commit,
            initial_tree_digest=digest,
            lock_owner=run_id,
        )
        self._repo.set_run_worktree(run_id, worktree_id)
        self._store.append(
            run_id=run_id,
            event_type="workspace.created",
            actor="treepact",
            correlation_id=run_id,
            pact_sha256=self._pact_sha(run),
            payload={"worktree_id": worktree_id, "path": str(target), "base_commit": base_commit},
        )
        self._store.append(
            run_id=run_id,
            event_type="workspace.locked",
            actor="treepact",
            correlation_id=run_id,
            pact_sha256=self._pact_sha(run),
            payload={"worktree_id": worktree_id, "lock_owner": run_id},
        )
        return worktree_id, target, digest

    def reconcile(self, run_id: str) -> dict[str, Any]:
        """Compute the final patch and tree digest against the recorded base,
        independently of any runtime claims."""
        run = self._repo.run_by_id(run_id)
        if run is None:
            raise RepositoryError(f"run {run_id} not found", code="run_not_found")
        workspace = self._repo.workspace_by_run(run_id)
        if workspace is None:
            raise WorkspaceError("run has no workspace", code="workspace_missing")
        target = Path(workspace["path"])
        base = workspace["base_commit"]
        changed = self._git.diff_name_only(target, base)
        patch = self._git.diff_unified(target, base)
        digest = self._git.tree_digest(target)
        self._repo.set_workspace_state(run_id, WorkspaceState.RECONCILED, tree_digest=digest)
        self._store.append(
            run_id=run_id,
            event_type="workspace.reconciled",
            actor="treepact",
            correlation_id=run_id,
            pact_sha256=self._pact_sha(run),
            payload={
                "worktree_id": workspace["worktree_id"],
                "tree_digest": digest,
                "changed_paths": changed,
            },
        )
        return {
            "changed_paths": changed,
            "patch": patch,
            "tree_digest": digest,
        }

    def remove(self, run_id: str, *, force: bool = True) -> None:
        """Explicit cleanup: remove the worktree after verifying it lives
        under TreePact data storage. Never touches the main repository."""
        workspace = self._repo.workspace_by_run(run_id)
        if workspace is None:
            return
        target = Path(workspace["path"]).resolve()
        data_root = (self._cfg.data_dir / WORKTREE_DIR_NAME).resolve()
        try:
            target.relative_to(data_root)
        except ValueError:
            raise WorkspaceError(
                f"refusing to remove {target}: not under TreePact worktree storage",
                code="worktree_outside_storage",
            ) from None
        if not self._git.worktree_exists(target):
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            self._repo.set_workspace_state(run_id, WorkspaceState.REMOVED)
            return
        self._git.worktree_unlock(target)
        try:
            self._git.worktree_remove(target, force=force)
        except Exception:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
        self._repo.set_workspace_state(run_id, WorkspaceState.REMOVED)
        run = self._repo.run_by_id(run_id)
        self._store.append(
            run_id=run_id,
            event_type="workspace.removed",
            actor="treepact",
            correlation_id=run_id,
            pact_sha256=self._pact_sha(run) if run else "",
            payload={"worktree_id": workspace["worktree_id"]},
        )

    # ---- helpers -------------------------------------------------------------

    def _pact_sha(self, run: dict[str, object]) -> str:
        pact = self._repo.pact_by_id(str(run["pact_id"]))
        return pact["sha256"] if pact else ""


def project_identity(git: GitAdapter, start: Path) -> tuple[Path, str, str, bool]:
    """Read-only discovery used by `init` and `doctor`: root, common-dir
    identity, base commit, and clean status."""
    root, common_dir = git.discover_root(start)
    base = git.head_commit(root)
    clean, status = git.is_clean(root)
    return root, common_dir, base, clean
