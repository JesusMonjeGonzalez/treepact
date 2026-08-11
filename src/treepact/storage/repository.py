"""Typed data access for the authoritative SQLite schema.

One application command uses one transaction for its database effects; the
caller owns BEGIN/COMMIT (see storage.connection.Transaction) and these
methods never commit on their own.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from treepact.clock import rfc3339
from treepact.enums import (
    AttemptState,
    CheckPhase,
    CheckState,
    CommandState,
    LeaseState,
    RunMode,
    RunState,
    RuntimeId,
    ToolProposalState,
    WorkspaceState,
)
from treepact.ids import new_id


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- projects ---------------------------------------------------------

    def register_project(
        self, project_id: str, canonical_root: str, git_common_dir_identity: str, display_name: str
    ) -> None:
        now = rfc3339()
        self._conn.execute(
            "INSERT OR IGNORE INTO projects (project_id, canonical_root, git_common_dir_identity, "
            "display_name, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, canonical_root, git_common_dir_identity, display_name, now, now),
        )
        self._conn.execute(
            "UPDATE projects SET last_seen_at = ? WHERE project_id = ?", (now, project_id)
        )

    def project_by_root(self, canonical_root: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE canonical_root = ?", (canonical_root,)
        ).fetchone()
        return dict(row) if row else None

    def project_by_id(self, project_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_projects(self, active_only: bool = False) -> list[dict[str, Any]]:
        sql = (
            "SELECT p.* FROM projects p"
            + (" WHERE p.project_id IN (SELECT DISTINCT task.project_id FROM tasks task JOIN runs r ON r.task_id = task.task_id)" if active_only else "")
            + " ORDER BY p.last_seen_at DESC"
        )
        return [dict(r) for r in self._conn.execute(sql).fetchall()]

    # ---- pact versions -----------------------------------------------------

    def store_pact(
        self,
        *,
        project_id: str,
        schema_version: int,
        sha256: str,
        canonical_json: str,
        source_path: str,
    ) -> str:
        pact_id = new_id("pact")
        self._conn.execute(
            "INSERT OR IGNORE INTO pact_versions (pact_id, project_id, schema_version, sha256, "
            "canonical_json, source_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pact_id, project_id, schema_version, sha256, canonical_json, source_path, rfc3339()),
        )
        row = self._conn.execute(
            "SELECT pact_id FROM pact_versions WHERE project_id = ? AND sha256 = ?",
            (project_id, sha256),
        ).fetchone()
        return row["pact_id"]

    def pact_by_id(self, pact_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pact_versions WHERE pact_id = ?", (pact_id,)
        ).fetchone()
        return dict(row) if row else None

    # ---- tasks -------------------------------------------------------------

    def create_task(self, *, project_id: str, operator_text: str, mode: RunMode) -> str:
        task_id = new_id("task")
        self._conn.execute(
            "INSERT INTO tasks (task_id, project_id, operator_text, mode, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, project_id, operator_text, mode.value, rfc3339()),
        )
        return task_id

    def task_by_id(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    # ---- runs --------------------------------------------------------------

    def create_run(
        self,
        *,
        task_id: str,
        pact_id: str,
        runtime_id: RuntimeId,
        provider_id: str | None,
        model_profile: str | None,
        base_commit: str,
        assurance_level: str,
        attempt_limit: int,
        turn_limit: int,
        deadline_at: str,
    ) -> str:
        run_id = new_id("run")
        now = rfc3339()
        self._conn.execute(
            "INSERT INTO runs (run_id, task_id, pact_id, state, runtime_id, provider_id, "
            "model_profile, base_commit, worktree_id, assurance_level, attempt_limit, turn_limit, "
            "deadline_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                task_id,
                pact_id,
                RunState.CREATED.value,
                runtime_id.value,
                provider_id,
                model_profile,
                base_commit,
                assurance_level,
                attempt_limit,
                turn_limit,
                deadline_at,
                now,
                now,
            ),
        )
        return run_id

    def run_by_id(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def set_run_state(self, run_id: str, state: RunState, reason_code: str | None = None, decision: str | None = None) -> None:
        self._conn.execute(
            "UPDATE runs SET state = ?, updated_at = ?, terminal_reason_code = COALESCE(?, terminal_reason_code), "
            "decision = COALESCE(?, decision) WHERE run_id = ?",
            (state.value, rfc3339(), reason_code, decision, run_id),
        )

    def set_run_worktree(self, run_id: str, worktree_id: str) -> None:
        self._conn.execute("UPDATE runs SET worktree_id = ? WHERE run_id = ?", (worktree_id, run_id))

    def list_runs(
        self, *, project_id: str | None = None, state: RunState | None = None, limit: int = 20, since: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT r.* FROM runs r"
        where: list[str] = []
        params: list[Any] = []
        if project_id:
            sql += " JOIN tasks t ON t.task_id = r.task_id"
            where.append("t.project_id = ?")
            params.append(project_id)
        if state:
            where.append("r.state = ?")
            params.append(state.value)
        if since:
            where.append("r.created_at >= ?")
            params.append(since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def active_runs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE state NOT IN (?, ?, ?, ?, ?, ?) ORDER BY created_at ASC",
            (
                RunState.ACCEPTED.value,
                RunState.REJECTED.value,
                RunState.NEEDS_REVIEW.value,
                RunState.CANCELLED.value,
                RunState.FAILED.value,
                RunState.INFRASTRUCTURE_ERROR.value,
            ),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- attempts ----------------------------------------------------------

    def create_attempt(self, run_id: str, attempt_number: int) -> str:
        attempt_id = new_id("att")
        self._conn.execute(
            "INSERT INTO attempts (attempt_id, run_id, attempt_number, state, started_at, "
            "finished_at, termination_code) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
            (attempt_id, run_id, attempt_number, AttemptState.CREATED.value, rfc3339()),
        )
        return attempt_id

    def attempt_by_id(self, attempt_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_attempt_state(
        self, attempt_id: str, state: AttemptState, termination_code: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE attempts SET state = ?, termination_code = COALESCE(?, termination_code), "
            "finished_at = CASE WHEN ? THEN ? ELSE finished_at END WHERE attempt_id = ?",
            (
                state.value,
                termination_code,
                state in (AttemptState.PASSED, AttemptState.FAILED, AttemptState.EXHAUSTED,
                          AttemptState.CANCELLED, AttemptState.INTERRUPTED),
                rfc3339(),
                attempt_id,
            ),
        )

    def attempts_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM attempts WHERE run_id = ? ORDER BY attempt_number ASC", (run_id,)
            ).fetchall()
        ]

    # ---- workspaces --------------------------------------------------------

    def create_workspace(
        self,
        *,
        run_id: str,
        path: str,
        base_commit: str,
        initial_tree_digest: str,
        lock_owner: str,
    ) -> str:
        worktree_id = new_id("wt")
        self._conn.execute(
            "INSERT INTO workspaces (worktree_id, run_id, path, base_commit, initial_tree_digest, "
            "current_tree_digest, lock_owner, state, created_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
            (worktree_id, run_id, path, base_commit, initial_tree_digest, lock_owner, WorkspaceState.ACTIVE.value, rfc3339()),
        )
        return worktree_id

    def workspace_by_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_workspace_state(self, run_id: str, state: WorkspaceState, tree_digest: str | None = None) -> None:
        self._conn.execute(
            "UPDATE workspaces SET state = ?, current_tree_digest = COALESCE(?, current_tree_digest), "
            "removed_at = CASE WHEN ? THEN ? ELSE removed_at END WHERE run_id = ?",
            (state.value, tree_digest, state == WorkspaceState.REMOVED, rfc3339(), run_id),
        )

    # ---- runtime sessions --------------------------------------------------

    def create_session(
        self,
        *,
        run_id: str,
        adapter_id: str,
        adapter_version: str,
        external_session_id: str | None,
        runtime_version: str | None,
        metadata: dict[str, Any],
    ) -> str:
        session_id = new_id("sess")
        self._conn.execute(
            "INSERT INTO runtime_sessions (session_id, run_id, adapter_id, adapter_version, "
            "external_session_id, runtime_version, state, coverage_started_at, coverage_gap, "
            "metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)",
            (
                session_id,
                run_id,
                adapter_id,
                adapter_version,
                external_session_id,
                runtime_version,
                "active",
                json.dumps(metadata, sort_keys=True),
            ),
        )
        return session_id

    def session_by_id(self, session_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM runtime_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def mark_session_finished(self, session_id: str, state: str = "finished") -> None:
        self._conn.execute(
            "UPDATE runtime_sessions SET state = ? WHERE session_id = ?", (state, session_id)
        )

    def add_coverage_gap(self, session_id: str, gap_seconds: int) -> None:
        self._conn.execute(
            "UPDATE runtime_sessions SET coverage_gap = coverage_gap + ? WHERE session_id = ?",
            (gap_seconds, session_id),
        )

    # ---- model invocations -------------------------------------------------

    def create_invocation(
        self, *, run_id: str, attempt_id: str, turn: int, provider_id: str, model_ref: str, privacy_class: str
    ) -> str:
        invocation_id = new_id("inv")
        self._conn.execute(
            "INSERT INTO model_invocations (invocation_id, run_id, attempt_id, turn, provider_id, "
            "model_ref, privacy_class, started_at, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (invocation_id, run_id, attempt_id, turn, provider_id, model_ref, privacy_class, rfc3339(), "started"),
        )
        return invocation_id

    def finish_invocation(
        self,
        invocation_id: str,
        *,
        outcome: str,
        input_units: int | None = None,
        output_units: int | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE model_invocations SET outcome = ?, input_units = ?, output_units = ?, "
            "latency_ms = ?, error_code = ?, finished_at = ? WHERE invocation_id = ?",
            (outcome, input_units, output_units, latency_ms, error_code, rfc3339(), invocation_id),
        )

    # ---- tool proposals / decisions / executions ----------------------------

    def create_proposal(
        self,
        *,
        run_id: str,
        attempt_id: str,
        turn: int,
        tool_name: str,
        schema_version: int,
        arguments: dict[str, Any],
        arguments_sha256: str,
    ) -> str:
        proposal_id = new_id("prop")
        self._conn.execute(
            "INSERT INTO tool_proposals (proposal_id, run_id, attempt_id, turn, tool_name, "
            "schema_version, arguments_json, arguments_sha256, state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                run_id,
                attempt_id,
                turn,
                tool_name,
                schema_version,
                json.dumps(arguments, sort_keys=True),
                arguments_sha256,
                ToolProposalState.PROPOSED.value,
                rfc3339(),
            ),
        )
        return proposal_id

    def set_proposal_state(self, proposal_id: str, state: ToolProposalState) -> None:
        self._conn.execute(
            "UPDATE tool_proposals SET state = ? WHERE proposal_id = ?", (state.value, proposal_id)
        )

    def proposal_by_id(self, proposal_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM tool_proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def create_decision(
        self,
        *,
        proposal_id: str,
        policy_sha256: str,
        outcome: str,
        rule_id: str,
        reason_code: str,
        details: dict[str, Any],
    ) -> str:
        decision_id = new_id("dec")
        self._conn.execute(
            "INSERT INTO policy_decisions (decision_id, proposal_id, policy_sha256, outcome, "
            "rule_id, reason_code, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, proposal_id, policy_sha256, outcome, rule_id, reason_code,
             json.dumps(details, sort_keys=True), rfc3339()),
        )
        return decision_id

    def create_execution(self, proposal_id: str) -> str:
        execution_id = new_id("exec")
        self._conn.execute(
            "INSERT INTO tool_executions (execution_id, proposal_id, state) VALUES (?, ?, ?)",
            (execution_id, proposal_id, "started"),
        )
        return execution_id

    def finish_execution(
        self,
        execution_id: str,
        *,
        state: str,
        pid: int | None = None,
        process_group: int | None = None,
        exit_code: int | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE tool_executions SET state = ?, process_pid = ?, process_group = ?, exit_code = ?, "
            "result_json = ?, error_code = ?, finished_at = ? WHERE execution_id = ?",
            (
                state,
                pid,
                process_group,
                exit_code,
                json.dumps(result or {}, sort_keys=True),
                error_code,
                rfc3339(),
                execution_id,
            ),
        )

    def execution_by_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM tool_executions WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    # ---- checks ------------------------------------------------------------

    def create_check(
        self,
        *,
        run_id: str,
        attempt_id: str | None,
        check_id: str,
        phase: CheckPhase,
        argv_sha256: str,
        cwd_relative: str,
        input_snapshot_sha256: str,
        state: CheckState = CheckState.QUEUED,
    ) -> str:
        check_execution_id = new_id("chk")
        self._conn.execute(
            "INSERT INTO checks (check_execution_id, run_id, attempt_id, check_id, phase, "
            "argv_sha256, cwd_relative, input_snapshot_sha256, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (check_execution_id, run_id, attempt_id, check_id, phase.value, argv_sha256,
             cwd_relative, input_snapshot_sha256, state.value),
        )
        return check_execution_id

    def set_check_state(
        self,
        check_execution_id: str,
        state: CheckState,
        *,
        exit_code: int | None = None,
        stdout_artifact_id: str | None = None,
        stderr_artifact_id: str | None = None,
        result_artifact_id: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        now = rfc3339()
        self._conn.execute(
            "UPDATE checks SET state = ?, exit_code = COALESCE(?, exit_code), "
            "stdout_artifact_id = COALESCE(?, stdout_artifact_id), "
            "stderr_artifact_id = COALESCE(?, stderr_artifact_id), "
            "result_artifact_id = COALESCE(?, result_artifact_id), "
            "started_at = CASE WHEN ? AND started_at IS NULL THEN ? ELSE started_at END, "
            "finished_at = CASE WHEN ? THEN ? ELSE finished_at END "
            "WHERE check_execution_id = ?",
            (
                state.value,
                exit_code,
                stdout_artifact_id,
                stderr_artifact_id,
                result_artifact_id,
                state == CheckState.STARTED,
                now,
                state in (CheckState.PASSED, CheckState.FAILED, CheckState.TIMED_OUT,
                          CheckState.CANCELLED, CheckState.INFRASTRUCTURE_ERROR,
                          CheckState.BLOCKED_INPUT_CHANGED),
                now,
                check_execution_id,
            ),
        )

    def checks_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM checks WHERE run_id = ? ORDER BY rowid ASC, check_id ASC", (run_id,)
            ).fetchall()
        ]

    # ---- gates -------------------------------------------------------------

    def store_gate(
        self,
        *,
        run_id: str,
        gate_id: str,
        policy_sha256: str,
        state: str,
        reason_code: str,
        evidence_refs: list[str],
    ) -> str:
        gate_result_id = new_id("gate")
        self._conn.execute(
            "INSERT OR IGNORE INTO gates (gate_result_id, run_id, gate_id, policy_sha256, state, "
            "reason_code, evidence_refs_json, calculated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (gate_result_id, run_id, gate_id, policy_sha256, state, reason_code,
             json.dumps(evidence_refs, sort_keys=True), rfc3339()),
        )
        row = self._conn.execute(
            "SELECT gate_result_id FROM gates WHERE run_id = ? AND gate_id = ? AND policy_sha256 = ?",
            (run_id, gate_id, policy_sha256),
        ).fetchone()
        return row["gate_result_id"]

    def gates_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM gates WHERE run_id = ? ORDER BY calculated_at ASC, gate_id ASC", (run_id,)
            ).fetchall()
        ]

    # ---- resource leases ---------------------------------------------------

    def create_lease(
        self, *, run_id: str, provider: str, profile: str, estimated_memory_mb: int, state: LeaseState
    ) -> str:
        lease_id = new_id("lease")
        self._conn.execute(
            "INSERT INTO resource_leases (lease_id, run_id, provider, profile, "
            "estimated_memory_mb, state, granted_at, expires_at, released_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
            (lease_id, run_id, provider, profile, estimated_memory_mb, state.value),
        )
        return lease_id

    def set_lease_state(
        self,
        lease_id: str,
        state: LeaseState,
        *,
        granted_at: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE resource_leases SET state = ?, granted_at = COALESCE(?, granted_at), "
            "expires_at = COALESCE(?, expires_at), "
            "released_at = CASE WHEN ? THEN ? ELSE released_at END WHERE lease_id = ?",
            (state.value, granted_at, expires_at, state == LeaseState.RELEASED, rfc3339(), lease_id),
        )

    def leases_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM resource_leases WHERE run_id = ? ORDER BY rowid ASC", (run_id,)
            ).fetchall()
        ]

    def active_leases(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM resource_leases WHERE state IN (?, ?, ?, ?)",
                (LeaseState.REQUESTED.value, LeaseState.GRANTED.value, LeaseState.ACTIVE.value, LeaseState.QUEUED.value),
            ).fetchall()
        ]

    # ---- application commands -----------------------------------------------

    def begin_command(
        self,
        *,
        command_type: str,
        idempotency_key: str | None,
        actor: str,
        run_id: str | None,
        request_sha256: str,
    ) -> dict[str, Any]:
        """Persist `requested` before work, then the caller transitions to
        `running` before any external effect. Returns the command row or a
        replay result for an existing idempotency key.

        Replay semantics per STATE_AND_DATA_MODEL.md: requested/running is a
        state conflict; completed returns the stored result; failed returns
        the stored failure; uncertain requires reconciliation.
        """
        if idempotency_key is not None:
            existing = self._conn.execute(
                "SELECT * FROM application_commands WHERE command_type = ? AND idempotency_key = ?",
                (command_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                return dict(existing)
        command_id = new_id("cmd")
        self._conn.execute(
            "INSERT INTO application_commands (command_id, command_type, idempotency_key, actor, "
            "run_id, request_sha256, state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (command_id, command_type, idempotency_key, actor, run_id, request_sha256,
             CommandState.REQUESTED.value, rfc3339()),
        )
        return {"command_id": command_id, "replay": False}

    def mark_command_running(self, command_id: str) -> None:
        self._conn.execute(
            "UPDATE application_commands SET state = ? WHERE command_id = ?",
            (CommandState.RUNNING.value, command_id),
        )

    def finish_command(
        self,
        command_id: str,
        *,
        state: CommandState,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE application_commands SET state = ?, result_json = ?, error_code = ?, "
            "completed_at = ? WHERE command_id = ?",
            (state.value, json.dumps(result or {}, sort_keys=True), error_code, rfc3339(), command_id),
        )
