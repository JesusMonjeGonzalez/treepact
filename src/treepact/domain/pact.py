"""Strict `.treepact.yaml` Pact compiler (ADRs 0005/0007, schemas/pact.schema.json).

The compiler converts a repository Pact into an immutable, deterministic run
policy: canonical serialization + SHA-256 hash, compiled read/write/deny path
rules, checks as fixed argv arrays, limits, gates, and semantic command
validation that JSON Schema cannot express completely.

Shell executables, shell `-c` forms, env-to-shell indirection, and
interpreter inline-evaluation flags are rejected. TreePact always uses
exec-style argv; metacharacters in ordinary arguments are literal because no
shell is ever invoked.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from treepact.enums import CheckPhase, GateId, NetworkMode, RunMode
from treepact.errors import PactValidationError

PACT_FILENAME = ".treepact.yaml"
MAX_CHECK_ID_LENGTH = 64
MAX_PATH_LENGTH = 512
MAX_ARGV_LENGTH = 2048

_SHELL_BASENAMES = {"sh", "bash", "zsh", "csh", "tcsh", "ksh", "fish", "dash", "ash", "pwsh"}

_INTERPRETER_INLINE_FLAGS: dict[str, set[str]] = {
    "python": {"-c"},
    "python3": {"-c"},
    "python3.12": {"-c"},
    "node": {"-e", "--eval"},
    "deno": {"eval", "-e", "--eval"},
    "bun": {"-e", "--eval"},
    "ruby": {"-e"},
    "perl": {"-e", "-pe", "-ne"},
    "php": {"-r"},
    "lua": {"-e"},
    "julia": {"-e"},
    "R": {"-e"},
    "Rscript": {"-e"},
    "powershell": {"-Command", "-c"},
}

_PATH_PROBLEM_PATTERNS = (
    (re.compile(r"^/"), "absolute path is not allowed"),
    (re.compile(r"(^|/)\.\.(/|$)"), "parent traversal is not allowed"),
    (re.compile(r"\x00"), "NUL byte is not allowed"),
    (re.compile(r"\\"), "backslash is not a path separator"),
)

_GLOB_META = re.compile(r"[*?\[{]")


class PathList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[str] = Field(min_length=0)

    @field_validator("items")
    @classmethod
    def _check_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_relative_path(value)
        return values


def _validate_relative_path(value: str) -> None:
    if not value:
        raise ValueError("empty path")
    if len(value) > MAX_PATH_LENGTH:
        raise ValueError(f"path longer than {MAX_PATH_LENGTH} characters")
    if value == "./":
        raise ValueError("ambiguous path './' is not allowed; use '.' for the root")
    for pattern, message in _PATH_PROBLEM_PATTERNS:
        if pattern.search(value):
            raise ValueError(message)


class PactProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    toolchain: str = Field(min_length=1, max_length=64)


class PactWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    readable: list[str] = Field(min_length=1)
    writable: list[str] = Field(min_length=0)
    denied: list[str] = Field(min_length=0)

    @field_validator("readable", "writable", "denied")
    @classmethod
    def _paths(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            _validate_relative_path(value)
            if value in seen:
                raise ValueError(f"duplicate path {value!r}")
            seen.add(value)
        return values


class PactCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(ge=1, le=7200)
    required: bool
    phases: list[CheckPhase] = Field(min_length=1)
    modes: list[RunMode] = Field(min_length=1)
    cwd: str | None = Field(default=None, max_length=MAX_PATH_LENGTH)

    @field_validator("argv")
    @classmethod
    def _argv(cls, values: list[str]) -> list[str]:
        for index, token in enumerate(values):
            if not token:
                raise ValueError(f"argv[{index}] is empty")
            if len(token) > MAX_ARGV_LENGTH:
                raise ValueError(f"argv[{index}] longer than {MAX_ARGV_LENGTH} characters")
        _reject_shell_or_inline_eval(values)
        return values

    @field_validator("cwd")
    @classmethod
    def _cwd(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_relative_path(value)
        return value


def _reject_shell_or_inline_eval(argv: list[str]) -> None:
    """Semantic command validation beyond JSON Schema: shell executables,
    shell `-c` forms, env-to-shell indirection, and interpreter
    inline-evaluation flags cannot be represented as check commands.

    Interpreter inline flags are rejected only in the flag region (before
    the first non-flag argument): `python3 -c "..."` is inline evaluation,
    while `python3 script.py -c x` passes a literal `-c` to the script.
    """
    program = Path(argv[0]).name
    if program in _SHELL_BASENAMES:
        raise ValueError(
            f"shell executable {program!r} is not allowed as a check command"
        )
    if program == "env":
        for arg in argv[1:]:
            if arg.startswith(("-", "=")):
                continue
            if Path(arg).name in _SHELL_BASENAMES or arg in {"-c"}:
                raise ValueError(
                    "env-to-shell indirection is not allowed in check commands"
                )
        return
    flags = _INTERPRETER_INLINE_FLAGS.get(program)
    if flags:
        for arg in argv[1:]:
            if arg.startswith("-"):
                if arg in flags:
                    raise ValueError(
                        f"inline evaluation flag {arg!r} is not allowed for {program!r}"
                    )
                continue
            break


class PactNetwork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime: NetworkMode = NetworkMode.DENIED
    checks: NetworkMode = NetworkMode.DENIED


class PactLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempts: int = Field(ge=1, le=3, description="maximum three attempts in schema version 1")
    turns_per_attempt: int = Field(ge=1, le=100)
    minutes: int = Field(ge=1, le=480)
    context_bytes: int = Field(ge=1024, le=10_000_000)
    max_input_tokens: int = Field(ge=256, le=1_000_000)
    max_output_tokens: int = Field(ge=128, le=100_000)
    model_profile: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    memory_mb: int = Field(ge=512, le=32768)
    network: PactNetwork


_REQUIRED_UNAVAILABLE_ACTIONS = (
    "commit",
    "push",
    "merge",
    "publish",
    "deploy",
    "release",
    "access_credentials",
    "modify_pact",
    "destructive_delete",
    "external_message",
)


class PactActions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unavailable: list[str] = Field(min_length=10)

    @field_validator("unavailable")
    @classmethod
    def _unavailable(cls, values: list[str]) -> list[str]:
        if set(values) != set(_REQUIRED_UNAVAILABLE_ACTIONS):
            missing = sorted(set(_REQUIRED_UNAVAILABLE_ACTIONS) - set(values))
            extra = sorted(set(values) - set(_REQUIRED_UNAVAILABLE_ACTIONS))
            detail = []
            if missing:
                detail.append(f"missing: {', '.join(missing)}")
            if extra:
                detail.append(f"unknown: {', '.join(extra)}")
            raise ValueError(
                "schema version 1 requires the full unavailable-action assertion; " + "; ".join(detail)
            )
        return values


class Pact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    project: PactProject
    workspace: PactWorkspace
    checks: dict[str, PactCheck] = Field(min_length=1)
    gates: list[GateId] = Field(min_length=1)
    limits: PactLimits
    actions: PactActions

    @field_validator("checks")
    @classmethod
    def _check_ids(cls, values: dict[str, PactCheck]) -> dict[str, PactCheck]:
        for key in values:
            if not re.match(r"^[a-z][a-z0-9_-]{0,63}$", key):
                raise ValueError(f"invalid check id {key!r}")
        return values

    @field_validator("gates")
    @classmethod
    def _gates(cls, values: list[GateId]) -> list[GateId]:
        if len(set(values)) != len(values):
            raise ValueError("gates must be unique")
        return values


_IMPLICIT_DENIED_PATHS = (".git", ".git/", ".treepact.yaml")


def canonical_json(data: dict[str, Any]) -> str:
    """Canonical Pact serialization: sorted keys, compact separators, UTF-8
    without ASCII escaping, no floats."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_yaml_safe(text: str, source_name: str) -> dict[str, Any]:
    """Safe YAML parsing: SafeLoader only, never arbitrary object loading."""
    try:
        loaded = yaml.load(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise PactValidationError(
            f"Pact file {source_name} is not valid YAML: {exc}", code="pact_yaml_invalid"
        ) from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise PactValidationError(
            f"Pact file {source_name} must contain a mapping at the top level",
            code="pact_structure_invalid",
        )
    return loaded


def _format_validation_error(source_name: str, exc: PydanticValidationError) -> PactValidationError:
    issues: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        issues.append(f"{location}: {error['msg']}")
    message = "\n".join(issues)
    return PactValidationError(
        f"Pact file {source_name} failed validation:\n{message}", code="pact_invalid"
    )


class CompiledPact:
    """Immutable compiled Pact used for the whole run."""

    def __init__(self, pact: Pact, canonical: str, sha256: str, source_path: str) -> None:

        self.pact = pact
        self.canonical_json = canonical
        self.sha256 = sha256
        self.source_path = source_path
        self._checks: dict[str, PactCheck] = pact.checks
        self._deny_rules: list[str] = list(pact.workspace.denied) + list(_IMPLICIT_DENIED_PATHS)
        self._writable_set = set(pact.workspace.writable)
        self._readable_set = set(pact.workspace.readable)
        self._deny_ruleset = _compile_ruleset(self._deny_rules)
        self._readable_ruleset = _compile_ruleset(pact.workspace.readable)
        self._writable_ruleset = _compile_ruleset(pact.workspace.writable)

    @property
    def policy_sha256(self) -> str:
        """Hash used by policy decisions and gates for this Pact snapshot."""
        return self.sha256

    def check(self, check_id: str) -> PactCheck | None:
        return self._checks.get(check_id)

    def check_ids(self) -> list[str]:
        return list(self._checks)

    def gate_ids(self) -> list[str]:
        return [gate.value for gate in self.pact.gates]

    def is_denied(self, relative: str) -> bool:
        return _path_matches(relative, self._deny_ruleset, self._deny_rules)

    def is_readable(self, relative: str) -> bool:
        if self.is_denied(relative):
            return False
        return _path_matches(relative, self._readable_ruleset, self.pact.workspace.readable)

    def is_writable(self, relative: str) -> bool:
        if self.is_denied(relative):
            return False
        return _path_matches(relative, self._writable_ruleset, self.pact.workspace.writable)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.canonical_json)

    def protected_input_paths(self) -> set[str]:
        """Repository-relative paths that checks depend on and that patches
        may never modify: check executables and scripts (every argv token
        that looks like a relative path) plus common build-configuration
        files."""
        protected: set[str] = set()
        for check in self.pact.checks.values():
            for token in check.argv:
                if "/" in token or token.startswith("."):
                    protected.add(token)
        protected.update(BUILD_CONFIG_FILENAMES)
        return protected


BUILD_CONFIG_FILENAMES = (
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "Package.swift",
    "Podfile",
    "Podfile.lock",
)


def _compile_ruleset(rules: list[str]) -> object:
    import pathspec

    prefixes: list[str] = []
    for rule in rules:
        stripped = rule.rstrip("/")
        if not stripped:
            continue
        if stripped == ".":
            # "." is the repository root and matches everything.
            prefixes.append("**")
            continue
        if _GLOB_META.search(rule):
            prefixes.append(rule)
        else:
            prefixes.append(stripped + "/")
            prefixes.append(stripped)
    return pathspec.PathSpec.from_lines("gitwildmatch", prefixes)


def _path_matches(relative: str, ruleset: object, rules: list[str]) -> bool:
    """Match against the precompiled rule set. Deny always overrides allow;
    TreePact canonicalizes paths before evaluation."""
    spec = ruleset  # type: ignore[attr-defined]
    if spec.match_file(relative):  # type: ignore[attr-defined]
        return True
    stripped = {rule.rstrip("/") for rule in rules if not _GLOB_META.search(rule)}
    return relative in stripped


def compile_pact(text: str, *, source_name: str) -> CompiledPact:
    """Load, validate, canonicalize, and hash a Pact. Raises
    PactValidationError with diagnostics; never executes project commands."""
    data = parse_yaml_safe(text, source_name)
    try:
        pact = Pact.model_validate(data)
    except PydanticValidationError as exc:
        raise _format_validation_error(source_name, exc) from exc
    canonical = canonical_json(pact.model_dump(mode="json"))
    sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return CompiledPact(pact, canonical, sha256, source_name)


def load_pact_file(path: Path) -> CompiledPact:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PactValidationError(
            f"cannot read Pact file {path}: {exc}", code="pact_unreadable"
        ) from exc
    return compile_pact(text, source_name=str(path))


def find_pact_file(start: Path) -> Path | None:
    """Walk upward from `start` to the filesystem root looking for a Pact
    file. The first directory containing one is the canonical project root
    in version 1 (before Git discovery is required)."""
    current = start.resolve()
    while True:
        candidate = current / PACT_FILENAME
        if candidate.is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent
