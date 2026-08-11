"""Append-only event ledger with monotonic per-run sequence and SHA-256 hash
chain (STATE_AND_DATA_MODEL.md).

The generic event envelope is not sufficient payload validation: every
event type and schema version has a dedicated strict payload schema
registered in EVENT_PAYLOAD_SCHEMAS. Unknown event types or payload fields
cannot enter the authoritative ledger.

The hash input is the UTF-8 bytes of canonical event JSON containing every
envelope field except `event_sha256`. `previous_event_sha256` is included
exactly once and is null for sequence 1. Canonical JSON uses sorted keys, no
insignificant whitespace, UTF-8 without ASCII escaping, and prohibits
floating-point payload values. The chain detects accidental corruption and
later local modification; it is not tamper-proof against an attacker
controlling the account.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from treepact.clock import rfc3339
from treepact.errors import EvidenceError
from treepact.ids import uuid7


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCreatedPayload(EventPayload):
    task_id: str
    mode: str
    runtime_id: str
    model_profile: str | None = None
    attempt_limit: int
    turn_limit: int
    deadline_at: str


class TransitionPayload(EventPayload):
    from_state: str | None = None
    to_state: str
    reason_code: str | None = None


class PactLoadedPayload(EventPayload):
    pact_sha256: str
    schema_version: int
    source_path: str


class PactCompiledPayload(EventPayload):
    pact_sha256: str
    check_ids: list[str] = Field(default_factory=list)
    gate_ids: list[str] = Field(default_factory=list)


class WorkspaceCreatedPayload(EventPayload):
    worktree_id: str
    path: str
    base_commit: str


class WorkspaceLockedPayload(EventPayload):
    worktree_id: str
    lock_owner: str


class WorkspaceChangeSignalledPayload(EventPayload):
    worktree_id: str
    source: str
    reported_paths: list[str] = Field(default_factory=list)


class WorkspaceReconciledPayload(EventPayload):
    worktree_id: str
    tree_digest: str
    changed_paths: list[str] = Field(default_factory=list)
    reason_code: str | None = None


class WorkspaceInvalidatedPayload(EventPayload):
    worktree_id: str
    reason_code: str


class WorkspaceRemovedPayload(EventPayload):
    worktree_id: str


class AttemptPayload(EventPayload):
    attempt_id: str
    attempt_number: int
    from_state: str | None = None
    to_state: str
    reason_code: str | None = None


class RuntimeSessionStartedPayload(EventPayload):
    session_id: str
    adapter_id: str
    adapter_version: str
    external_session_id: str | None = None
    runtime_version: str | None = None


class RuntimeSessionPayload(EventPayload):
    session_id: str
    reason_code: str | None = None
    error_code: str | None = None


class RuntimePermissionRequestedPayload(EventPayload):
    session_id: str
    tool_name: str
    arguments_sha256: str


class RuntimeToolReportedPayload(EventPayload):
    session_id: str
    tool_name: str
    report_outcome: str


class ResourcePayload(EventPayload):
    lease_id: str
    provider: str
    profile: str
    estimated_memory_mb: int
    from_state: str | None = None
    to_state: str | None = None
    reason_code: str | None = None


class ResourcePressureDetectedPayload(EventPayload):
    zone: str
    percent: float | None = None
    free_mb: int | None = None


class ModelInvocationStartedPayload(EventPayload):
    invocation_id: str
    attempt_id: str
    turn: int
    provider_id: str
    model_ref: str
    privacy_class: str


class ModelInvocationFinishedPayload(EventPayload):
    invocation_id: str
    input_units: int | None = None
    output_units: int | None = None
    latency_ms: int | None = None


class ModelInvocationFailedPayload(EventPayload):
    invocation_id: str
    error_code: str


class ToolProposedPayload(EventPayload):
    proposal_id: str
    attempt_id: str
    turn: int
    tool_name: str
    schema_version: int
    arguments_sha256: str


class PolicyDecisionPayload(EventPayload):
    decision_id: str
    proposal_id: str
    rule_id: str
    reason_code: str | None = None


class ToolExecutionPayload(EventPayload):
    proposal_id: str
    execution_id: str | None = None
    outcome: str | None = None
    error_code: str | None = None
    reason_code: str | None = None


class CheckPayload(EventPayload):
    check_execution_id: str
    check_id: str
    phase: str
    exit_code: int | None = None
    argv_sha256: str | None = None
    timeout_seconds: int | None = None
    error_code: str | None = None
    reason_code: str | None = None


class GateCalculatedPayload(EventPayload):
    gate_result_id: str
    gate_id: str
    state: str
    reason_code: str


class ArtifactPayload(EventPayload):
    artifact_id: str
    kind: str
    sha256: str
    size_bytes: int
    media_type: str
    reason_code: str | None = None


class ReportGeneratedPayload(EventPayload):
    report_kind: str
    path_relative: str


class EvidenceVerifiedPayload(EventPayload):
    scope: str
    ok: bool
    checked: int


class EvidenceExportedPayload(EventPayload):
    destination: str
    kind: str


EVENT_PAYLOAD_SCHEMAS: dict[str, type[EventPayload]] = {
    "run.created": RunCreatedPayload,
    "run.preparing": TransitionPayload,
    "run.ready": TransitionPayload,
    "run.waiting_resource": TransitionPayload,
    "run.started": TransitionPayload,
    "run.verifying": TransitionPayload,
    "run.interrupted": TransitionPayload,
    "run.resumed": TransitionPayload,
    "run.cancel_requested": TransitionPayload,
    "run.cancelled": TransitionPayload,
    "run.accepted": TransitionPayload,
    "run.rejected": TransitionPayload,
    "run.needs_review": TransitionPayload,
    "run.failed": TransitionPayload,
    "run.infrastructure_error": TransitionPayload,
    "pact.loaded": PactLoadedPayload,
    "pact.compiled": PactCompiledPayload,
    "workspace.created": WorkspaceCreatedPayload,
    "workspace.locked": WorkspaceLockedPayload,
    "workspace.change_signalled": WorkspaceChangeSignalledPayload,
    "workspace.reconciled": WorkspaceReconciledPayload,
    "workspace.invalidated": WorkspaceInvalidatedPayload,
    "workspace.removed": WorkspaceRemovedPayload,
    "attempt.created": AttemptPayload,
    "attempt.started": AttemptPayload,
    "attempt.planning": AttemptPayload,
    "attempt.acting": AttemptPayload,
    "attempt.checking": AttemptPayload,
    "attempt.failed": AttemptPayload,
    "attempt.completed": AttemptPayload,
    "attempt.exhausted": AttemptPayload,
    "attempt.interrupted": AttemptPayload,
    "attempt.cancelled": AttemptPayload,
    "runtime.session_started": RuntimeSessionStartedPayload,
    "runtime.permission_requested": RuntimePermissionRequestedPayload,
    "runtime.tool_reported": RuntimeToolReportedPayload,
    "runtime.coverage_gap": RuntimeSessionPayload,
    "runtime.idle": RuntimeSessionPayload,
    "runtime.failed": RuntimeSessionPayload,
    "runtime.session_finished": RuntimeSessionPayload,
    "resource.requested": ResourcePayload,
    "resource.granted": ResourcePayload,
    "resource.activated": ResourcePayload,
    "resource.queued": ResourcePayload,
    "resource.denied": ResourcePayload,
    "resource.released": ResourcePayload,
    "resource.expired": ResourcePayload,
    "resource.revoked": ResourcePayload,
    "resource.pressure_detected": ResourcePressureDetectedPayload,
    "model.invocation_started": ModelInvocationStartedPayload,
    "model.invocation_finished": ModelInvocationFinishedPayload,
    "model.invocation_failed": ModelInvocationFailedPayload,
    "tool.proposed": ToolProposedPayload,
    "policy.allowed": PolicyDecisionPayload,
    "policy.denied": PolicyDecisionPayload,
    "tool.started": ToolExecutionPayload,
    "tool.finished": ToolExecutionPayload,
    "tool.failed": ToolExecutionPayload,
    "tool.uncertain": ToolExecutionPayload,
    "tool.stale": ToolExecutionPayload,
    "check.queued": CheckPayload,
    "check.started": CheckPayload,
    "check.passed": CheckPayload,
    "check.failed": CheckPayload,
    "check.timed_out": CheckPayload,
    "check.cancelled": CheckPayload,
    "check.infrastructure_error": CheckPayload,
    "check.blocked": CheckPayload,
    "gate.calculated": GateCalculatedPayload,
    "artifact.created": ArtifactPayload,
    "artifact.redacted": ArtifactPayload,
    "report.generated": ReportGeneratedPayload,
    "evidence.verified": EvidenceVerifiedPayload,
    "evidence.exported": EvidenceExportedPayload,
}

SCHEMA_VERSION = 1


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a payload against its registered event-type schema.

    Rejects unknown event types and unknown or malformed payload fields
    before anything reaches the ledger.
    """
    schema = EVENT_PAYLOAD_SCHEMAS.get(event_type)
    if schema is None:
        raise EvidenceError(
            f"unknown event type {event_type!r} cannot enter the ledger",
            code="event_type_unknown",
        )
    try:
        return schema.model_validate(payload).model_dump(exclude_none=False)
    except Exception as exc:  # pydantic ValidationError
        raise EvidenceError(
            f"invalid payload for event type {event_type!r}: {exc}",
            code="event_payload_invalid",
        ) from exc


def canonical_json(obj: dict[str, Any]) -> str:
    """Canonical serialization: sorted keys, compact separators, UTF-8
    without ASCII escaping, no floats."""
    _reject_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _reject_floats(obj: Any) -> None:
    if isinstance(obj, dict):
        for value in obj.values():
            _reject_floats(value)
    elif isinstance(obj, list):
        for item in obj:
            _reject_floats(item)
    elif isinstance(obj, float):
        raise EvidenceError(
            "floating-point values are prohibited in event payloads",
            code="event_float_prohibited",
        )


def event_digest(envelope_without_digest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(envelope_without_digest).encode("utf-8")).hexdigest()


class EventStore:
    """Writes and reads the append-only event ledger for one data root."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(
        self,
        *,
        run_id: str,
        event_type: str,
        actor: str,
        correlation_id: str,
        pact_sha256: str,
        payload: dict[str, Any],
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one validated event inside the caller's transaction.

        The sequence is computed monotonically per run under the same
        transaction, so the ledger and relational state stay consistent.
        Returns the full envelope including the digest.
        """
        if not pact_sha256:
            raise EvidenceError("event requires a Pact hash", code="event_missing_pact")
        payload = validate_payload(event_type, payload)
        previous = self._conn.execute(
            "SELECT event_sha256 FROM events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        previous_sha = previous[0] if previous else None
        sequence = self._next_sequence(run_id)
        envelope = {
            "event_id": f"evt_{uuid7().replace('-', '')}",
            "run_id": run_id,
            "sequence": sequence,
            "event_type": event_type,
            "schema_version": SCHEMA_VERSION,
            "occurred_at": rfc3339(),
            "actor": actor,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "pact_sha256": pact_sha256,
            "payload": payload,
            "previous_event_sha256": previous_sha,
        }
        digest = event_digest(envelope)
        self._conn.execute(
            "INSERT INTO events (event_id, run_id, sequence, event_type, schema_version, "
            "occurred_at, actor, correlation_id, causation_id, pact_sha256, payload_json, "
            "previous_event_sha256, event_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                envelope["event_id"],
                run_id,
                sequence,
                event_type,
                SCHEMA_VERSION,
                envelope["occurred_at"],
                actor,
                correlation_id,
                causation_id,
                pact_sha256,
                canonical_json(payload),
                previous_sha,
                digest,
            ),
        )
        envelope["event_sha256"] = digest
        return envelope

    def _next_sequence(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(sequence) FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row[0]) + 1 if row and row[0] is not None else 1

    def head_digest(self, run_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT event_sha256 FROM events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return row[0] if row else None

    def events_for_run(self, run_id: str, since: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events WHERE run_id = ?"
        params: list[Any] = [run_id]
        if since:
            sql += " AND occurred_at >= ?"
            params.append(since)
        sql += " ORDER BY sequence ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def verify_chain(self, run_id: str) -> tuple[bool, str | None]:
        """Recompute the hash chain from the ledger rows. Returns (ok, message)."""
        rows = self._conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY sequence ASC", (run_id,)
        ).fetchall()
        previous = None
        for i, row in enumerate(rows):
            envelope = {
                "event_id": row["event_id"],
                "run_id": row["run_id"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "schema_version": row["schema_version"],
                "occurred_at": row["occurred_at"],
                "actor": row["actor"],
                "correlation_id": row["correlation_id"],
                "causation_id": row["causation_id"],
                "pact_sha256": row["pact_sha256"],
                "payload": json.loads(row["payload_json"]),
                "previous_event_sha256": row["previous_event_sha256"],
            }
            expected = event_digest(envelope)
            if expected != row["event_sha256"]:
                return False, f"event {i + 1} digest mismatch"
            if envelope["previous_event_sha256"] != previous:
                return False, f"event {i + 1} chain link mismatch"
            if i > 0 and envelope["sequence"] != i + 1:
                return False, f"event {i + 1} sequence gap"
            previous = expected
        return True, "ok"
