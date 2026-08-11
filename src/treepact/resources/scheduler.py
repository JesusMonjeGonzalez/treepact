"""Resource scheduling (MASTER_PLAN M6, SECURITY_MODEL resource controls).

Conservative local scheduling on the operator Mac:
- one global mutating-run lease;
- one large-model lease at a time;
- reserved memory for macOS and active applications;
- macOS memory pressure observed before expensive actions;
- critical pressure blocks new model/build work;
- waiting does not consume an attempt;
- no remote fallback and no silent swap-heavy attempt.

Loopback's resource API is not treated as stable in version 1; the local
provider is authoritative.
"""

from __future__ import annotations

import sqlite3
import time

import psutil

from treepact.clock import rfc3339
from treepact.config import Config
from treepact.enums import LeaseState, RunState
from treepact.errors import ResourceUnavailable
from treepact.evidence.events import EventStore
from treepact.storage.repository import Repository

CRITICAL_MEM_PERCENT = 90.0
CRITICAL_SWAP_PERCENT = 30.0
PRESSURE_SAMPLE_INTERVAL_SECONDS = 2.0

_MUTATING_STATES = (
    RunState.PREPARING,
    RunState.READY,
    RunState.RUNNING,
    RunState.WAITING_RESOURCE,
    RunState.VERIFYING,
    RunState.INTERRUPTED,
)


class MemoryObservation:
    def __init__(self, *, zone: str, percent: float, free_mb: int, swap_percent: float) -> None:
        self.zone = zone
        self.percent = percent
        self.free_mb = free_mb
        self.swap_percent = swap_percent

    @property
    def critical(self) -> bool:
        return self.zone == "critical"


def observe_memory() -> MemoryObservation:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    swap_percent = swap.percent if swap.total else 0.0
    if mem.percent >= CRITICAL_MEM_PERCENT or swap_percent >= CRITICAL_SWAP_PERCENT:
        zone = "critical"
    elif mem.percent >= 75.0 or swap_percent >= 10.0:
        zone = "elevated"
    else:
        zone = "normal"
    return MemoryObservation(
        zone=zone,
        percent=mem.percent,
        free_mb=mem.available // (1024 * 1024),
        swap_percent=swap_percent,
    )


class ResourceScheduler:
    """Version 1: one foreground mutating run owns a lease. Memory pressure
    is sampled before expensive actions; waiting does not consume attempts."""

    def __init__(self, conn: sqlite3.Connection, cfg: Config) -> None:
        self._conn = conn
        self._cfg = cfg
        self._repo = Repository(conn)
        self._store = EventStore(conn)

    def acquire(
        self,
        *,
        run_id: str,
        profile: str,
        estimated_memory_mb: int,
        wait_for_resources: bool,
        pact_sha256: str,
        wait_seconds: int = 3600,
    ) -> str:
        """Acquire the local mutating-run lease. Returns the lease ID.

        Raises ResourceUnavailable (exit 14) when resources are missing and
        waiting is not requested. Entering the wait loop consumes no attempt
        and keeps the run in `waiting_resource`."""
        lease_id = self._repo.create_lease(
            run_id=run_id,
            provider="local",
            profile=profile,
            estimated_memory_mb=estimated_memory_mb,
            state=LeaseState.REQUESTED,
        )
        self._store.append(
            run_id=run_id,
            event_type="resource.requested",
            actor="treepact",
            correlation_id=lease_id,
            pact_sha256=pact_sha256,
            payload={
                "lease_id": lease_id,
                "provider": "local",
                "profile": profile,
                "estimated_memory_mb": estimated_memory_mb,
            },
        )

        deadline = time.monotonic() + wait_seconds
        while True:
            reason = self._why_not(run_id, estimated_memory_mb)
            if reason is None:
                self._repo.set_lease_state(
                    lease_id, LeaseState.GRANTED, granted_at=rfc3339(),
                    expires_at=rfc3339(),
                )
                self._repo.set_lease_state(lease_id, LeaseState.ACTIVE)
                self._store.append(
                    run_id=run_id,
                    event_type="resource.granted",
                    actor="treepact",
                    correlation_id=lease_id,
                    pact_sha256=pact_sha256,
                    payload={
                        "lease_id": lease_id,
                        "provider": "local",
                        "profile": profile,
                        "estimated_memory_mb": estimated_memory_mb,
                    },
                )
                self._store.append(
                    run_id=run_id,
                    event_type="resource.activated",
                    actor="treepact",
                    correlation_id=lease_id,
                    pact_sha256=pact_sha256,
                    payload={
                        "lease_id": lease_id,
                        "provider": "local",
                        "profile": profile,
                        "estimated_memory_mb": estimated_memory_mb,
                    },
                )
                return lease_id

            self._store.append(
                run_id=run_id,
                event_type="resource.queued",
                actor="treepact",
                correlation_id=lease_id,
                pact_sha256=pact_sha256,
                payload={
                    "lease_id": lease_id,
                    "provider": "local",
                    "profile": profile,
                    "estimated_memory_mb": estimated_memory_mb,
                    "reason_code": reason,
                },
            )
            if not wait_for_resources:
                self._repo.set_lease_state(lease_id, LeaseState.DENIED)
                self._store.append(
                    run_id=run_id,
                    event_type="resource.denied",
                    actor="treepact",
                    correlation_id=lease_id,
                    pact_sha256=pact_sha256,
                    payload={
                        "lease_id": lease_id,
                        "provider": "local",
                        "profile": profile,
                        "estimated_memory_mb": estimated_memory_mb,
                        "reason_code": reason,
                    },
                )
                raise ResourceUnavailable(
                    f"resources unavailable: {reason}; retry with --wait-for-resources",
                    code="resource_unavailable",
                )
            if time.monotonic() >= deadline:
                self._repo.set_lease_state(lease_id, LeaseState.DENIED)
                self._store.append(
                    run_id=run_id,
                    event_type="resource.denied",
                    actor="treepact",
                    correlation_id=lease_id,
                    pact_sha256=pact_sha256,
                    payload={
                        "lease_id": lease_id,
                        "provider": "local",
                        "profile": profile,
                        "estimated_memory_mb": estimated_memory_mb,
                        "reason_code": "wait_timeout",
                    },
                )
                raise ResourceUnavailable(
                    f"timed out waiting for resources: {reason}", code="resource_wait_timeout"
                )
            time.sleep(PRESSURE_SAMPLE_INTERVAL_SECONDS)

    def release(self, lease_id: str, pact_sha256: str, run_id: str) -> None:
        self._repo.set_lease_state(lease_id, LeaseState.RELEASED)
        self._store.append(
            run_id=run_id,
            event_type="resource.released",
            actor="treepact",
            correlation_id=lease_id,
            pact_sha256=pact_sha256,
            payload={
                "lease_id": lease_id,
                "provider": "local",
                "profile": "unknown",
                "estimated_memory_mb": 0,
            },
        )

    # ---- decision -------------------------------------------------------------

    def _why_not(self, run_id: str, estimated_memory_mb: int) -> str | None:
        active_runs = self._repo.active_runs()
        mutating = [
            r for r in active_runs
            if r["run_id"] != run_id
            and RunState(r["state"]) in _MUTATING_STATES
        ]
        if mutating:
            return "another_mutating_run_active"

        active_leases = self._repo.active_leases()
        large = [
            lease
            for lease in active_leases
            if lease["run_id"] != run_id and lease["estimated_memory_mb"] >= self._cfg.resources.large_model_memory_mb
        ]
        if large:
            return "large_model_lease_active"

        obs = observe_memory()
        if obs.critical:
            return "critical_memory_pressure"

        budget = self._cfg.resources.reserved_memory_mb + estimated_memory_mb
        total_mb = psutil.virtual_memory().total // (1024 * 1024)
        if obs.free_mb < budget and obs.free_mb < total_mb // 3:
            return "insufficient_free_memory"
        return None
