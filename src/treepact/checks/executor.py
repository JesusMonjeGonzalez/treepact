"""Named-check process runner (TECHNICAL_ARCHITECTURE.md check executor).

Only checks declared by ID in the Pact are executed, with exact argv from
the snapshot, a sanitized environment, a temporary HOME, a fixed cwd, and a
mandatory timeout. Script and build-configuration inputs are hashed when the
workspace is created; execution is blocked when those inputs were modified
(`blocked_input_changed`). TreePact records every result itself; a check
passes only when TreePact ran it and captured its exit status.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from treepact.adapters.git import GitAdapter
from treepact.config import Config
from treepact.domain.pact import BUILD_CONFIG_FILENAMES, CompiledPact
from treepact.enums import CheckPhase, CheckState, RunMode
from treepact.errors import PolicyDenied, WorkspaceError
from treepact.evidence.artifacts import ArtifactStore
from treepact.evidence.events import EventStore
from treepact.storage.repository import Repository
from treepact.workspace.broker import PathBroker
from treepact.workspace.env import TempHome, build_child_env
from treepact.workspace.process import CancellationToken, run_process


class InputSnapshot:
    """SHA-256 over sorted (path, content-hash) pairs of the files a check
    depends on: its executable (when repository-relative) and build/config
    files that exist in the worktree."""

    def __init__(self, root: Path, check_id: str, argv: list[str]) -> None:
        entries: list[dict[str, str]] = []
        candidates: list[str] = list(BUILD_CONFIG_FILENAMES)
        # Every argv token that looks like a repository-relative path is a
        # protected check input: executables and scripts (e.g. python3
        # scripts/check.py), never bare tool names.
        for token in argv:
            if "/" in token or token.startswith("."):
                candidates.append(token)
        for relative in sorted(set(candidates)):
            path = root / relative
            try:
                if path.is_file():
                    entries.append(
                        {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    )
            except OSError:
                continue
        self._entries = entries
        self._digest = hashlib.sha256(
            json.dumps(entries, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @property
    def digest(self) -> str:
        return self._digest

    def __eq__(self, other: object) -> bool:
        return isinstance(other, InputSnapshot) and other._entries == self._entries


class CheckExecutor:
    def __init__(
        self,
        conn: sqlite3.Connection,
        compiled: CompiledPact,
        workspace_root: Path,
        cfg: Config,
        temp_home: TempHome,
        git: GitAdapter,
    ) -> None:
        self._conn = conn
        self._compiled = compiled
        self._root = workspace_root
        self._cfg = cfg
        self._temp_home = temp_home
        self._git = git
        self._repo = Repository(conn)
        self._store = EventStore(conn)
        self._artifacts = ArtifactStore(conn, Path(str(cfg.data_dir)) / "artifacts")
        self._broker = PathBroker(workspace_root, Path(str(cfg.data_dir)))
        self._baseline: dict[str, str] = {}

    def capture_baseline(self, run_id: str) -> None:
        """Declare checks once when the workspace is created. Declared rows
        are declarations, not executions; they never satisfy gates."""
        for check_id, check in self._compiled.pact.checks.items():
            snapshot = InputSnapshot(self._root, check_id, check.argv)
            self._baseline[check_id] = snapshot.digest
            self._repo.create_check(
                run_id=run_id,
                attempt_id=None,
                check_id=check_id,
                phase=CheckPhase.FINAL,
                argv_sha256=hashlib.sha256(json.dumps(check.argv).encode()).hexdigest(),
                cwd_relative=check.cwd or ".",
                input_snapshot_sha256=snapshot.digest,
                state=CheckState.DECLARED,
            )

    def run(
        self,
        *,
        run_id: str,
        attempt_id: str | None,
        check_id: str,
        phase: CheckPhase,
        mode: RunMode,
        token: CancellationToken | None = None,
    ) -> dict[str, object]:
        check = self._compiled.check(check_id)
        if check is None:
            raise PolicyDenied(
                f"check {check_id!r} is not declared in the Pact", code="check_undeclared"
            )
        if phase not in check.phases:
            raise PolicyDenied(
                f"check {check_id!r} is not allowed in phase {phase.value}",
                code="check_phase_denied",
            )
        if mode not in check.modes:
            raise PolicyDenied(
                f"check {check_id!r} is not allowed in mode {mode.value}",
                code="check_mode_denied",
            )

        argv = list(check.argv)
        cwd_rel = check.cwd or "."
        cwd = self._broker.resolve(cwd_rel)
        if not cwd.is_dir():
            raise WorkspaceError(
                f"check working directory {cwd_rel!r} is not a directory",
                code="check_cwd_missing",
            )

        snapshot = InputSnapshot(self._root, check_id, check.argv)
        if self._baseline.get(check_id) and snapshot.digest != self._baseline[check_id]:
            return self._blocked(run_id, check_id, phase, "check_inputs_changed")

        declared = self._conn.execute(
            "SELECT check_execution_id FROM checks WHERE run_id = ? AND check_id = ? "
            "AND phase = ? AND state = ? AND attempt_id IS ?",
            (run_id, check_id, phase.value, CheckState.DECLARED.value, attempt_id),
        ).fetchone()
        if declared is not None:
            check_execution_id = declared["check_execution_id"]
            self._repo.set_check_state(check_execution_id, CheckState.QUEUED)
        else:
            check_execution_id = self._repo.create_check(
                run_id=run_id,
                attempt_id=attempt_id,
                check_id=check_id,
                phase=phase,
                argv_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                cwd_relative=cwd_rel,
                input_snapshot_sha256=snapshot.digest,
            )
        self._store.append(
            run_id=run_id,
            event_type="check.started",
            actor="treepact",
            correlation_id=check_execution_id,
            pact_sha256=self._compiled.sha256,
            payload={
                "check_execution_id": check_execution_id,
                "check_id": check_id,
                "phase": phase.value,
            },
        )

        env = build_child_env(self._temp_home.path())
        result = run_process(
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=check.timeout_seconds,
            token=token,
        )

        stdout_id = self._artifacts.store_bytes(
            run_id=run_id, kind=f"check:{check_id}:stdout", data=result.stdout,
            media_type="text/plain",
        )
        stderr_id = self._artifacts.store_bytes(
            run_id=run_id, kind=f"check:{check_id}:stderr", data=result.stderr,
            media_type="text/plain",
        )
        result_id = self._artifacts.store_bytes(
            run_id=run_id, kind=f"check:{check_id}:result",
            data=json.dumps({
                "argv": argv,
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "cancelled": result.cancelled,
                "duration_ms": result.duration_ms,
                "pid": result.pid,
                "truncated_stdout": result.truncated_stdout,
                "truncated_stderr": result.truncated_stderr,
            }, sort_keys=True).encode(),
            media_type="application/json",
        )

        if result.timed_out:
            state = CheckState.TIMED_OUT
            reason = "timed_out"
        elif result.cancelled:
            state = CheckState.CANCELLED
            reason = "cancelled"
        elif result.returncode == 0:
            state = CheckState.PASSED
            reason = "exit_zero"
        elif result.returncode is None:
            state = CheckState.INFRASTRUCTURE_ERROR
            reason = result.error_code or "process_failed"
        else:
            state = CheckState.FAILED
            reason = f"exit_{result.returncode}"

        event_type = {
            CheckState.PASSED: "check.passed",
            CheckState.FAILED: "check.failed",
            CheckState.TIMED_OUT: "check.timed_out",
            CheckState.CANCELLED: "check.cancelled",
            CheckState.INFRASTRUCTURE_ERROR: "check.infrastructure_error",
        }[state]

        self._repo.set_check_state(
            check_execution_id,
            state,
            exit_code=result.returncode,
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
            result_artifact_id=result_id,
            reason_code=reason,
        )
        self._store.append(
            run_id=run_id,
            event_type=event_type,
            actor="treepact",
            correlation_id=check_execution_id,
            pact_sha256=self._compiled.sha256,
            payload={
                "check_execution_id": check_execution_id,
                "check_id": check_id,
                "phase": phase.value,
                "exit_code": result.returncode,
            },
        )
        return {
            "check_id": check_id,
            "state": state.value,
            "exit_code": result.returncode,
            "stdout_artifact_id": stdout_id,
            "stderr_artifact_id": stderr_id,
            "result_artifact_id": result_id,
        }

    def _blocked(self, run_id: str, check_id: str, phase: CheckPhase, reason: str) -> dict[str, object]:
        check_execution_id = self._repo.create_check(
            run_id=run_id,
            attempt_id=None,
            check_id=check_id,
            phase=phase,
            argv_sha256=hashlib.sha256(b"").hexdigest(),
            cwd_relative=".",
            input_snapshot_sha256=self._baseline.get(check_id) or "",
        )
        self._repo.set_check_state(
            check_execution_id, CheckState.BLOCKED_INPUT_CHANGED, reason_code=reason
        )
        self._store.append(
            run_id=run_id,
            event_type="check.blocked",
            actor="treepact",
            correlation_id=check_execution_id,
            pact_sha256=self._compiled.sha256,
            payload={
                "check_execution_id": check_execution_id,
                "check_id": check_id,
                "phase": phase.value,
                "reason_code": reason,
            },
        )
        return {"check_id": check_id, "state": CheckState.BLOCKED_INPUT_CHANGED.value, "reason_code": reason}
