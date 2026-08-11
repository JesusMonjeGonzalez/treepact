"""Git adapter: exact argv arrays, never a shell (TECHNICAL_ARCHITECTURE.md).

Allowed supervisor operations: discovery, resolve HEAD/root, worktree add,
list, lock, unlock, remove, status porcelain, diff, apply check/apply, and
object hashing. The runtime has no Git tool; no commit, branch, ref,
push, fetch, merge, or rewrite operation exists here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from treepact.errors import RepositoryError, WorkspaceError


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str


class GitAdapter:
    def __init__(self, git_bin: str = "git", env: dict[str, str] | None = None) -> None:
        self._git_bin = git_bin
        self._env = dict(env or os.environ)

    def _run(self, args: list[str], cwd: Path | None = None, timeout: int = 120) -> GitResult:
        """Fixed argv subprocess without a shell. Metacharacters in arguments
        are literal because no shell parses them."""
        if cwd is not None and not cwd.is_dir():
            raise RepositoryError(
                f"directory does not exist: {cwd}", code="not_a_repository"
            )
        if shutil.which(self._git_bin) is None:
            raise RepositoryError(
                f"git executable not found: {self._git_bin}", code="git_missing"
            )
        try:
            proc = subprocess.run(
                [self._git_bin, *args],
                cwd=str(cwd) if cwd else None,
                env=self._env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RepositoryError(
                f"git executable not found: {self._git_bin}", code="git_missing"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RepositoryError(
                f"git command timed out: {' '.join(args[:3])}...", code="git_timeout"
            ) from exc
        return GitResult(proc.returncode, proc.stdout, proc.stderr)

    def _must(self, result: GitResult, what: str) -> str:
        if result.returncode != 0:
            raise RepositoryError(
                f"git {what} failed: {result.stderr.strip() or result.stdout.strip()}",
                code="git_failed",
            )
        return result.stdout.strip()

    # ---- discovery ----------------------------------------------------------

    def discover_root(self, start: Path) -> tuple[Path, str]:
        """Return (canonical Git root, common-dir identity). Rejects
        unresolved Git operations such as merge or rebase."""
        result = self._run(["rev-parse", "--show-toplevel"], cwd=start)
        if result.returncode != 0:
            raise RepositoryError(
                f"{start} is not inside a Git repository", code="not_a_repository"
            )
        root = Path(result.stdout.strip())
        common_dir = self._must(
            self._run(["rev-parse", "--git-common-dir"], cwd=start), "common-dir resolution"
        )
        # `--git-common-dir` is relative to the repository root, never to the
        # process cwd; resolve it against the discovered root.
        common_path = Path(common_dir)
        if not common_path.is_absolute():
            common_path = root / common_path
        for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
            candidate = common_path / marker
            if candidate.exists():
                raise RepositoryError(
                    f"unresolved Git operation detected: {marker} exists",
                    code="unresolved_git_operation",
                )
        return root.resolve(), common_dir

    def head_commit(self, repo: Path) -> str:
        return self._must(self._run(["rev-parse", "HEAD"], cwd=repo), "HEAD resolution")

    def is_clean(self, repo: Path) -> tuple[bool, str]:
        """True when index and working tree match HEAD."""
        status = self._must(self._run(["status", "--porcelain"], cwd=repo), "status")
        return (not status), status

    # ---- worktree lifecycle --------------------------------------------------

    def worktree_add(self, repo: Path, target: Path, base_commit: str) -> None:
        self._must(
            self._run(
                ["worktree", "add", "--detach", str(target), base_commit], cwd=repo, timeout=300
            ),
            "worktree add",
        )

    def worktree_lock(self, target: Path, reason: str) -> None:
        self._must(self._run(["worktree", "lock", str(target), "--reason", reason], cwd=target), "worktree lock")

    def worktree_unlock(self, target: Path) -> None:
        result = self._run(["worktree", "unlock", str(target)], cwd=target)
        if result.returncode != 0:
            raise WorkspaceError(
                f"worktree unlock failed: {result.stderr.strip()}", code="worktree_unlock_failed"
            )

    def worktree_remove(self, target: Path, force: bool = True) -> None:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(target))
        self._must(self._run(args, cwd=target), "worktree remove")

    def worktree_exists(self, target: Path) -> bool:
        return (target / ".git").exists()

    # ---- state ---------------------------------------------------------------

    def status_porcelain(self, worktree: Path) -> str:
        return self._must(self._run(["status", "--porcelain"], cwd=worktree), "status")

    def diff_unified(self, worktree: Path, base_commit: str) -> str:
        """Final patch of the worktree against the base commit."""
        return self._run(
            ["diff", "--binary", base_commit, "--"], cwd=worktree
        ).stdout

    def diff_name_only(self, worktree: Path, base_commit: str) -> list[str]:
        stdout = self._must(
            self._run(["diff", "--name-only", base_commit, "--"], cwd=worktree), "diff names"
        )
        return [line for line in stdout.splitlines() if line.strip()]

    def diff_stat(self, worktree: Path, base_commit: str) -> str:
        return self._run(["diff", "--stat", base_commit, "--"], cwd=worktree).stdout

    def apply_check(self, worktree: Path, patch_text: str) -> None:
        patch_path = _write_patch(worktree, patch_text)
        result = self._run(["apply", "--check", "--whitespace=error-all", patch_path.name], cwd=worktree)
        if result.returncode != 0:
            raise WorkspaceError(
                f"git apply --check failed: {result.stderr.strip() or result.stdout.strip()}",
                code="patch_rejected",
            )

    def apply(self, worktree: Path, patch_text: str) -> None:
        patch_path = _write_patch(worktree, patch_text)
        result = self._run(["apply", "--whitespace=error-all", patch_path.name], cwd=worktree)
        if result.returncode != 0:
            raise WorkspaceError(
                f"git apply failed: {result.stderr.strip() or result.stdout.strip()}",
                code="patch_apply_failed",
            )

    # ---- digests -------------------------------------------------------------

    def tree_digest(self, worktree: Path) -> str:
        """Deterministic SHA-256 digest of the worktree tree state: sorted
        (path, mode, blob) entries from `git ls-files -s`. Git blob hashes
        are contextual, not the TreePact digest."""
        stdout = self._must(self._run(["ls-files", "-s"], cwd=worktree), "tree listing")
        entries: list[dict[str, str]] = []
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                mode, obj, _stage, path = parts[0], parts[1], parts[2], " ".join(parts[3:])
                entries.append({"path": path, "mode": mode, "blob": obj})
        entries.sort(key=lambda e: e["path"])
        return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()

    def hash_object(self, worktree: Path, relative: str) -> str:
        return self._must(
            self._run(["hash-object", relative], cwd=worktree), "hash-object"
        )


def _write_patch(worktree: Path, patch_text: str) -> Path:
    patch_path = worktree / ".treepact-patch"
    try:
        patch_path.write_text(patch_text, encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError(f"cannot stage patch: {exc}", code="patch_stage_failed") from exc
    return patch_path
