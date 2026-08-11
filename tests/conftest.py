"""Shared fixtures for the consolidated M9 campaign."""

from __future__ import annotations

import json
import pathlib
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from treepact.config import Config, ProviderConfig, ResourceConfig, RetentionConfig

PACT_MINIMAL = """\
version: 1
project:
  id: {project_id}
  toolchain: python
workspace:
  readable:
    - .
  writable:
    - {writable}
  denied:
    - .env
checks:
  {check_id}:
    argv: {argv}
    timeout_seconds: 120
    required: true
    phases: ["attempt", "final"]
    modes: ["observe", "repair"]
gates:
  - required_checks_pass
  - no_denied_paths_changed
  - no_secrets_in_diff
  - worktree_consistent
  - evidence_complete
limits:
  attempts: 3
  turns_per_attempt: 10
  minutes: 5
  context_bytes: 100000
  max_input_tokens: 2000
  max_output_tokens: 1000
  model_profile: fast-code
  memory_mb: 2000
  network:
    runtime: loopback_only
    checks: denied
actions:
  unavailable:
    - commit
    - push
    - merge
    - publish
    - deploy
    - release
    - access_credentials
    - modify_pact
    - destructive_delete
    - external_message
"""


class FakeModelServer:
    """Controlled fake OpenAI-compatible loopback endpoint for the campaign.

    Scenarios: plain narrative, tool calls, malformed tool calls, errors,
    hangs, and secret-bearing responses.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.scenario: dict = {"kind": "narrative", "text": "done"}
        self._server = HTTPServer(("127.0.0.1", 0), self._handler_factory())
        self.port = self._server.server_address[1]
        self.thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_factory(self) -> type:
        owner = self

        class Handler(BaseHTTPRequestHandler):
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
                body = json.loads(self.rfile.read(length))
                owner.requests.append(body)
                scenario = owner.scenario
                if scenario["kind"] == "tool_call":
                    message = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": scenario["tool"],
                                    "arguments": json.dumps(scenario["arguments"]),
                                },
                            }
                        ],
                    }
                    payload = {"choices": [{"message": message}], "usage": {}}
                elif scenario["kind"] == "malformed":
                    payload = {
                        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                            {"id": "x", "type": "function", "function": {"name": "list_files", "arguments": "{not json"}}
                        ]}}],
                        "usage": {},
                    }
                elif scenario["kind"] == "secret":
                    payload = {
                        "choices": [{"message": {"role": "assistant", "content": "key: sk-super-secret-canary-1234567890", "tool_calls": []}}],
                        "usage": {},
                    }
                elif scenario["kind"] == "error":
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b'{"error": "boom"}')
                    return
                else:
                    payload = {
                        "choices": [{"message": {"role": "assistant", "content": scenario.get("text", "done"), "tool_calls": []}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 3},
                    }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

        return Handler

    def __enter__(self) -> FakeModelServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._server.shutdown()
        self.thread.join(timeout=5)


@pytest.fixture
def fake_model() -> FakeModelServer:
    with FakeModelServer() as server:
        yield server


@pytest.fixture
def workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "campaign@treepact.local"],
        ["git", "config", "user.name", "Campaign"],
    ):
        subprocess.run(command, cwd=repo, check=True)
    return repo


def init_repo(repo: pathlib.Path, files: dict[str, str]) -> str:
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def cfg_factory(tmp_path: pathlib.Path):
    def build(**overrides: object) -> Config:
        data_dir = tmp_path / "data"
        provider = ProviderConfig(
            endpoint=overrides.pop("endpoint", None),
            profiles=overrides.pop("profiles", None),
        )
        resources = ResourceConfig()
        retention = RetentionConfig()
        return Config(
            data_dir=data_dir,
            config_path=None,
            log_level="info",
            provider=provider,
            resources=resources,
            retention=retention,
            sources=("test",),
        )

    return build
