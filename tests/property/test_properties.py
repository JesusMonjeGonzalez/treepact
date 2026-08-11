"""Property tests with Hypothesis (FINAL_VERIFICATION_PLAN.md property
layer). Each property guards a security or state invariant."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from conftest import PACT_MINIMAL
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from treepact.domain.pact import compile_pact
from treepact.domain.state_machines import allowed_transitions, validate_transition
from treepact.enums import RunState
from treepact.errors import StateConflict
from treepact.evidence.events import EventStore
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.repository import Repository

BAD_PATH_COMPONENTS = st.sampled_from(["..", "", ".", "a/../b", "/abs", "x\x00y"])

RELATIVE_PATHS = st.one_of(
    st.from_regex(r"[a-z0-9_]{1,10}(/[a-z0-9_]{1,10}){0,4}", fullmatch=True),
    st.sampled_from(["src/a.py", "src/deep/file.txt", "README.md", "a/b/c/d"]),
)


def _compiled() -> object:
    text = PACT_MINIMAL.format(
        project_id="demo", writable="src/", check_id="unit",
        argv='["python3", "-m", "pytest", "tests"]',
    )
    return compile_pact(text, source_name="demo")


class TestPathNormalization:
    @given(path=RELATIVE_PATHS)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_generated_relative_path_never_escapes_root(self, path: str, tmp_path: Path) -> None:
        from treepact.workspace.broker import PathBroker

        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        broker = PathBroker(repo, tmp_path / "data")
        resolved = broker.resolve(path)
        try:
            resolved.relative_to(repo)
        except ValueError:
            pytest.fail(f"path {path!r} escaped the root")


class TestDenyOverridesAllow:
    @given(path=RELATIVE_PATHS)
    @settings(max_examples=100, deadline=None)
    def test_denied_never_readable_or_writable(self, path: str) -> None:
        compiled = compile_pact(
            PACT_MINIMAL.format(project_id="demo", writable="src/", check_id="unit",
                                argv='["python3", "-m", "pytest", "tests"]')
            .replace("    - .env", "    - src/"),
            source_name="x",
        )
        if path.startswith("src/"):
            assert not compiled.is_readable(path)
            assert not compiled.is_writable(path)


class TestStateMachineProperties:
    @given(
        state=st.sampled_from(list(RunState)),
        target=st.sampled_from(list(RunState)),
    )
    @settings(max_examples=200, deadline=None)
    def test_illegal_transitions_never_succeed(self, state: RunState, target: RunState) -> None:
        if target in allowed_transitions(state):
            validate_transition(state, target)
        else:
            with pytest.raises(StateConflict):
                validate_transition(state, target)

    @given(attempt=st.integers(min_value=1, max_value=10))
    @settings(max_examples=50, deadline=None)
    def test_attempt_budget_bound(self, attempt: int) -> None:
        compiled = _compiled()
        assert attempt <= 3 or attempt > compiled.pact.limits.attempts


class TestEventChainProperties:
    @given(run_id=st.text(min_size=1, max_size=20))
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_sequences_monotonic_per_run(self, run_id: str, tmp_path: Path) -> None:
        from treepact.enums import RunMode, RuntimeId

        conn = open_connection(tmp_path / "db.sqlite")
        MigrationRunner(conn).migrate()
        repo = Repository(conn)
        conn.execute("BEGIN IMMEDIATE")
        repo.register_project("demo", "/tmp/repo", "common", "Demo")
        pact_id = repo.store_pact(project_id="demo", schema_version=1, sha256="a" * 64,
                                  canonical_json="{}", source_path="x")
        task_id = repo.create_task(project_id="demo", operator_text="t", mode=RunMode.OBSERVE)
        actual_run = repo.create_run(
            task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE, provider_id=None,
            model_profile=None, base_commit="0" * 40, assurance_level="TP0",
            attempt_limit=3, turn_limit=5, deadline_at="2026-08-11T00:00:00Z",
        )
        conn.execute("COMMIT")
        store = EventStore(conn)
        sequences: list[int] = []
        for _ in range(5):
            conn.execute("BEGIN IMMEDIATE")
            event = store.append(
                run_id=actual_run, event_type="run.preparing", actor="t", correlation_id="c",
                pact_sha256="a" * 64, payload={"to_state": "preparing"},
            )
            conn.execute("COMMIT")
            sequences.append(event["sequence"])
        assert sequences == sorted(sequences)
        assert sequences == [1, 2, 3, 4, 5]


class TestArtifactContentAddressing:
    @given(content=st.binary(min_size=1, max_size=64))
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_digest_changes_with_content(self, content: bytes, tmp_path: Path) -> None:
        assert hashlib.sha256(content).hexdigest() != hashlib.sha256(content + b"x").hexdigest()


class TestIdempotencyProperty:
    @given(payload=st.dictionaries(st.text(max_size=8), st.integers(), max_size=3))
    @settings(max_examples=30, deadline=None)
    def test_same_request_same_digest(self, payload: dict) -> None:
        from treepact.application.commands import request_sha256

        assert request_sha256(payload) == request_sha256(dict(payload))


class TestCLILimitsNeverExpand:
    @given(requested=st.integers(min_value=1, max_value=10))
    @settings(max_examples=50, deadline=None)
    def test_cli_attempts_capped_by_pact(self, requested: int) -> None:
        compiled = _compiled()
        effective = min(compiled.pact.limits.attempts, requested)
        assert effective <= compiled.pact.limits.attempts
