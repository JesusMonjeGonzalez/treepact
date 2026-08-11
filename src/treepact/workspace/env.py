"""Sanitized child environment (SECURITY_MODEL.md process controls).

The environment is built from an allowlist, never filtered after
inheritance: SSH_AUTH_SOCK is absent, token/key/password/cloud variables are
absent, and HOME points to a run-specific temporary directory. No
installation commands and no elevated privileges are ever used.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from treepact.errors import WorkspaceError

_ALLOWED_ENV_FIELDS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "USER",
    "LOGNAME",
    "SHELL",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "NO_COLOR",
    "CI",
)

_SECRET_NAME_PATTERNS = (
    ("SSH", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_PRIVATE_KEY"),
    ("TOKEN",),
    ("KEY",),
    ("PASSWORD", "PASS", "PASSWD"),
    ("SECRET",),
    ("CREDENTIAL",),
    ("AWS", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"),
    ("AZURE", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"),
    ("GITHUB", "GITHUB_TOKEN", "GH_TOKEN"),
    ("OPENAI", "OPENAI_API_KEY"),
    ("ANTHROPIC", "ANTHROPIC_API_KEY"),
    ("DATABASE_URL", "DB_PASSWORD"),
)


def is_secret_variable(name: str) -> bool:
    upper = name.upper()
    return any(
        upper == marker or upper.endswith(marker)
        for group in _SECRET_NAME_PATTERNS
        for marker in group
    )


def build_child_env(temp_home: Path) -> dict[str, str]:
    """Build an allowlisted environment for child processes. Values come from
    the current process only for the allowed fields; everything else is
    dropped. If HOME is inherited it is never forwarded."""
    inherited = os.environ
    env: dict[str, str] = {}
    if inherited.get("PATH"):
        env["PATH"] = inherited["PATH"]
    if inherited.get("LANG"):
        env["LANG"] = inherited["LANG"]
    if inherited.get("LC_ALL"):
        env["LC_ALL"] = inherited["LC_ALL"]
    if inherited.get("TERM"):
        env["TERM"] = inherited["TERM"]
    env["HOME"] = str(temp_home)
    env["TMPDIR"] = str(temp_home)
    env["XDG_CACHE_HOME"] = str(temp_home / ".cache")
    env["XDG_CONFIG_HOME"] = str(temp_home / ".config")
    env["PYTHONUTF8"] = "1"
    env["NO_COLOR"] = "1"
    return env


class TempHome:
    """Run-specific temporary HOME. Removed after the terminal run state."""

    def __init__(self, base: Path) -> None:
        self._base = base
        self._path: Path | None = None

    def path(self) -> Path:
        if self._path is None:
            try:
                self._base.mkdir(parents=True, exist_ok=True)
                self._path = Path(tempfile.mkdtemp(prefix="home-", dir=self._base))
            except OSError as exc:
                raise WorkspaceError(
                    f"cannot create temporary HOME: {exc}", code="temp_home_failed"
                ) from exc
        return self._path

    def cleanup(self) -> None:
        if self._path is not None and self._path.exists():
            shutil.rmtree(self._path, ignore_errors=True)
            self._path = None

    def __enter__(self) -> Path:
        return self.path()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.cleanup()
