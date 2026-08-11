"""Stage 8 fixture: the same Pact semantics through the native loop and the
OpenCode external runtime on one small evaluation."""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from treepact.adapters.git import GitAdapter
from treepact.adapters.http_provider import OpenAICompatibleProvider
from treepact.application.run_engine import RunEngine
from treepact.config import Config, ProviderConfig, ResourceConfig, RetentionConfig
from treepact.domain.pact import compile_pact
from treepact.enums import RunMode, RuntimeId
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.repository import Repository

PACT = """\
version: 1
project:
  id: cmp
  toolchain: python
workspace:
  readable:
    - .
  writable: []
  denied:
    - .env
checks:
  smoke:
    argv: ["python3", "-m", "compileall", "-q", "src"]
    timeout_seconds: 120
    required: false
    phases: ["attempt", "final"]
    modes: ["observe", "repair"]
gates:
  - required_checks_pass
  - no_denied_paths_changed
  - no_secrets_in_diff
  - worktree_consistent
  - evidence_complete
limits:
  attempts: 2
  turns_per_attempt: 5
  minutes: 5
  context_bytes: 100000
  max_input_tokens: 2000
  max_output_tokens: 1000
  model_profile: fast-code
  memory_mb: 2000
  network: {runtime: loopback_only, checks: denied}
actions:
  unavailable: [commit, push, merge, publish, deploy, release, access_credentials, modify_pact, destructive_delete, external_message]
"""


class StaticProvider(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "local-model"}]}).encode())

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "choices": [{"message": {"role": "assistant",
                                     "content": "The repository is minimal and consistent.",
                                     "tool_calls": []}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 4},
        }).encode())


def _start_provider() -> int:
    server = HTTPServer(("127.0.0.1", 0), StaticProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def _make_repo(tmp: Path, name: str) -> tuple[Path, str]:
    repo = tmp / name
    repo.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "c@t"],
        ["git", "config", "user.name", "C"],
    ):
        subprocess.run(command, cwd=repo, check=True)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("x = 1\n")
    (repo / ".treepact.yaml").write_text(PACT)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    return repo, subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                                text=True, check=True).stdout.strip()


def _run_native(tmp: Path, repo: Path, port: int) -> dict[str, object]:
    git = GitAdapter()
    root, common = git.discover_root(repo)
    cfg = Config(data_dir=tmp / f"data-{repo.name}", config_path=None, log_level="info",
                 provider=ProviderConfig(endpoint=f"http://127.0.0.1:{port}/v1"),
                 resources=ResourceConfig(), retention=RetentionConfig(), sources=("cmp",))
    conn = open_connection(cfg.data_dir / "db" / "treepact.sqlite")
    MigrationRunner(conn).migrate()
    store_repo = Repository(conn)
    compiled = compile_pact((repo / ".treepact.yaml").read_text(), source_name=".treepact.yaml")
    conn.execute("BEGIN IMMEDIATE")
    store_repo.register_project("cmp", str(root), common, "Cmp")
    pact_id = store_repo.store_pact(project_id="cmp", schema_version=1, sha256=compiled.sha256,
                                    canonical_json=compiled.canonical_json, source_path="x")
    task_id = store_repo.create_task(project_id="cmp", operator_text="diagnose", mode=RunMode.OBSERVE)
    run_id = store_repo.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.NATIVE,
                                   provider_id="loopback", model_profile="fast-code",
                                   base_commit=git.head_commit(root), assurance_level="TP3",
                                   attempt_limit=2, turn_limit=5,
                                   deadline_at="2026-08-11T00:00:00Z")
    conn.execute("COMMIT")
    provider = OpenAICompatibleProvider(endpoint=f"http://127.0.0.1:{port}/v1",
                                        profiles={"fast-code": "local-model"})
    engine = RunEngine(conn, cfg, provider=provider, git=git)
    final = engine.execute(run_id, "diagnose the repository")
    run = store_repo.run_by_id(run_id)
    gates = {g["gate_id"]: g["state"] for g in store_repo.gates_for_run(run_id)}
    return {"runtime": "native", "decision": run["decision"], "state": final,
            "pact": compiled.sha256, "gates": gates, "assurance": run["assurance_level"]}


def _run_opencode(tmp: Path, repo: Path, port: int) -> dict[str, object]:
    git = GitAdapter()
    root, common = git.discover_root(repo)
    cfg = Config(data_dir=tmp / f"data-{repo.name}", config_path=None, log_level="info",
                 provider=ProviderConfig(endpoint=f"http://127.0.0.1:{port}/v1"),
                 resources=ResourceConfig(), retention=RetentionConfig(), sources=("cmp",))
    conn = open_connection(cfg.data_dir / "db" / "treepact.sqlite")
    MigrationRunner(conn).migrate()
    store_repo = Repository(conn)
    compiled = compile_pact((repo / ".treepact.yaml").read_text(), source_name=".treepact.yaml")
    conn.execute("BEGIN IMMEDIATE")
    store_repo.register_project("cmp", str(root), common, "Cmp")
    pact_id = store_repo.store_pact(project_id="cmp", schema_version=1, sha256=compiled.sha256,
                                    canonical_json=compiled.canonical_json, source_path="x")
    task_id = store_repo.create_task(project_id="cmp", operator_text="diagnose", mode=RunMode.OBSERVE)
    run_id = store_repo.create_run(task_id=task_id, pact_id=pact_id, runtime_id=RuntimeId.OPENCODE,
                                   provider_id="loopback", model_profile="fast-code",
                                   base_commit=git.head_commit(root), assurance_level="TP2",
                                   attempt_limit=2, turn_limit=5,
                                   deadline_at="2026-08-11T00:00:00Z")
    conn.execute("COMMIT")

    engine = RunEngine(conn, cfg, provider=None, git=git)
    final = engine.execute(run_id, "diagnose the repository")
    run = store_repo.run_by_id(run_id)
    gates = {g["gate_id"]: g["state"] for g in store_repo.gates_for_run(run_id)}
    return {"runtime": "opencode", "decision": run["decision"], "state": final,
            "pact": compiled.sha256, "gates": gates, "assurance": run["assurance_level"]}


def run_runtime_comparison(tmp: Path) -> dict[str, object]:
    port = _start_provider()
    native_repo, _ = _make_repo(tmp, "native")
    opencode_repo, _ = _make_repo(tmp, "opencode")
    native = _run_native(tmp, native_repo, port)
    try:
        external = _run_opencode(tmp, opencode_repo, port)
    except Exception as exc:  # noqa: BLE001
        return {"match": False, "native": native,
                "opencode_error": f"{type(exc).__name__}: {exc}"}
    same_pact = native["pact"] == external["pact"]
    same_gates = native["gates"] == external["gates"]
    return {
        "match": same_pact and same_gates and native["decision"] == external["decision"],
        "native": native,
        "opencode": external,
        "same_pact": same_pact,
        "same_gates": same_gates,
    }
