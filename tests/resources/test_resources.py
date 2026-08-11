"""Resource tests (RES-001..005, STATE-007, EVAL-009): lease serialization,
memory pressure blocking, waiting without consuming attempts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from treepact.config import Config, ProviderConfig, ResourceConfig, RetentionConfig
from treepact.enums import LeaseState, RunMode, RunState, RuntimeId
from treepact.evidence.events import EventStore
from treepact.resources.scheduler import MemoryObservation, ResourceScheduler, observe_memory
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.repository import Repository


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = open_connection(tmp_path / "db.sqlite")
    MigrationRunner(connection).migrate()
    return connection


def _cfg(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data", config_path=None, log_level="info",
        provider=ProviderConfig(), resources=ResourceConfig(reserved_memory_mb=1024),
        retention=RetentionConfig(), sources=("test",),
    )


def _run(conn: sqlite3.Connection) -> str:
    repo = Repository(conn)
    conn.execute("BEGIN IMMEDIATE")
    repo.register_project("demo", "/tmp/repo", "common", "Demo")
    pact_id = repo.store_pact(project_id="demo", schema_version=1, sha256="a" * 64,
                              canonical_json="{}", source_path="x")
    task_id = repo.create_task(project_id="demo", operator_text="t", mode=RunMode.REPAIR)
    run_id = repo.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE,
                             provider_id=None, model_profile="fast-code", base_commit="0" * 40,
                             assurance_level="TP3", attempt_limit=3, turn_limit=5,
                             deadline_at="2026-08-11T00:00:00Z")
    conn.execute("COMMIT")
    return run_id


class TestLeaseSerialization:
    def test_two_runs_cannot_hold_lease(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        scheduler = ResourceScheduler(conn, cfg)
        first = _run(conn)
        second = _run(conn)
        repo = Repository(conn)
        repo.set_run_state(first, RunState.RUNNING)  # production: engine transitions first
        lease = scheduler.acquire(run_id=first, profile="fast-code", estimated_memory_mb=1024,
                                  wait_for_resources=False, pact_sha256="a" * 64)
        with pytest.raises(Exception) as info:  # noqa: B017
            scheduler.acquire(run_id=second, profile="fast-code", estimated_memory_mb=1024,
                              wait_for_resources=False, pact_sha256="a" * 64)
        assert "another_mutating_run_active" in str(info.value)
        repo.set_run_state(first, RunState.ACCEPTED)
        scheduler.release(lease, "a" * 64, first)
        lease2 = scheduler.acquire(run_id=second, profile="fast-code", estimated_memory_mb=1024,
                                   wait_for_resources=False, pact_sha256="a" * 64)
        assert lease2

    def test_large_model_lease_serialized(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        scheduler = ResourceScheduler(conn, cfg)
        first = _run(conn)
        second = _run(conn)
        repo = Repository(conn)
        # first run finishes (terminal) but keeps an active large lease
        repo.set_run_state(first, RunState.ACCEPTED)
        scheduler.acquire(run_id=first, profile="deep-code", estimated_memory_mb=12000,
                          wait_for_resources=False, pact_sha256="a" * 64)
        with pytest.raises(Exception) as info:  # noqa: B017
            scheduler.acquire(run_id=second, profile="deep-code", estimated_memory_mb=12000,
                              wait_for_resources=False, pact_sha256="a" * 64)
        assert "large_model_lease_active" in str(info.value)


class TestMemoryPressure:
    def test_critical_pressure_blocks(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        scheduler = ResourceScheduler(conn, cfg)
        run_id = _run(conn)
        with patch("treepact.resources.scheduler.observe_memory", return_value=MemoryObservation(
            zone="critical", percent=95, free_mb=100, swap_percent=40
        )):
            with pytest.raises(Exception) as info:  # noqa: B017
                scheduler.acquire(run_id=run_id, profile="fast-code", estimated_memory_mb=1024,
                                  wait_for_resources=False, pact_sha256="a" * 64)
            assert "critical_memory_pressure" in str(info.value)

    def test_waiting_does_not_consume_attempt(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """STATE-007: a waiting run records resource.queued but no attempt."""
        cfg = _cfg(tmp_path)
        scheduler = ResourceScheduler(conn, cfg)
        first = _run(conn)
        second = _run(conn)
        repo = Repository(conn)
        repo.set_run_state(first, RunState.RUNNING)
        scheduler.acquire(run_id=first, profile="fast-code", estimated_memory_mb=1024,
                                  wait_for_resources=False, pact_sha256="a" * 64)
        with pytest.raises(Exception):  # noqa: B017 - any rejection acceptable
            scheduler.acquire(run_id=second, profile="fast-code", estimated_memory_mb=1024,
                              wait_for_resources=True, pact_sha256="a" * 64, wait_seconds=1)
        repo = Repository(conn)
        assert repo.attempts_for_run(second) == []
        leases = repo.leases_for_run(second)
        assert leases and leases[0]["state"] == LeaseState.DENIED.value
        events = EventStore(conn).events_for_run(second)
        assert any(e["event_type"] == "resource.queued" for e in events)
        assert not any(e["event_type"].startswith("attempt.") for e in events)

    def test_observe_memory_normal(self) -> None:
        obs = observe_memory()
        assert obs.zone in ("normal", "elevated", "critical")
        assert obs.free_mb > 0
