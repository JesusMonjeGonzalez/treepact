"""Tool broker: the small typed capability surface exposed to runtimes
(TECHNICAL_ARCHITECTURE.md tool protocol).

Every proposal is validated, receives a deterministic policy decision
recorded as `policy.allowed`/`policy.denied`, and every execution is
recorded as `tool.started`/`tool.finished`/`tool.failed`/`tool.uncertain`.
The runtime never receives host-absolute paths, shell, or Git tools.
`apply_patch` validates paths, uses `git apply --check`, applies, and
reconciles. `run_check` accepts only a declared check ID.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from treepact.adapters.git import GitAdapter
from treepact.checks.executor import CheckExecutor
from treepact.config import Config
from treepact.domain.pact import CompiledPact
from treepact.enums import CheckPhase, RunMode, ToolProposalState
from treepact.errors import PolicyDenied
from treepact.evidence.events import EventStore
from treepact.storage.repository import Repository
from treepact.workspace.broker import PathBroker, is_confusable
from treepact.workspace.env import TempHome
from treepact.workspace.process import CancellationToken
from treepact.workspace.supervisor import WorkspaceSupervisor

TOOL_SCHEMA_VERSION = 1
MAX_LIST_ENTRIES = 1000
MAX_READ_BYTES = 8 * 1024 * 1024
DEFAULT_READ_BYTES = 1024 * 1024
MAX_SEARCH_MATCHES = 500
MAX_PATCH_BYTES = 2 * 1024 * 1024

SECRET_PATH_PATTERNS = (
    re.compile(r"(^|/)\.env(\.|$)"),
    re.compile(r"(^|/)\.ssh(/|$)"),
    re.compile(r"(^|/)id_(rsa|ed25519|ecdsa|dsa)($|\.)"),
    re.compile(r"(^|/)credentials?(/|$)"),
    re.compile(r"(^|/)secrets?(/|$)"),
    re.compile(r"(^|/)\.aws(/|$)"),
    re.compile(r"(^|/)\.kube(/|$)"),
    re.compile(r"(^|/)\.pypirc$"),
    re.compile(r"(^|/)\.npmrc$"),
    re.compile(r"\.(pem|key|p12|pfx|keystore|jks|asc|gpg)$"),
)

_BINARY_NUL_PROBE = b"\x00"


class ToolResult:
    """Structured observation returned to a runtime after a tool call."""

    def __init__(self, *, outcome: str, structured: dict[str, Any], message: str) -> None:
        self.outcome = outcome
        self.structured = structured
        self.message = message
        self.execution_id: str | None = None
        self.proposal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "proposal_id": self.proposal_id,
            "outcome": self.outcome,
            "structured_result": self.structured,
            "redacted_message": self.message,
        }


class ToolBroker:
    def __init__(
        self,
        conn: sqlite3.Connection,
        compiled: CompiledPact,
        workspace_root: Path,
        cfg: Config,
        *,
        run_id: str,
        attempt_id: str,
        turn: int,
        mode: RunMode,
        temp_home: TempHome,
        git: GitAdapter,
        data_dir: Path,
    ) -> None:
        self._conn = conn
        self._compiled = compiled
        self._root = workspace_root
        self._cfg = cfg
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._turn = turn
        self._mode = mode
        self._git = git
        self._data_dir = data_dir
        self._repo = Repository(conn)
        self._store = EventStore(conn)
        self._broker = PathBroker(workspace_root, data_dir)
        self._executor = CheckExecutor(conn, compiled, workspace_root, cfg, temp_home, git)
        self._supervisor = WorkspaceSupervisor(conn, cfg, git=git)
        self._cancelled = False
        self._finished = False
        self._active_check_token: CancellationToken | None = None

    @property
    def tool_names(self) -> list[str]:
        return ["list_files", "read_file", "search_text", "apply_patch", "run_check", "finish"]

    @property
    def cancelled_flag(self) -> bool:
        return self._cancelled

    @property
    def finished_flag(self) -> bool:
        return self._finished

    def cancel(self) -> None:
        self._cancelled = True
        if self._active_check_token is not None:
            self._active_check_token.cancel()

    # ---- dispatch -----------------------------------------------------------

    def propose(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Record a tool proposal and return its policy decision. Denied
        proposals never reach execution."""
        if self._cancelled or self._finished:
            raise PolicyDenied("run is finished or cancelled", code="run_finished")
        if tool_name not in self.tool_names:
            self._store.append(
                run_id=self._run_id,
                event_type="tool.proposed",
                actor="runtime",
                correlation_id=self._run_id,
                pact_sha256=self._compiled.sha256,
                payload={
                    "proposal_id": f"rejected-{tool_name}",
                    "attempt_id": self._attempt_id,
                    "turn": self._turn,
                    "tool_name": tool_name,
                    "schema_version": TOOL_SCHEMA_VERSION,
                    "arguments_sha256": _digest(arguments),
                },
            )
            decision_id = f"dec_rejected_{tool_name}"
            self._store.append(
                run_id=self._run_id,
                event_type="policy.denied",
                actor="treepact",
                correlation_id=decision_id,
                pact_sha256=self._compiled.sha256,
                payload={
                    "decision_id": decision_id,
                    "proposal_id": f"rejected-{tool_name}",
                    "rule_id": "tool_inventory",
                    "reason_code": "tool_unknown",
                },
            )
            raise PolicyDenied(
                f"tool {tool_name!r} is not in the TreePact tool inventory",
                code="tool_unknown",
            )

        args_digest = _digest(arguments)
        proposal_id = self._repo.create_proposal(
            run_id=self._run_id,
            attempt_id=self._attempt_id,
            turn=self._turn,
            tool_name=tool_name,
            schema_version=TOOL_SCHEMA_VERSION,
            arguments=_redact_arguments(tool_name, arguments),
            arguments_sha256=args_digest,
        )
        self._store.append(
            run_id=self._run_id,
            event_type="tool.proposed",
            actor="runtime",
            correlation_id=proposal_id,
            pact_sha256=self._compiled.sha256,
            payload={
                "proposal_id": proposal_id,
                "attempt_id": self._attempt_id,
                "turn": self._turn,
                "tool_name": tool_name,
                "schema_version": TOOL_SCHEMA_VERSION,
                "arguments_sha256": args_digest,
            },
        )

        decision = self._decide(tool_name, arguments)
        outcome = "allow" if decision["outcome"] == "allow" else "deny"
        self._repo.create_decision(
            proposal_id=proposal_id,
            policy_sha256=self._compiled.policy_sha256,
            outcome=outcome,
            rule_id=decision["rule_id"],
            reason_code=decision["reason_code"],
            details={},
        )
        self._repo.set_proposal_state(
            proposal_id,
            ToolProposalState.ALLOWED if outcome == "allow" else ToolProposalState.DENIED,
        )
        self._store.append(
            run_id=self._run_id,
            event_type="policy.allowed" if outcome == "allow" else "policy.denied",
            actor="treepact",
            correlation_id=decision["decision_id"],
            pact_sha256=self._compiled.sha256,
            payload={
                "decision_id": decision["decision_id"],
                "proposal_id": proposal_id,
                "rule_id": decision["rule_id"],
                "reason_code": decision["reason_code"],
            },
        )
        decision["proposal_id"] = proposal_id
        return decision

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute an allowed proposal and record the observation. Raises
        PolicyDenied before any side effect when policy fails."""
        decision = self.propose(tool_name, arguments)
        if decision["outcome"] != "allow":
            raise PolicyDenied(
                f"{tool_name} denied by policy: {decision['reason_code']}",
                code=decision["reason_code"],
            )
        proposal_id = decision["proposal_id"]
        execution_id = self._repo.create_execution(proposal_id)
        self._store.append(
            run_id=self._run_id,
            event_type="tool.started",
            actor="treepact",
            correlation_id=execution_id,
            pact_sha256=self._compiled.sha256,
            payload={"proposal_id": proposal_id, "execution_id": execution_id},
        )
        try:
            if tool_name == "list_files":
                result = self._list_files(arguments)
            elif tool_name == "read_file":
                result = self._read_file(arguments)
            elif tool_name == "search_text":
                result = self._search_text(arguments)
            elif tool_name == "apply_patch":
                result = self._apply_patch(arguments)
            elif tool_name == "run_check":
                result = self._run_check(arguments)
            elif tool_name == "finish":
                result = self._finish(arguments)
            else:  # pragma: no cover - propose() already restricts names
                raise PolicyDenied(f"unknown tool {tool_name!r}", code="tool_unknown")
        except PolicyDenied as exc:
            self._repo.finish_execution(
                execution_id, state="failed", error_code=exc.code,
                result={"denied": exc.message},
            )
            self._repo.set_proposal_state(proposal_id, ToolProposalState.FAILED)
            existing_decision = self._conn.execute(
                "SELECT decision_id FROM policy_decisions WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if existing_decision is None:
                self._repo.create_decision(
                    proposal_id=proposal_id,
                    policy_sha256=self._compiled.policy_sha256,
                    outcome="deny",
                    rule_id="execution_guard",
                    reason_code=exc.code,
                    details={},
                )
            self._store.append(
                run_id=self._run_id,
                event_type="policy.denied",
                actor="treepact",
                correlation_id=f"dec_{execution_id}",
                pact_sha256=self._compiled.sha256,
                payload={
                    "decision_id": f"dec_{execution_id}",
                    "proposal_id": proposal_id,
                    "rule_id": "execution_guard",
                    "reason_code": exc.code,
                },
            )
            self._store.append(
                run_id=self._run_id,
                event_type="tool.failed",
                actor="treepact",
                correlation_id=execution_id,
                pact_sha256=self._compiled.sha256,
                payload={
                    "proposal_id": proposal_id,
                    "execution_id": execution_id,
                    "error_code": exc.code,
                },
            )
            raise
        except Exception as exc:
            self._repo.finish_execution(
                execution_id, state="uncertain", error_code="unknown",
                result={"error": f"{type(exc).__name__}: {exc}"},
            )
            self._repo.set_proposal_state(proposal_id, ToolProposalState.UNCERTAIN)
            self._store.append(
                run_id=self._run_id,
                event_type="tool.uncertain",
                actor="treepact",
                correlation_id=execution_id,
                pact_sha256=self._compiled.sha256,
                payload={
                    "proposal_id": proposal_id,
                    "execution_id": execution_id,
                    "reason_code": "unknown_error",
                },
            )
            raise

        self._repo.finish_execution(
            execution_id, state="completed",
            result={"outcome": result.outcome, "structured": result.structured},
        )
        self._repo.set_proposal_state(proposal_id, ToolProposalState.COMPLETED)
        self._store.append(
            run_id=self._run_id,
            event_type="tool.finished",
            actor="treepact",
            correlation_id=execution_id,
            pact_sha256=self._compiled.sha256,
            payload={
                "proposal_id": proposal_id,
                "execution_id": execution_id,
                "outcome": result.outcome,
            },
        )
        result.execution_id = execution_id
        result.proposal_id = proposal_id
        return result.to_dict()

    # ---- policy --------------------------------------------------------------

    def _decide(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        decision_id = f"dec_{_digest({tool_name: arguments})[:32]}"
        if tool_name == "apply_patch" and self._mode != RunMode.REPAIR:
            return {
                "decision_id": decision_id,
                "outcome": "deny",
                "rule_id": "mode",
                "reason_code": "apply_patch_denied_in_observe",
            }
        if tool_name == "run_check":
            check_id = arguments.get("check_id")
            if not isinstance(check_id, str) or self._compiled.check(check_id) is None:
                return {
                    "decision_id": decision_id,
                    "outcome": "deny",
                    "rule_id": "check_declared",
                    "reason_code": "check_not_declared",
                }
        if tool_name == "finish":
            return {
                "decision_id": decision_id,
                "outcome": "allow",
                "rule_id": "finish",
                "reason_code": "ok",
            }
        if tool_name in ("list_files", "read_file", "search_text", "apply_patch"):
            path = arguments.get("path") or arguments.get("scope")
            if path is not None and isinstance(path, str):
                if path == "/":
                    path = ""
                if path:
                    parts = path.split("/")
                    if parts[0] == ".git" or any(part == ".git" for part in parts):
                        return {
                            "decision_id": decision_id,
                            "outcome": "deny",
                            "rule_id": "path_git",
                            "reason_code": "path_git_denied",
                        }
                    if self._is_secret_path(path):
                        return {
                            "decision_id": decision_id,
                            "outcome": "deny",
                            "rule_id": "path_secret",
                            "reason_code": "path_secret_denied",
                        }
                if path and not self._compiled.is_readable(path):
                    return {
                        "decision_id": decision_id,
                        "outcome": "deny",
                        "rule_id": "path_readable",
                        "reason_code": "path_not_readable",
                    }
                if tool_name == "apply_patch" and not self._compiled.is_writable(path):
                    return {
                        "decision_id": decision_id,
                        "outcome": "deny",
                        "rule_id": "path_writable",
                        "reason_code": "path_not_writable",
                    }
            if tool_name == "apply_patch":
                return {
                    "decision_id": decision_id,
                    "outcome": "allow",
                    "rule_id": "patch",
                    "reason_code": "ok",
                }
        return {
            "decision_id": decision_id,
            "outcome": "allow",
            "rule_id": "ok",
            "reason_code": "ok",
        }

    # ---- tool implementations -------------------------------------------------

    def _list_files(self, arguments: dict[str, Any]) -> ToolResult:
        scope = _require_str(arguments, "path", "/")
        glob = arguments.get("glob")
        limit = min(_require_int(arguments, "limit", MAX_LIST_ENTRIES), MAX_LIST_ENTRIES)
        base = self._broker.resolve(scope)
        if not base.is_dir():
            raise PolicyDenied(f"{scope!r} is not a directory", code="path_not_directory")
        entries: list[str] = []
        total = 0
        for path in sorted(base.rglob("*")):
            if total >= limit:
                break
            try:
                rel = self._broker.relative(path)
            except PolicyDenied:
                continue
            if rel.split("/")[0] == ".git":
                continue
            if not self._compiled.is_readable(rel):
                continue
            if glob and not fnmatch(path.name, glob):
                continue
            total += 1
            entries.append(rel)
        entries.sort()
        return ToolResult(
            outcome="completed",
            structured={"entries": entries, "count": len(entries), "truncated": total >= limit},
            message=f"listed {len(entries)} readable entries",
        )

    def _read_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = _require_str(arguments, "path")
        _validate_path_arg(path)
        if self._is_secret_path(path):
            raise PolicyDenied(
                f"reading {path!r} is denied: common secret path", code="path_secret_denied"
            )
        start = _require_int(arguments, "start", 0)
        target = self._broker.resolve(path)
        if not target.is_file():
            raise PolicyDenied(f"{path!r} is not a regular file", code="path_not_file")
        max_bytes = min(_require_int(arguments, "max_bytes", DEFAULT_READ_BYTES), MAX_READ_BYTES)
        with open(target, "rb") as fh:
            fh.seek(start)
            data = fh.read(max_bytes)
        if _BINARY_NUL_PROBE in data:
            raise PolicyDenied(
                "binary file reads are unsupported", code="binary_read_denied"
            )
        truncated = len(data) >= max_bytes
        confusable = is_confusable(path)
        return ToolResult(
            outcome="completed",
            structured={
                "path": path,
                "start": start,
                "bytes_read": len(data),
                "truncated": truncated,
                "confusable": confusable,
                "content": data.decode("utf-8", errors="replace"),
            },
            message=f"read {len(data)} bytes from {path}",
        )

    def _search_text(self, arguments: dict[str, Any]) -> ToolResult:
        expression = _require_str(arguments, "expression")
        scope = _require_str(arguments, "path", "/")
        max_matches = min(_require_int(arguments, "max_matches", MAX_SEARCH_MATCHES), MAX_SEARCH_MATCHES)
        base = self._broker.resolve(scope)
        if not base.is_dir():
            raise PolicyDenied(f"{scope!r} is not a directory", code="path_not_directory")
        try:
            pattern = re.compile(expression)
        except re.error as exc:
            raise PolicyDenied(f"invalid search expression: {exc}", code="search_expression_invalid") from exc
        matches: list[dict[str, Any]] = []
        scanned = 0
        for path in sorted(base.rglob("*")):
            if len(matches) >= max_matches:
                break
            try:
                rel = self._broker.relative(path)
            except PolicyDenied:
                continue
            if not path.is_file() or not self._compiled.is_readable(rel):
                continue
            if self._is_secret_path(rel):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if _BINARY_NUL_PROBE in data:
                continue
            scanned += 1
            text = data.decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append({"path": rel, "line": number, "text": line[:500]})
                    if len(matches) >= max_matches:
                        break
        return ToolResult(
            outcome="completed",
            structured={
                "matches": matches,
                "count": len(matches),
                "files_scanned": scanned,
                "truncated": len(matches) >= max_matches,
            },
            message=f"found {len(matches)} matches in {scanned} files",
        )

    def _apply_patch(self, arguments: dict[str, Any]) -> ToolResult:
        patch = _require_str(arguments, "patch")
        if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
            raise PolicyDenied("patch exceeds the size bound", code="patch_too_large")
        paths = _patch_paths(patch)
        for rel in paths:
            _validate_path_arg(rel)
            if not self._compiled.is_writable(rel):
                raise PolicyDenied(
                    f"patch touches {rel!r}, which is outside the writable scope",
                    code="patch_path_denied",
                )
            if self._is_secret_path(rel):
                raise PolicyDenied(
                    f"patch touches protected path {rel!r}", code="patch_path_secret"
                )
            if rel in self._compiled.protected_input_paths():
                raise PolicyDenied(
                    f"patch touches a protected check input {rel!r}",
                    code="patch_check_input_denied",
                )
        if _patch_has_mode_changes(patch):
            raise PolicyDenied("patch contains mode changes", code="patch_mode_change")
        if _patch_has_submodule_change(patch):
            raise PolicyDenied("patch contains submodule changes", code="patch_submodule")
        patch_digest_before = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        self._git.apply_check(self._root, patch)
        self._git.apply(self._root, patch)
        reconcile = self._supervisor.reconcile(self._run_id)
        changed = list(reconcile["changed_paths"])  # type: ignore[arg-type]
        for rel in changed:
            if not self._compiled.is_writable(rel):
                raise PolicyDenied(
                    f"patch resulted in changes to {rel!r} outside the writable scope",
                    code="patch_reconcile_denied",
                )
        return ToolResult(
            outcome="completed",
            structured={
                "applied_paths": paths,
                "patch_digest_before": patch_digest_before,
                "reconciled_paths": changed,
                "tree_digest": reconcile["tree_digest"],
            },
            message=f"applied patch touching {len(paths)} files",
        )

    def _run_check(self, arguments: dict[str, Any]) -> ToolResult:
        check_id = _require_str(arguments, "check_id")
        raw_phase = arguments.get("phase", "attempt")
        if raw_phase not in (CheckPhase.ATTEMPT.value, CheckPhase.FINAL.value):
            raise PolicyDenied(f"invalid check phase {raw_phase!r}", code="argument_invalid")
        phase = CheckPhase(raw_phase)
        token = CancellationToken()
        self._active_check_token = token
        try:
            result = self._executor.run(
                run_id=self._run_id,
                attempt_id=self._attempt_id,
                check_id=check_id,
                phase=phase,
                mode=self._mode,
                token=token,
            )
        finally:
            self._active_check_token = None
        return ToolResult(
            outcome="completed",
            structured=result,
            message=f"check {check_id} finished: {result['state']}",
        )

    def _finish(self, arguments: dict[str, Any]) -> ToolResult:
        summary = arguments.get("summary")
        claimed = arguments.get("claimed_status")
        self._finished = True
        return ToolResult(
            outcome="completed",
            structured={
                "summary": str(summary)[:2000] if summary else "",
                "claimed_status": claimed if isinstance(claimed, str) else "unknown",
                "note": "claimed status is context only; TreePact decides the run outcome",
            },
            message="finish recorded; TreePact calculates the final decision",
        )

    # ---- helpers --------------------------------------------------------------

    def _is_secret_path(self, relative: str) -> bool:
        return any(pattern.search(relative) for pattern in SECRET_PATH_PATTERNS)


def _validate_path_arg(path: str) -> None:
    if not path or "\x00" in path:
        raise PolicyDenied(f"invalid path {path!r}", code="path_invalid")
    if path.startswith("/"):
        raise PolicyDenied(f"absolute path {path!r} is not accepted from a runtime", code="path_absolute")
    if ".." in path.split("/"):
        raise PolicyDenied(f"parent traversal in path {path!r}", code="path_traversal")


def _require_str(arguments: dict[str, Any], key: str, default: str | None = None) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str) or not value:
        if default is None:
            raise PolicyDenied(f"missing string argument {key!r}", code="argument_missing")
        return default
    return value


def _require_int(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyDenied(f"invalid integer argument {key!r}", code="argument_invalid")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _redact_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Patch bodies and search expressions are stored redacted as digest-only
    metadata, never as full payloads."""
    if tool_name == "apply_patch" and "patch" in arguments:
        redacted = dict(arguments)
        redacted["patch"] = {"sha256": _digest(arguments["patch"]), "bytes": len(str(arguments["patch"]))}
        return redacted
    if tool_name == "search_text" and "expression" in arguments:
        redacted = dict(arguments)
        redacted["expression"] = {"sha256": _digest(arguments["expression"])}
        return redacted
    return arguments


_PATCH_PATH_RE = re.compile(r"^[+-]{3} (?:a/|b/)?(.*)$")


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        match = _PATCH_PATH_RE.match(line)
        if match:
            path = match.group(1).strip()
            if path not in ("/dev/null",):
                paths.append(path)
    return sorted(set(paths))


def _patch_has_mode_changes(patch: str) -> bool:
    return any(
        line.startswith("old mode") or line.startswith("new mode") or "file mode" in line
        for line in patch.splitlines()
    )


def _patch_has_submodule_change(patch: str) -> bool:
    return any("Subproject commit" in line for line in patch.splitlines())
