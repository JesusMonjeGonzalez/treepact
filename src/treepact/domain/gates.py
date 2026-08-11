"""Final gates (MASTER_PLAN M6, ADR 0009, GATE-001..007).

Every gate references captured facts (check records, Git reconciliation,
artifact digests, event chain) never an agent statement. Gates determine
`accepted`, `rejected`, and `needs_review`.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from treepact.adapters.git import GitAdapter
from treepact.config import Config
from treepact.domain.pact import CompiledPact
from treepact.enums import CheckState, GateId, GateState
from treepact.evidence.artifacts import ArtifactStore
from treepact.evidence.events import EventStore
from treepact.storage.repository import Repository

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|credential)\s*[:=]\s*\S+"),
    re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
)


class GateResult:
    def __init__(self, gate_id: str, state: GateState, reason_code: str, evidence: list[str]) -> None:
        self.gate_id = gate_id
        self.state = state
        self.reason_code = reason_code
        self.evidence = evidence


class GateEvaluator:
    def __init__(
        self,
        conn: sqlite3.Connection,
        compiled: CompiledPact,
        run_id: str,
        worktree_path: Path,
        git: GitAdapter,
        cfg: Config,
    ) -> None:
        self._conn = conn
        self._compiled = compiled
        self._run_id = run_id
        self._worktree = worktree_path
        self._git = git
        self._cfg = cfg
        self._repo = Repository(conn)
        self._store = EventStore(conn)
        self._artifacts = ArtifactStore(conn, Path(str(cfg.data_dir)) / "artifacts")

    def evaluate_all(
        self, *, changed_paths: list[str], patch: str, tree_digest: str
    ) -> list[GateResult]:
        results: list[GateResult] = []
        for gate_id in self._compiled.pact.gates:
            if gate_id == GateId.REQUIRED_CHECKS_PASS:
                result = self._required_checks_pass()
            elif gate_id == GateId.NO_DENIED_PATHS_CHANGED:
                result = self._no_denied_paths_changed(changed_paths)
            elif gate_id == GateId.NO_SECRETS_IN_DIFF:
                result = self._no_secrets_in_diff(patch, changed_paths)
            elif gate_id == GateId.WORKTREE_CONSISTENT:
                result = self._worktree_consistent(tree_digest, changed_paths, patch)
            elif gate_id == GateId.EVIDENCE_COMPLETE:
                result = self._evidence_complete()
            else:  # pragma: no cover - schema restricts gate IDs
                result = GateResult(
                    gate_id.value, GateState.INSUFFICIENT_EVIDENCE,
                    "gate_not_supported", [],
                )
            self._persist(result)
            results.append(result)
        return results

    # ---- gates ---------------------------------------------------------------

    def _required_checks_pass(self) -> GateResult:
        checks = self._repo.checks_for_run(self._run_id)
        required = []
        for check in checks:
            declared = self._compiled.check(check["check_id"])
            if check["state"] != CheckState.DECLARED.value and declared is not None and declared.required:
                required.append(check)
        if not required:
            return GateResult(
                GateId.REQUIRED_CHECKS_PASS, GateState.INSUFFICIENT_EVIDENCE,
                "no_required_check_executions", [],
            )
        terminal = []
        for check in required:
            state = CheckState(check["state"])
            if state in (CheckState.PASSED, CheckState.FAILED, CheckState.TIMED_OUT,
                         CheckState.CANCELLED, CheckState.BLOCKED_INPUT_CHANGED,
                         CheckState.INFRASTRUCTURE_ERROR):
                terminal.append(check)
            elif state in (CheckState.QUEUED, CheckState.STARTED):
                return GateResult(
                    GateId.REQUIRED_CHECKS_PASS, GateState.INSUFFICIENT_EVIDENCE,
                    "check_not_terminal", [check["check_execution_id"]],
                )
        if not terminal:
            return GateResult(
                GateId.REQUIRED_CHECKS_PASS, GateState.INSUFFICIENT_EVIDENCE,
                "no_required_check_executions", [],
            )
        blocked = [check for check in terminal if check["state"] == CheckState.BLOCKED_INPUT_CHANGED.value]
        if blocked:
            return GateResult(
                GateId.REQUIRED_CHECKS_PASS, GateState.INSUFFICIENT_EVIDENCE,
                "check_inputs_changed",
                [check["check_execution_id"] for check in blocked],
            )
        failed = [
            check for check in terminal
            if check["state"] != CheckState.PASSED.value
        ]
        if failed:
            return GateResult(
                GateId.REQUIRED_CHECKS_PASS, GateState.FAILED,
                "required_check_failed",
                [check["check_execution_id"] for check in failed],
            )
        refs = [check["check_execution_id"] for check in terminal]
        return GateResult(GateId.REQUIRED_CHECKS_PASS, GateState.PASSED, "all_required_passed", refs)

    def _no_denied_paths_changed(self, changed_paths: list[str]) -> GateResult:
        denied = [p for p in changed_paths if self._compiled.is_denied(p)]
        if denied:
            return GateResult(
                GateId.NO_DENIED_PATHS_CHANGED, GateState.FAILED,
                "denied_path_changed", [f"path:{p}" for p in denied],
            )
        return GateResult(
            GateId.NO_DENIED_PATHS_CHANGED, GateState.PASSED,
            "no_denied_paths_changed", [f"path:{p}" for p in changed_paths],
        )

    def _no_secrets_in_diff(self, patch: str, changed_paths: list[str]) -> GateResult:
        findings: list[str] = []
        for number, line in enumerate(patch.splitlines(), start=1):
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                for pattern in _SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append(f"patch:line{number}")
                        break
        for rel in changed_paths:
            path = self._worktree / rel
            try:
                if path.is_file() and path.stat().st_size < 4 * 1024 * 1024:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    for pattern in _SECRET_PATTERNS:
                        if pattern.search(text):
                            findings.append(f"file:{rel}")
                            break
            except OSError:
                continue
        if findings:
            return GateResult(
                GateId.NO_SECRETS_IN_DIFF, GateState.FAILED,
                "secret_detected", findings[:20],
            )
        return GateResult(
            GateId.NO_SECRETS_IN_DIFF, GateState.PASSED,
            "no_secrets_detected", [f"paths:{len(changed_paths)}"],
        )

    def _worktree_consistent(self, tree_digest: str, changed_paths: list[str], patch: str) -> GateResult:
        workspace = self._repo.workspace_by_run(self._run_id)
        if workspace is None:
            return GateResult(
                GateId.WORKTREE_CONSISTENT, GateState.FAILED,
                "workspace_missing", [],
            )
        if workspace["state"] != "reconciled":
            return GateResult(
                GateId.WORKTREE_CONSISTENT, GateState.INSUFFICIENT_EVIDENCE,
                "workspace_not_reconciled", [workspace["worktree_id"]],
            )
        actual = self._git.tree_digest(self._worktree)
        if actual != tree_digest:
            return GateResult(
                GateId.WORKTREE_CONSISTENT, GateState.FAILED,
                "tree_digest_mismatch", [workspace["worktree_id"]],
            )
        actual_changed = self._git.diff_name_only(self._worktree, workspace["base_commit"])
        if set(actual_changed) != set(changed_paths):
            return GateResult(
                GateId.WORKTREE_CONSISTENT, GateState.FAILED,
                "changed_paths_mismatch", [f"path:{p}" for p in actual_changed],
            )
        if patch and not patch.strip():
            return GateResult(
                GateId.WORKTREE_CONSISTENT, GateState.INSUFFICIENT_EVIDENCE,
                "empty_patch_with_changes", [],
            )
        return GateResult(
            GateId.WORKTREE_CONSISTENT, GateState.PASSED,
            "tree_reconciled", [workspace["worktree_id"]],
        )

    def _evidence_complete(self) -> GateResult:
        refs: list[str] = []
        ok, message = self._store.verify_chain(self._run_id)
        if not ok:
            return GateResult(
                GateId.EVIDENCE_COMPLETE, GateState.FAILED,
                f"event_chain:{message}", [],
            )
        refs.append(f"chain:{self._store.head_digest(self._run_id)}")
        checks = self._repo.checks_for_run(self._run_id)
        for check in checks:
            for artifact_id in (check.get("stdout_artifact_id"), check.get("stderr_artifact_id"),
                                check.get("result_artifact_id")):
                if artifact_id and not self._artifacts.verify(artifact_id):
                    return GateResult(
                        GateId.EVIDENCE_COMPLETE, GateState.FAILED,
                        "artifact_missing", [artifact_id],
                    )
                if artifact_id:
                    refs.append(artifact_id)
        gates = self._repo.gates_for_run(self._run_id)
        if not gates:
            return GateResult(
                GateId.EVIDENCE_COMPLETE, GateState.INSUFFICIENT_EVIDENCE,
                "gates_not_calculated", [],
            )
        incomplete = [g for g in gates if g["state"] == "insufficient_evidence"]
        if incomplete:
            return GateResult(
                GateId.EVIDENCE_COMPLETE, GateState.INSUFFICIENT_EVIDENCE,
                "gate_insufficient", [g["gate_id"] for g in incomplete],
            )
        return GateResult(GateId.EVIDENCE_COMPLETE, GateState.PASSED, "evidence_verified", refs)

    # ---- persistence ----------------------------------------------------------

    def _persist(self, result: GateResult) -> None:
        gate_result_id = self._repo.store_gate(
            run_id=self._run_id,
            gate_id=result.gate_id,
            policy_sha256=self._compiled.policy_sha256,
            state=result.state.value,
            reason_code=result.reason_code,
            evidence_refs=result.evidence,
        )
        self._store.append(
            run_id=self._run_id,
            event_type="gate.calculated",
            actor="treepact",
            correlation_id=gate_result_id,
            pact_sha256=self._compiled.sha256,
            payload={
                "gate_result_id": gate_result_id,
                "gate_id": result.gate_id,
                "state": result.state.value,
                "reason_code": result.reason_code,
            },
        )


def decide(gates: list[GateResult]) -> tuple[str, str]:
    """Decision from gate facts: accepted only when every gate passed;
    rejected when a gate failed; needs_review when evidence is incomplete."""
    if not gates:
        return "needs_review", "no_gates_calculated"
    if all(gate.state == GateState.PASSED for gate in gates):
        return "accepted", "all_gates_passed"
    if any(gate.state == GateState.FAILED for gate in gates):
        return "rejected", next(gate.reason_code for gate in gates if gate.state == GateState.FAILED)
    return "needs_review", "evidence_incomplete"
