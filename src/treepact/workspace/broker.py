"""Canonical path broker (SECURITY_MODEL.md filesystem controls).

Repository-relative paths only. Rejects absolute paths, empty ambiguity,
NUL, and parent traversal; resolves existing components and rejects symlink
escape; rejects `.git` and TreePact data irrespective of the Pact; detects
case-folding collisions on macOS and flags Unicode-confusable paths.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from treepact.errors import PolicyDenied

MAX_PATH_COMPONENTS = 128


class PathBroker:
    def __init__(self, worktree_root: Path, data_root: Path) -> None:
        self._root = worktree_root.resolve()
        self._data = data_root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, relative: str) -> Path:
        """Resolve a repository-relative path to a canonical absolute path
        inside the worktree, rejecting any escape. Never follows symlinks
        out of scope."""
        if relative == "/":
            return self._root
        cleaned = self._validate_relative(relative)
        candidate = self._root / cleaned
        return self._canonicalize(candidate)

    def relative(self, absolute: Path) -> str:
        resolved = absolute.resolve()
        try:
            rel = resolved.relative_to(self._root)
        except ValueError:
            raise PolicyDenied(
                f"path {absolute} resolves outside the worktree", code="path_outside_worktree"
            ) from None
        return rel.as_posix()

    def _validate_relative(self, relative: str) -> Path:
        if not relative:
            raise PolicyDenied("empty path", code="path_invalid")
        if "\x00" in relative:
            raise PolicyDenied("NUL byte in path", code="path_invalid")
        if relative.startswith("/"):
            raise PolicyDenied(f"absolute path {relative!r} is not accepted from a runtime", code="path_absolute")
        parts = Path(relative).parts
        if ".." in parts:
            raise PolicyDenied(f"parent traversal in path {relative!r}", code="path_traversal")
        if len(parts) > MAX_PATH_COMPONENTS:
            raise PolicyDenied("path has too many components", code="path_invalid")
        if parts and (parts[0] == ".git" or parts[0].startswith(".git/")):
            raise PolicyDenied(".git is inaccessible to runtime tools", code="path_git_denied")
        return Path(*parts)

    def _canonicalize(self, candidate: Path) -> Path:
        """Walk components resolving symlinks; every resolved component must
        stay inside the worktree. A component pointing into `.git` or the
        TreePact data root is denied."""
        current = self._root
        for component in candidate.parts[len(self._root.parts):]:
            next_path = current / component
            try:
                if next_path.is_symlink():
                    resolved = next_path.resolve()
                    if not self._is_inside_root(resolved):
                        raise PolicyDenied(
                            f"symlink {next_path} resolves outside the worktree",
                            code="path_symlink_escape",
                        )
                    if self._is_git_or_data(resolved):
                        raise PolicyDenied(
                            f"symlink {next_path} resolves into protected state",
                            code="path_symlink_protected",
                        )
                    current = resolved
                else:
                    current = next_path
            except (OSError, FileNotFoundError):
                current = next_path
        final = current
        if self._is_git_or_data(final):
            raise PolicyDenied(f"path {final} is protected state", code="path_git_denied")
        self._check_case_collision(final)
        return final

    def _is_inside_root(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._root)
            return True
        except ValueError:
            return False

    def _is_git_or_data(self, path: Path) -> bool:
        resolved = path.resolve()
        if ".git" in resolved.parts:
            return True
        try:
            resolved.relative_to(self._data)
        except ValueError:
            return False
        # Inside the TreePact data root: only the run's own worktree is
        # legitimate; db, artifacts, runs, logs, and locks stay protected.
        try:
            resolved.relative_to(self._root)
            return False
        except ValueError:
            return True

    def _check_case_collision(self, path: Path) -> None:
        """macOS case-folding: if the actual entry differs in case from the
        requested name, the path is ambiguous and denied."""
        if not path.exists():
            return
        parent = path.parent
        requested = path.name
        try:
            names = set(os.listdir(parent)) if parent.is_dir() else set()
        except OSError:
            return
        if requested in names:
            return
        for name in names:
            if name.lower() == requested.lower():
                raise PolicyDenied(
                    f"path {path} differs in case from existing entry {name!r}",
                    code="path_case_folding",
                )


def is_confusable(relative: str) -> bool:
    """Heuristic: a path with non-ASCII characters that normalize to ASCII
    lookalikes is visibly flagged in reports."""
    normalized = unicodedata.normalize("NFKC", relative)
    if normalized == relative:
        return False
    for ch in relative:
        if unicodedata.category(ch) == "Cf":
            return True
    return False
