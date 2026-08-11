"""Run engine (TECHNICAL_ARCHITECTURE.md).

Drives run/attempt state transitions, workspace creation, the bounded
attempt loop, cancellation, and finalization. It never executes a runtime
tool without the tool broker. The model never determines the final outcome;
final decision calculation (gates) is milestone M6.
"""

from __future__ import annotations

import signal
import sqlite3
import time
from pathlib import Path
from typing import Any

from treepact.adapters.git import GitAdapter
from treepact.application.bootstrap import release_run_lock, write_run_lock
from treepact.checks.executor import CheckExecutor
from treepact.config import Config
from treepact.domain.pact import CompiledPact, compile_pact
from treepact.domain.state_machines import validate_transition
from treepact.enums import (
    AttemptState,
    CheckPhase,
    CheckState,
    RunMode,
    RunState,
    RuntimeId,
)
from treepact.errors import (
    CancellationError,
    EvidenceError,
    PolicyDenied,
    ProviderUnavailable,
    RepositoryError,
    ResourceUnavailable,
    StateConflict,
    WorkspaceError,
)
from treepact.evidence.events import EventStore
from treepact.ports.provider import ModelProvider
from treepact.resources.scheduler import ResourceScheduler
from treepact.runtime.native import NativeLoop
from treepact.storage.repository import Repository
from treepact.workspace.env import TempHome
from treepact.workspace.supervisor import WorkspaceSupervisor
from treepact.workspace.tools import ToolBroker


class RunEngine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cfg: Config,
        provider: ModelProvider | None = None,
        git: GitAdapter | None = None,
    ) -> None:
        self._conn = conn
        self._cfg = cfg
        self._provider = provider
        self._git = git or GitAdapter()
        self._repo = Repository(conn)
        self._store = EventStore(conn)
        self._temp_home = TempHome(cfg.data_dir / "tmp")
        self._cancelled = False
        self._active_broker: ToolBroker | None = None
        self._active_external: object | None = None
        self._install_sigterm_handler()

    def _install_sigterm_handler(self) -> None:
        """A SIGTERM from `treepact cancel` (or the OS) is converted into a
        cooperative cancellation at the next safe loop point; the engine
        never dies mid-transaction."""
        try:
            signal.signal(signal.SIGTERM, self._on_sigterm)
        except ValueError:  # not in the main thread
            pass

    def _on_sigterm(self, _signum: int, _frame: object) -> None:
        self.cancel()

    def cancel(self) -> None:
        self._cancelled = True
        if self._active_broker is not None:
            self._active_broker.cancel()
        if self._active_external is not None:
            cancel_method = getattr(self._active_external, "cancel", None)
            if cancel_method is not None:
                cancel_method()

    def _check_cancel_request(self, run_id: str) -> None:
        """Honor a cancel file written by `treepact cancel` from another
        terminal: persist the request, then stop at the next safe point."""
        cancel_path = self._cfg.data_dir / "locks" / f"{run_id}.cancel"
        if cancel_path.exists() or self._cancelled:
            if not self._cancelled:
                try:
                    reason = cancel_path.read_text(encoding="utf-8")[:500]
                except OSError:
                    reason = ""
                run = self._repo.run_by_id(run_id)
                if run:
                    self._store.append(
                        run_id=run_id,
                        event_type="run.cancel_requested",
                        actor="operator",
                        correlation_id=run_id,
                        pact_sha256=self._pact_sha(run),
                        payload={
                            "from_state": run["state"],
                            "to_state": RunState.CANCELLED.value,
                            "reason_code": reason or "operator_cancel",
                        },
                    )
            self.cancel()
            raise CancellationError("run cancelled", code="cancelled")

    # ---- transitions ---------------------------------------------------------

    def _transition(self, run_id: str, to_state: RunState, reason_code: str | None = None) -> None:
        run = self._repo.run_by_id(run_id)
        if run is None:
            raise RepositoryError(f"run {run_id} not found", code="run_not_found")
        from_state = RunState(run["state"])
        validate_transition(from_state, to_state)
        self._repo.set_run_state(run_id, to_state, reason_code=reason_code)
        event_type = _run_event_type(to_state)
        self._store.append(
            run_id=run_id,
            event_type=event_type,
            actor="treepact",
            correlation_id=run_id,
            pact_sha256=self._pact_sha(run),
            payload={
                "from_state": from_state.value,
                "to_state": to_state.value,
                "reason_code": reason_code,
            },
        )

    def _attempt_transition(
        self, attempt_id: str, to_state: AttemptState, termination_code: str | None = None
    ) -> None:
        attempt = self._repo.attempt_by_id(attempt_id)
        if attempt is None:
            raise StateConflict(f"attempt {attempt_id} not found", code="attempt_not_found")
        from_state = AttemptState(attempt["state"])
        validate_transition(from_state, to_state)
        self._repo.set_attempt_state(attempt_id, to_state, termination_code=termination_code)
        self._store.append(
            run_id=attempt["run_id"],
            event_type=_attempt_event_type(to_state),
            actor="treepact",
            correlation_id=attempt_id,
            pact_sha256=self._pact_sha(self._repo.run_by_id(attempt["run_id"]) or {}),
            payload={
                "attempt_id": attempt_id,
                "attempt_number": attempt["attempt_number"],
                "from_state": from_state.value,
                "to_state": to_state.value,
                "reason_code": termination_code,
            },
        )

    # ---- orchestration ---------------------------------------------------------

    def execute(
        self,
        run_id: str,
        task: str,
        *,
        wait_for_resources: bool = False,
        max_attempts: int | None = None,
        max_minutes: int | None = None,
    ) -> str:
        """Run the full workflow for a created/preparing run. Returns the
        terminal run state."""
        run = self._repo.run_by_id(run_id)
        if run is None:
            raise RepositoryError(f"run {run_id} not found", code="run_not_found")
        if RunState(run["state"]) not in (RunState.CREATED, RunState.PREPARING):
            raise StateConflict(
                f"run {run_id} is {run['state']}; only created/preparing runs can start",
                code="run_state_conflict",
            )

        write_run_lock(self._cfg.data_dir, run_id)
        try:
            self._transition(run_id, RunState.PREPARING, "engine_start")
            compiled = self._load_snapshot(run_id)
            self._store.append(
                run_id=run_id,
                event_type="pact.compiled",
                actor="treepact",
                correlation_id=run_id,
                pact_sha256=compiled.sha256,
                payload={
                    "pact_sha256": compiled.sha256,
                    "check_ids": compiled.check_ids(),
                    "gate_ids": compiled.gate_ids(),
                },
            )

            task_row = self._repo.task_by_id(run["task_id"])
            mode = RunMode(task_row["mode"] if task_row else "observe")

            # CLI limits may lower Pact limits, never raise them.
            attempt_limit = compiled.pact.limits.attempts
            if max_attempts is not None:
                attempt_limit = min(attempt_limit, max_attempts)
            minutes_limit = compiled.pact.limits.minutes
            if max_minutes is not None:
                minutes_limit = min(minutes_limit, max_minutes)

            self._transition(run_id, RunState.WAITING_RESOURCE, "resource_acquire")
            scheduler = ResourceScheduler(self._conn, self._cfg)
            try:
                lease_id = scheduler.acquire(
                    run_id=run_id,
                    profile=run.get("model_profile") or compiled.pact.limits.model_profile,
                    estimated_memory_mb=_profile_memory_estimate(
                        run.get("model_profile") or compiled.pact.limits.model_profile
                    ),
                    wait_for_resources=wait_for_resources,
                    pact_sha256=compiled.sha256,
                )
            except ResourceUnavailable:
                self._transition(run_id, RunState.FAILED, "resource_unavailable")
                raise
            self._transition(run_id, RunState.READY, "resources_granted")

            base_commit = run["base_commit"]
            worktree_id, worktree_path, _ = self._workspace(run_id, base_commit)
            self._transition(run_id, RunState.RUNNING, "engine_running")

            executor = CheckExecutor(
                self._conn, compiled, worktree_path, self._cfg, self._temp_home, self._git
            )
            executor.capture_baseline(run_id)

            deadline = time.time() + minutes_limit * 60
            attempts = 0
            while attempts < attempt_limit:
                self._check_cancel_request(run_id)
                attempts += 1
                outcome = self._run_attempt(
                    run_id=run_id,
                    attempt_number=attempts,
                    task=task,
                    mode=mode,
                    compiled=compiled,
                    worktree_path=worktree_path,
                    model_profile=run.get("model_profile") or compiled.pact.limits.model_profile,
                    deadline=deadline,
                )
                if outcome in ("finished", "passed"):
                    break
                if outcome == "failed":
                    raise ProviderUnavailable(
                        "model or runtime failed the attempt", code="provider_attempt_failed"
                    )
                if outcome == "cancelled":
                    raise CancellationError("run cancelled", code="cancelled")
                # exhausted -> next attempt or reject at the limit

            self._transition(run_id, RunState.VERIFYING, "attempts_finished")
            final_state = self.finalize(run_id, compiled, worktree_path, mode)
            scheduler.release(lease_id, compiled.sha256, run_id)
            return final_state
        except CancellationError:
            if self._repo.run_by_id(run_id):
                self._transition(run_id, RunState.CANCELLED, "cancelled")
            raise
        except ProviderUnavailable:
            if self._repo.run_by_id(run_id):
                self._transition(run_id, RunState.FAILED, "provider_unavailable")
            raise
        finally:
            release_run_lock(self._cfg.data_dir, run_id)

    def _run_attempt(
        self,
        *,
        run_id: str,
        attempt_number: int,
        task: str,
        mode: RunMode,
        compiled: CompiledPact,
        worktree_path: Path,
        model_profile: str,
        deadline: float,
    ) -> str:
        attempt_id = self._repo.create_attempt(run_id, attempt_number)
        self._attempt_transition(attempt_id, AttemptState.PLANNING, "begin")
        self._attempt_transition(attempt_id, AttemptState.ACTING, "loop")

        run = self._repo.run_by_id(run_id)
        runtime_id = RuntimeId(run["runtime_id"] if run else RuntimeId.NATIVE.value)
        if runtime_id == RuntimeId.OPENCODE:
            outcome = self._run_external_attempt(
                run_id=run_id,
                attempt_id=attempt_id,
                task=task,
                worktree_path=worktree_path,
                deadline=deadline,
            )
        else:
            outcome = self._run_native_attempt(
                run_id=run_id,
                attempt_id=attempt_id,
                task=task,
                mode=mode,
                compiled=compiled,
                worktree_path=worktree_path,
                model_profile=model_profile,
                deadline=deadline,
            )
        self._active_broker = None
        self._attempt_transition(
            attempt_id,
            AttemptState.CHECKING,
            f"loop_{outcome}",
        )
        if outcome == "finished":
            self._attempt_transition(attempt_id, AttemptState.PASSED, "finished")
            return "finished"
        if outcome == "cancelled":
            self._attempt_transition(attempt_id, AttemptState.CANCELLED, "cancelled")
            return "cancelled"
        if outcome == "failed":
            self._attempt_transition(attempt_id, AttemptState.FAILED, "provider_failed")
            return "failed"
        self._attempt_transition(attempt_id, AttemptState.EXHAUSTED, "exhausted")
        return "exhausted"

    def _run_native_attempt(
        self,
        *,
        run_id: str,
        attempt_id: str,
        task: str,
        mode: RunMode,
        compiled: CompiledPact,
        worktree_path: Path,
        model_profile: str,
        deadline: float,
    ) -> str:
        broker = ToolBroker(
            self._conn,
            compiled,
            worktree_path,
            self._cfg,
            run_id=run_id,
            attempt_id=attempt_id,
            turn=0,
            mode=mode,
            temp_home=self._temp_home,
            git=self._git,
            data_dir=self._cfg.data_dir,
        )
        self._active_broker = broker
        loop = NativeLoop(
            compiled=compiled,
            provider=self._provider,  # type: ignore[arg-type]
            broker=broker,
            run_id=run_id,
            attempt_id=attempt_id,
            mode=mode,
            model_profile=model_profile,
            max_turns=compiled.pact.limits.turns_per_attempt,
            deadline_unix=deadline,
            context_bytes=compiled.pact.limits.context_bytes,
            max_input_tokens=compiled.pact.limits.max_input_tokens,
            max_output_tokens=compiled.pact.limits.max_output_tokens,
            provider_timeout=float(self._cfg.provider.timeout_seconds),
        )
        result = loop.run(task)
        return result.outcome

    def _run_external_attempt(
        self,
        *,
        run_id: str,
        attempt_id: str,
        task: str,
        worktree_path: Path,
        deadline: float,
    ) -> str:
        """OpenCode adapter attempt: controlled server, session, events, and
        independent Git reconciliation afterwards."""
        from treepact.adapters.opencode import OpenCodeRuntimeAdapter

        run = self._repo.run_by_id(run_id)
        if run is None:
            raise RepositoryError(f"run {run_id} not found", code="run_not_found")
        task_row = self._repo.task_by_id(str(run["task_id"]))
        mode = task_row["mode"] if task_row else "observe"
        endpoint = self._cfg.provider.endpoint
        if not endpoint:
            raise ProviderUnavailable(
                "OpenCode runtime requires a configured loopback provider endpoint",
                code="provider_not_configured",
            )
        adapter = OpenCodeRuntimeAdapter(
            self._conn,
            self._cfg,
            self._store,
            self._repo,
            run_id=run_id,
            pact_sha256=self._pact_sha(run),
            temp_home=self._temp_home,
            attempt_id=attempt_id,
        )
        self._active_external = adapter
        try:
            result = adapter.run_attempt(
                worktree=worktree_path,
                task=task,
                provider_endpoint=endpoint,
                deadline_unix=deadline,
                mode=mode,
            )
        finally:
            self._active_external = None
        return result["outcome"]

    def resume(self, run_id: str, task: str, *, wait_for_resources: bool = False) -> str:
        """Resume a previously interrupted run after strict verification.
        Resume cannot change runtime, Pact, base commit, repository, or
        provider: all are taken from the stored run record. Uncertain
        effects block automatic resume (STATE-004/CLI-006)."""
        from treepact.storage.recovery import verify_resume_conditions

        run = self._repo.run_by_id(run_id)
        if run is None:
            raise RepositoryError(f"run {run_id} not found", code="run_not_found")
        if RunState(run["state"]) != RunState.INTERRUPTED:
            raise StateConflict(
                f"run {run_id} is {run['state']}; only interrupted runs can be resumed",
                code="run_state_conflict",
            )
        problems = verify_resume_conditions(self._conn, self._cfg, run_id)
        if problems:
            self._transition(run_id, RunState.NEEDS_REVIEW, "resume_rejected")
            raise StateConflict(
                f"run {run_id} cannot be resumed: {', '.join(problems)}; "
                "preserve the evidence and create a new run",
                code="resume_rejected",
            )

        compiled = self._load_snapshot(run_id)
        workspace = self._repo.workspace_by_run(run_id)
        if workspace is None:
            raise WorkspaceError("run has no workspace", code="workspace_missing")
        worktree_path = Path(workspace["path"])
        task_row = self._repo.task_by_id(run["task_id"])
        mode = RunMode(task_row["mode"] if task_row else "observe")

        write_run_lock(self._cfg.data_dir, run_id)
        try:
            self._transition(run_id, RunState.RUNNING, "resumed")
            executor = CheckExecutor(
                self._conn, compiled, worktree_path, self._cfg, self._temp_home, self._git
            )
            executor.capture_baseline(run_id)
            attempts_done = len(self._repo.attempts_for_run(run_id))
            attempt_limit = min(compiled.pact.limits.attempts, run["attempt_limit"])
            deadline = time.time() + compiled.pact.limits.minutes * 60
            while attempts_done < attempt_limit:
                self._check_cancel_request(run_id)
                attempts_done += 1
                outcome = self._run_attempt(
                    run_id=run_id,
                    attempt_number=attempts_done,
                    task=task,
                    mode=mode,
                    compiled=compiled,
                    worktree_path=worktree_path,
                    model_profile=run.get("model_profile") or compiled.pact.limits.model_profile,
                    deadline=deadline,
                )
                if outcome in ("finished", "passed"):
                    break
                if outcome == "failed":
                    raise ProviderUnavailable(
                        "model or runtime failed the attempt", code="provider_attempt_failed"
                    )
                if outcome == "cancelled":
                    raise CancellationError("run cancelled", code="cancelled")
            self._transition(run_id, RunState.VERIFYING, "resumed_attempts_finished")
            return self.finalize(run_id, compiled, worktree_path, mode)
        except CancellationError:
            if self._repo.run_by_id(run_id):
                self._transition(run_id, RunState.CANCELLED, "cancelled")
            raise
        except ProviderUnavailable:
            if self._repo.run_by_id(run_id):
                self._transition(run_id, RunState.FAILED, "provider_unavailable")
            raise
        finally:
            release_run_lock(self._cfg.data_dir, run_id)

    # ---- workspace --------------------------------------------------------------

    def _workspace(self, run_id: str, base_commit: str) -> tuple[str, Path, str]:
        supervisor = WorkspaceSupervisor(self._conn, self._cfg, git=self._git, store=self._store, repo=self._repo)
        try:
            return supervisor.create(run_id, base_commit)
        except Exception as exc:
            self._transition(run_id, RunState.FAILED, "workspace_failed")
            raise WorkspaceError(f"workspace creation failed: {exc}", code="workspace_creation_failed") from exc

    # ---- finalization (gates are milestone M6) ------------------------------------

    def finalize(
        self,
        run_id: str,
        compiled: CompiledPact,
        worktree_path: Path,
        mode: RunMode,
    ) -> str:
        """Final verification: independent Git reconciliation, final-phase
        checks, gate calculation from captured facts, decision, and the
        Decision Bundle. Returns the terminal run state."""
        from treepact.domain.gates import GateEvaluator, decide
        from treepact.evidence.bundle import generate_bundle

        supervisor = WorkspaceSupervisor(self._conn, self._cfg, git=self._git, store=self._store, repo=self._repo)
        reconciled = supervisor.reconcile(run_id)
        changed_paths = list(reconciled["changed_paths"])  # type: ignore[arg-type]
        patch = str(reconciled["patch"])
        tree_digest = str(reconciled["tree_digest"])

        from treepact.evidence.artifacts import ArtifactStore

        artifact_store = ArtifactStore(self._conn, self._cfg.data_dir / "artifacts")
        if patch.strip():
            artifact_store.store_bytes(
                run_id=run_id, kind="diff:final", data=patch.encode("utf-8"),
                media_type="text/x-patch",
            )
        bundle_dir = self._cfg.data_dir / "runs" / run_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "diff.patch").write_text(patch, encoding="utf-8")

        final_executor = CheckExecutor(
            self._conn, compiled, worktree_path, self._cfg, self._temp_home, self._git
        )
        for check_id, check in compiled.pact.checks.items():
            if CheckPhase.FINAL not in check.phases:
                continue
            if not check.required:
                continue
            existing = [
                c for c in self._repo.checks_for_run(run_id)
                if c["check_id"] == check_id and c["phase"] == CheckPhase.FINAL.value
                and c["state"] == CheckState.PASSED.value
            ]
            if existing:
                continue
            try:
                final_executor.run(
                    run_id=run_id,
                    attempt_id=None,
                    check_id=check_id,
                    phase=CheckPhase.FINAL,
                    mode=mode,
                )
            except PolicyDenied:
                # A required check not permitted in this mode stays
                # non-terminal; the required_checks_pass gate reports
                # insufficient evidence and the run ends needs_review.
                continue

        evaluator = GateEvaluator(self._conn, compiled, run_id, worktree_path, self._git, self._cfg)
        gate_results = evaluator.evaluate_all(
            changed_paths=changed_paths, patch=patch, tree_digest=tree_digest
        )
        decision, reason = decide(gate_results)
        terminal = {
            "accepted": RunState.ACCEPTED,
            "rejected": RunState.REJECTED,
            "needs_review": RunState.NEEDS_REVIEW,
        }[decision]
        self._repo.set_run_state(run_id, terminal, reason_code=reason, decision=decision)

        try:
            generate_bundle(self._conn, self._cfg, run_id)
        except Exception as exc:
            self._transition(run_id, RunState.INFRASTRUCTURE_ERROR, "bundle_generation_failed")
            raise EvidenceError(
                f"Decision Bundle generation failed: {exc}", code="bundle_failed"
            ) from exc

        self._store.append(
            run_id=run_id,
            event_type=_run_event_type(terminal),
            actor="treepact",
            correlation_id=run_id,
            pact_sha256=compiled.sha256,
            payload={
                "from_state": RunState.VERIFYING.value,
                "to_state": terminal.value,
                "reason_code": reason,
            },
        )
        self._temp_home.cleanup()
        return terminal.value

    # ---- helpers ---------------------------------------------------------------

    def _load_snapshot(self, run_id: str) -> CompiledPact:
        """A run uses an immutable snapshot of the Pact; it is never reloaded
        mid-run. Recompiling the stored canonical JSON is deterministic."""
        run = self._repo.run_by_id(run_id)
        if run is None:
            raise RepositoryError(f"run {run_id} not found", code="run_not_found")
        pact = self._repo.pact_by_id(run["pact_id"])
        if pact is None:
            raise RepositoryError(f"Pact snapshot for run {run_id} is missing", code="pact_snapshot_missing")
        return compile_pact(str(pact["canonical_json"]), source_name=str(pact["source_path"]))

    def _pact_sha(self, run: dict[str, Any]) -> str:
        if not run:
            return ""
        pact = self._repo.pact_by_id(str(run.get("pact_id") or ""))
        return pact["sha256"] if pact else ""


def _profile_memory_estimate(profile: str) -> int:
    """Conservative model-size memory estimate in MB per profile. Build
    estimates (Swift/Gradle) are added by the scheduler when observed."""
    return {
        "classify": 2048,
        "fast-code": 6144,
        "deep-code": 12288,
    }.get(profile, 4096)


def _run_event_type(state: RunState) -> str:
    return {
        RunState.PREPARING: "run.preparing",
        RunState.READY: "run.ready",
        RunState.RUNNING: "run.started",
        RunState.VERIFYING: "run.verifying",
        RunState.ACCEPTED: "run.accepted",
        RunState.REJECTED: "run.rejected",
        RunState.NEEDS_REVIEW: "run.needs_review",
        RunState.CANCELLED: "run.cancelled",
        RunState.FAILED: "run.failed",
        RunState.INFRASTRUCTURE_ERROR: "run.infrastructure_error",
        RunState.WAITING_RESOURCE: "run.waiting_resource",
        RunState.INTERRUPTED: "run.interrupted",
    }[state]


def _attempt_event_type(state: AttemptState) -> str:
    """The event catalog uses `attempt.completed` for the passed state."""
    return {
        AttemptState.PLANNING: "attempt.planning",
        AttemptState.ACTING: "attempt.acting",
        AttemptState.CHECKING: "attempt.checking",
        AttemptState.PASSED: "attempt.completed",
        AttemptState.FAILED: "attempt.failed",
        AttemptState.EXHAUSTED: "attempt.exhausted",
        AttemptState.CANCELLED: "attempt.cancelled",
        AttemptState.INTERRUPTED: "attempt.interrupted",
    }[state]
