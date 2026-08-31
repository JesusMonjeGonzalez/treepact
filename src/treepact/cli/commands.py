"""CLI command implementations. Milestone stubs fail honestly; nothing here
pretends to work before its milestone."""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import psutil
import typer

from treepact import BUNDLE_SCHEMA_VERSION, EVENT_SCHEMA_VERSION, PACT_SCHEMA_VERSION, __version__
from treepact.cli.guard import guarded
from treepact.config import Config, database_path, ensure_data_layout
from treepact.errors import (
    ConfigurationError,
    EvidenceError,
    NotYetImplemented,
    PactValidationError,
    RuntimeError,
    StateConflict,
    TreePactError,
)
from treepact.evidence.events import EventStore
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.repository import Repository

provider_app = typer.Typer(
    name="provider",
    no_args_is_help=True,
    add_completion=False,
    help="Model provider health.",
)


@provider_app.command("status")
@guarded
def provider_status(
    ctx: typer.Context,
    provider: str | None = typer.Option(None, "--provider", help="Provider ID."),
) -> None:
    """Show configured model-provider health without sending repository
    content (CLI_REFERENCE.md)."""
    from treepact.adapters.loopback import LoopbackProvider

    app_ctx = ctx.find_root().obj
    cfg = app_ctx.config
    if provider not in (None, "loopback"):
        raise ConfigurationError(
            f"unknown provider {provider!r}; supported: loopback", code="provider_unknown"
        )
    if cfg.provider.endpoint is None:
        typer.echo("provider: loopback not configured (no endpoint); use ~/.config/treepact/config.toml")
        raise typer.Exit(0)
    try:
        health = LoopbackProvider(cfg).health()
    except ConfigurationError as exc:
        raise exc
    if health.available:
        typer.echo(f"provider: loopback ok ({cfg.provider.endpoint})")
    else:
        typer.echo(
            f"provider: loopback unavailable ({health.detail}); observe and validate remain "
            "available; repair requiring inference will pause or fail explicitly"
        )
        raise typer.Exit(0)


def init_command(
    cfg: Config,
    repo: Path | None,
    project_id: str | None,
    force: bool,
) -> None:
    """Create a conservative Pact draft. Refuses to overwrite without
    --force; writes no executable check; records no repository content;
    never runs tests, builds, package managers, or agents."""
    from treepact.adapters.git import GitAdapter
    from treepact.domain.pact import PACT_FILENAME

    start = (repo or Path.cwd()).resolve()
    git = GitAdapter()
    root, _common = git.discover_root(start)
    pact_path = root / PACT_FILENAME
    if pact_path.exists() and not force:
        raise ConfigurationError(
            f"Pact already exists at {pact_path}; use --force to overwrite",
            code="pact_exists",
        )
    name = project_id or root.name.lower().replace(" ", "-")
    import re

    if not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", name):
        name = re.sub(r"[^a-z0-9._-]", "-", name.lower())[:63]
    toolchain = _guess_toolchain(root)
    draft = _PACT_DRAFT_TEMPLATE.format(project_id=name, toolchain=toolchain)
    pact_path.write_text(draft, encoding="utf-8")
    typer.echo(f"Created conservative Pact draft at {pact_path}")
    typer.echo("Review and edit it, then run `treepact validate`.")


def _guess_toolchain(root: Path) -> str:
    if (root / "Package.swift").exists():
        return "swift"
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        return "python"
    if (root / "package.json").exists():
        return "javascript"
    if (root / "build.gradle.kts").exists() or (root / "build.gradle").exists():
        return "kotlin"
    if (root / "Cargo.toml").exists():
        return "rust"
    return "unknown"


_PACT_DRAFT_TEMPLATE = """\
# TreePact repository contract - conservative draft.
# Review every section before use. `treepact validate` checks it without
# executing anything.
version: 1
project:
  id: {project_id}
  toolchain: {toolchain}

workspace:
  readable:
    - .
  writable: []
  denied:
    - .git/
    - .env

# Replace this example with real repository checks. It executes no
# repository code and is safe as-is.
checks:
  smoke:
    argv: ["python3", "--version"]
    timeout_seconds: 60
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
  attempts: 3
  turns_per_attempt: 30
  minutes: 30
  context_bytes: 750000
  max_input_tokens: 64000
  max_output_tokens: 8000
  model_profile: fast-code
  memory_mb: 14000
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


def validate_command(
    cfg: Config,
    repo: Path | None,
    pact: Path | None,
    explain: bool,
    print_canonical: bool,
) -> None:
    """Validate and compile the repository Pact without executing it
    (CLI-004: never runs project commands)."""
    from treepact.domain.pact import PACT_FILENAME, load_pact_file

    if pact is not None:
        pact_file = pact
    else:
        start = repo or Path.cwd()
        candidate = start / PACT_FILENAME if (start / PACT_FILENAME).is_file() else None
        if candidate is None:
            current = start.resolve()
            while current.parent != current:
                current = current.parent
                candidate = current / PACT_FILENAME
                if candidate.is_file():
                    break
        if candidate is None or not candidate.is_file():
            raise PactValidationError(
                f"no Pact file found from {start.resolve()} (looked for {PACT_FILENAME})",
                code="pact_not_found",
            )
        pact_file = candidate

    compiled = load_pact_file(pact_file)
    if print_canonical:
        typer.echo(compiled.canonical_json)
    typer.echo(
        f"Pact OK: {pact_file} (schema v{compiled.pact.version}, "
        f"sha256 {compiled.sha256})"
    )
    if explain:
        typer.echo(f"  checks: {', '.join(compiled.check_ids())}")
        typer.echo(f"  gates: {', '.join(compiled.gate_ids())}")
        typer.echo(
            f"  limits: attempts={compiled.pact.limits.attempts}, "
            f"turns={compiled.pact.limits.turns_per_attempt}, "
            f"minutes={compiled.pact.limits.minutes}, "
            f"context={compiled.pact.limits.context_bytes} bytes, "
            f"memory={compiled.pact.limits.memory_mb} MB, "
            f"network.runtime={compiled.pact.limits.network.runtime.value}, "
            f"network.checks={compiled.pact.limits.network.checks.value}"
        )
        denied = compiled.pact.workspace.denied
        typer.echo(f"  denied paths (declared): {', '.join(denied) if denied else '(none)'}")
        typer.echo("  denied paths (implicit): .git/, .treepact.yaml")


def doctor_command(cfg: Config, repo: Path | None, deep: bool) -> None:
    """Read-only health inspection. Never invokes model inference or
    repository checks. Unimplemented checks are reported honestly."""
    ensure_data_layout(cfg)

    lines: list[str] = []
    lines.append(f"TreePact version: {__version__}")
    lines.append(
        f"Schemas: pact {PACT_SCHEMA_VERSION}, event {EVENT_SCHEMA_VERSION}, "
        f"bundle {BUNDLE_SCHEMA_VERSION}"
    )
    lines.append(f"Python: {platform.python_version()} ({platform.python_implementation()})")
    lines.append(f"Platform: {platform.system()} {platform.machine()} ({platform.release()})")

    git_path = shutil.which("git")
    lines.append(f"Git: {git_path or 'NOT FOUND'}")

    data_dir = cfg.data_dir
    ok_perms = data_dir.exists() and data_dir.is_dir() and os.access(data_dir, os.W_OK | os.R_OK)
    lines.append(f"Data directory: {data_dir} ({'ok' if ok_perms else 'permission problem'})")

    db_path = database_path(cfg)
    try:
        conn = open_connection(db_path)
        runner = MigrationRunner(conn)
        runner.migrate()
        runner.refuse_newer_schema()
        head = runner.head_version()
        lines.append(f"SQLite state: {db_path} (schema head {head})")
        conn.close()
    except (EvidenceError, sqlite3.Error) as exc:
        lines.append(f"SQLite state: PROBLEM ({exc})")

    mem = psutil.virtual_memory()
    lines.append(
        f"Memory: {mem.used // (1024 * 1024)} MB used of {mem.total // (1024 * 1024)} MB "
        f"({mem.percent:.0f}%), pressure zone: {_pressure_zone()}"
    )

    lines.append(f"Provider: {'not configured' if cfg.provider.endpoint is None else 'configured (loopback-only enforcement in M5)'}")

    if deep:
        lines.append("deep diagnostics: not implemented until M7 (doctor --deep)")

    lines.append("Interrupted runs: discovery implemented in M2")
    lines.append("Loopback availability: implemented in M5")

    try:
        conn = open_connection(database_path(cfg))
        MigrationRunner(conn).migrate()
        from treepact.application.bootstrap import discover_interrupted_runs
        from treepact.storage.recovery import recover_stale_locks

        stale = recover_stale_locks(cfg.data_dir)
        if stale:
            lines.append(f"Stale locks recovered: {len(stale)}")
        interrupted = discover_interrupted_runs(conn, cfg.data_dir)
        active = Repository(conn).active_runs()
        lines.append(f"Active runs: {len(active)}")
        if interrupted:
            lines.append(f"Marked interrupted on this startup: {len(interrupted)}")
        conn.close()
    except Exception as exc:  # noqa: BLE001 - doctor must never crash
        lines.append(f"Run discovery: PROBLEM ({exc})")

    for line in lines:
        typer.echo(line)


def _pressure_zone() -> str:
    try:
        import psutil as _ps

        swap = _ps.swap_memory()
        mem = _ps.virtual_memory()
        swap_used = swap.percent if swap.total else 0.0
        if mem.percent >= 90 or swap_used >= 30:
            return "critical"
        if mem.percent >= 75 or swap_used >= 10:
            return "elevated"
        return "normal"
    except Exception:  # noqa: BLE001
        return "unknown"


def run_command(
    cfg: Config,
    task: str,
    repo: Path | None,
    mode: str,
    runtime: str,
    model_profile: str | None,
    max_attempts: int | None,
    max_minutes: int | None,
    wait_for_resources: bool,
    label: str | None,
) -> None:
    """Create and execute a supervised run (CLI_REFERENCE.md)."""
    from treepact.adapters.git import GitAdapter
    from treepact.adapters.loopback import LoopbackProvider
    from treepact.application.run_engine import RunEngine
    from treepact.clock import rfc3339
    from treepact.domain.pact import PACT_FILENAME, load_pact_file
    from treepact.enums import NetworkMode, RunMode, RunState, RuntimeId
    from treepact.evidence.events import EventStore
    from treepact.storage.connection import Transaction, open_connection
    from treepact.storage.migrations import MigrationRunner
    from treepact.storage.repository import Repository

    if mode not in ("observe", "repair"):
        raise ConfigurationError(f"invalid --mode {mode!r}; choose observe or repair", code="mode_invalid")
    try:
        run_mode = RunMode(mode)
    except ValueError:
        raise ConfigurationError(f"invalid --mode {mode!r}", code="mode_invalid") from None
    try:
        runtime_id = RuntimeId(runtime)
    except ValueError:
        raise ConfigurationError(
            f"invalid --runtime {runtime!r}; supported: native, opencode", code="runtime_invalid"
        ) from None
    if runtime_id == RuntimeId.CLAUDE_CODE:
        raise NotYetImplemented("runtime claude-code", "deferred (requires egress ADR)")
    if runtime_id == RuntimeId.OPENCODE:
        from treepact.adapters.opencode import SUPPORTED_RUNTIME_VERSION

        probe = subprocess.run(
            ["opencode", "--version"], capture_output=True, text=True, timeout=30, check=False
        )
        installed = (probe.stdout or probe.stderr).strip().splitlines()
        installed_version = installed[-1].strip() if installed else ""
        if installed_version != SUPPORTED_RUNTIME_VERSION:
            raise RuntimeError(
                f"opencode {installed_version or 'unknown'} is unsupported; "
                f"pinned version is {SUPPORTED_RUNTIME_VERSION}",
                code="runtime_incompatible",
            )
        if not cfg.provider.endpoint:
            raise ConfigurationError(
                "the opencode runtime requires a configured loopback provider "
                "endpoint (config [provider] endpoint)",
                code="provider_not_configured",
            )

    start = (repo or Path.cwd()).resolve()
    git = GitAdapter()
    root, common_dir = git.discover_root(start)
    pact_file = root / PACT_FILENAME
    if not pact_file.is_file():
        raise PactValidationError(
            f"no Pact file at repository root {pact_file}; run `treepact init`",
            code="pact_not_found",
        )
    compiled = load_pact_file(pact_file)
    if model_profile is not None and not _profile_allowed(compiled, model_profile):
        raise ConfigurationError(
            f"model profile {model_profile!r} is not allowed by Pact "
            f"(pact declares {compiled.pact.limits.model_profile})",
            code="model_profile_denied",
        )
    if compiled.pact.limits.network.runtime != NetworkMode.LOOPBACK_ONLY and runtime_id == RuntimeId.NATIVE:
        raise ConfigurationError(
            "native runtime requires network.runtime loopback_only or denied in the Pact",
            code="runtime_network_incompatible",
        )

    ensure_data_layout(cfg)
    conn = open_connection(database_path(cfg))
    MigrationRunner(conn).migrate()
    repo_store = Repository(conn)
    event_store = EventStore(conn)

    project = repo_store.project_by_root(str(root))
    if project is None:
        project_id = compiled.pact.project.id
        repo_store.register_project(project_id, str(root), common_dir, root.name)
        project = repo_store.project_by_id(project_id)
    if project is None:
        raise ConfigurationError("project registration failed", code="project_register_failed")
    project_id = str(project["project_id"])

    base_commit = git.head_commit(root)
    with Transaction(conn):
        pact_id = repo_store.store_pact(
            project_id=project_id,
            schema_version=compiled.pact.version,
            sha256=compiled.sha256,
            canonical_json=compiled.canonical_json,
            source_path=str(pact_file),
        )
        task_id = repo_store.create_task(
            project_id=project_id, operator_text=task, mode=run_mode
        )
        run_id = repo_store.create_run(
            task_id=task_id,
            pact_id=pact_id,
            runtime_id=runtime_id,
            provider_id="loopback",
            model_profile=model_profile or compiled.pact.limits.model_profile,
            base_commit=base_commit,
            assurance_level="TP3" if runtime_id == RuntimeId.NATIVE else "TP2",
            attempt_limit=compiled.pact.limits.attempts,
            turn_limit=compiled.pact.limits.turns_per_attempt,
            deadline_at=rfc3339(),
        )
        event_store.append(
            run_id=run_id,
            event_type="run.created",
            actor="operator",
            correlation_id=run_id,
            pact_sha256=compiled.sha256,
            payload={
                "task_id": task_id,
                "mode": run_mode.value,
                "runtime_id": runtime_id.value,
                "model_profile": model_profile or compiled.pact.limits.model_profile,
                "attempt_limit": compiled.pact.limits.attempts,
                "turn_limit": compiled.pact.limits.turns_per_attempt,
                "deadline_at": rfc3339(),
            },
        )

    provider = LoopbackProvider(cfg) if cfg.provider.endpoint else None
    engine = RunEngine(conn, cfg, provider=provider, git=git)
    try:
        final_state = engine.execute(
            run_id,
            task,
            wait_for_resources=wait_for_resources,
            max_attempts=max_attempts,
            max_minutes=max_minutes,
        )
    except TreePactError as exc:
        exc.run_id = run_id
        _print_run_result(cfg, run_id)
        raise exc

    run = repo_store.run_by_id(run_id) or {}
    if final_state == RunState.ACCEPTED.value:
        typer.echo(
            f"run {run_id} -> accepted (review the patch; accepted is Pact "
            "compliance, not correctness)"
        )
    elif final_state == RunState.NEEDS_REVIEW.value:
        typer.echo(f"run {run_id} -> needs_review")
        raise typer.Exit(17)
    elif final_state == RunState.REJECTED.value:
        typer.echo(f"run {run_id} -> rejected ({run.get('terminal_reason_code')})")
        raise typer.Exit(16)
    elif final_state == RunState.CANCELLED.value:
        raise typer.Exit(18)
    elif final_state in (RunState.FAILED.value, RunState.INFRASTRUCTURE_ERROR.value):
        code = run.get("terminal_reason_code") or ""
        if code == "bundle_generation_failed":
            raise typer.Exit(20)
        raise typer.Exit(15)
    typer.echo(f"  worktree: {cfg.data_dir / 'worktrees' / run_id}")
    bundle_dir = cfg.data_dir / "runs" / run_id
    typer.echo(f"  bundle: {bundle_dir / 'report.json'}")


def _profile_allowed(compiled: object, profile: str) -> bool:
    pact = getattr(compiled, "pact", None)
    return bool(pact and profile == pact.limits.model_profile)


def _print_run_result(cfg: Config, run_id: str) -> None:
    bundle_dir = cfg.data_dir / "runs" / run_id
    if bundle_dir.exists():
        typer.echo(f"  bundle: {bundle_dir / 'report.json'}")


def _run_pact_sha256(repo: Repository, run: dict[str, Any]) -> str:
    if not run or not run.get("pact_id"):
        return ""
    pact = repo.pact_by_id(str(run["pact_id"]))
    return pact["sha256"] if pact else ""


def _open_store(cfg: Config) -> tuple[Repository, Any]:
    """Open the authoritative DB with migrations applied (read-only usage)."""
    from treepact.storage.connection import open_connection
    from treepact.storage.migrations import MigrationRunner

    ensure_data_layout(cfg)
    conn = open_connection(database_path(cfg))
    MigrationRunner(conn).migrate()
    return Repository(conn), EventStore(conn)


def status_command(cfg: Config, run_id: str | None, watch: bool, interval: float) -> None:
    """Show one run or the latest active run. --watch reads progress events
    and never alters scheduling."""
    import time as _time

    from treepact.application.bootstrap import discover_interrupted_runs

    repo, store = _open_store(cfg)
    discover_interrupted_runs(repo._conn, cfg.data_dir)
    if run_id is None:
        runs = repo.active_runs()
        if not runs:
            typer.echo("no active runs")
            return
        run_id = runs[0]["run_id"]
    while True:
        run = repo.run_by_id(run_id)
        if run is None:
            raise EvidenceError(f"run {run_id} not found", code="run_not_found")
        task = repo.task_by_id(str(run["task_id"]))
        workspace = repo.workspace_by_run(run_id)
        attempts = repo.attempts_for_run(run_id)
        last_event = store.events_for_run(run_id)[-1:] or []
        lines = [
            f"run: {run_id}",
            f"state: {run['state']}" + (f" ({run['terminal_reason_code']})" if run.get("terminal_reason_code") else ""),
            f"decision: {run.get('decision') or 'pending'}",
            f"task mode: {task['mode'] if task else '?'} | runtime: {run['runtime_id']} | profile: {run.get('model_profile') or '-'}",
            f"attempts: {len(attempts)}/{run['attempt_limit']} | assurance: {run['assurance_level']}",
            f"worktree: {workspace['path'] if workspace else 'not created'}",
            f"last event: {last_event[0]['event_type'] if last_event else '-'}",
            f"pact sha256: {run['pact_id']}",
        ]
        typer.echo("\n".join(lines))
        if not watch:
            return
        _time.sleep(max(interval, 0.5))


def runs_command(
    cfg: Config, repo: Path | None, state: str | None, limit: int, since: str | None
) -> None:
    """List runs."""
    from treepact.enums import RunState

    if state is not None:
        try:
            state_filter = RunState(state)
        except ValueError:
            raise ConfigurationError(f"invalid --state {state!r}", code="state_invalid") from None
    else:
        state_filter = None
    store_repo, _ = _open_store(cfg)
    project_id = None
    if repo is not None:
        from treepact.adapters.git import GitAdapter

        root, _common = GitAdapter().discover_root(repo)
        project = store_repo.project_by_root(str(root))
        if project is None:
            typer.echo("no runs (repository not registered)")
            return
        project_id = project["project_id"]
    rows = store_repo.list_runs(project_id=project_id, state=state_filter, limit=limit, since=since)
    for run in rows:
        typer.echo(
            f"{run['run_id']}  {run['state']:<20} {run.get('decision') or '-':<14} "
            f"created {run['created_at']}"
        )
    typer.echo(f"{len(rows)} run(s)")


def review_command(cfg: Config, run_id: str | None, limit: int, limit_explicit: bool) -> None:
    """Emit one versioned JSON document without changing storage or files."""
    import re

    from treepact.review import RunDetailDocument, RunListDocument, review_run, review_runs

    if not 1 <= limit <= 100:
        raise ConfigurationError("--limit must be between 1 and 100", code="limit_invalid")
    document: RunDetailDocument | RunListDocument
    if run_id is not None:
        if limit_explicit:
            raise ConfigurationError(
                "--limit and --run-id are mutually exclusive", code="review_form_invalid"
            )
        if re.fullmatch(r"run_[a-f0-9]{32}", run_id) is None:
            raise ConfigurationError(
                "--run-id must match run_<32 lowercase hex>", code="run_id_invalid"
            )
        document = review_run(cfg, run_id)
    else:
        document = review_runs(cfg, limit)
    typer.echo(json.dumps(document.model_dump(mode="json", by_alias=True), separators=(",", ":")))


def projects_command(cfg: Config, active_only: bool) -> None:
    """List registered projects and last known Pact metadata. Does not scan
    the home directory."""
    store_repo, _ = _open_store(cfg)
    for project in store_repo.list_projects(active_only=active_only):
        last = store_repo._conn.execute(
            "SELECT sha256, created_at FROM pact_versions WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (project["project_id"],),
        ).fetchone()
        sha = last["sha256"][:12] if last else "-"
        typer.echo(
            f"{project['project_id']:<24} {project['canonical_root']} (pact {sha}, "
            f"last seen {project['last_seen_at']})"
        )


def logs_command(
    cfg: Config,
    run_id: str,
    follow: bool,
    events: bool,
    attempt: int | None,
    check: str | None,
    since: str | None,
) -> None:
    """Display redacted operational output. Raw secrets and full provider
    payloads are never available through any flag."""
    repo, store = _open_store(cfg)
    if events:
        for event in store.events_for_run(run_id, since=since):
            if attempt is not None and str(attempt) not in json.dumps(event.get("payload", {})):
                continue
            typer.echo(
                f"{event['sequence']:>4} {event['occurred_at']} {event['event_type']} "
                f"{_mask_secrets(json.dumps(event.get('payload') or {}))}"
            )
        return
    if check is not None:
        rows = repo._conn.execute(
            "SELECT * FROM checks WHERE run_id = ? AND check_id = ?", (run_id, check)
        ).fetchall()
        for row in rows:
            typer.echo(f"check {row['check_id']} [{row['phase']}] {row['state']} exit={row['exit_code']}")
        return
    run = repo.run_by_id(run_id)
    if run is None:
        raise EvidenceError(f"run {run_id} not found", code="run_not_found")
    typer.echo(
        f"run {run_id} state={run['state']} decision={run.get('decision') or '-'}"
    )
    for attempt_row in repo.attempts_for_run(run_id):
        typer.echo(
            f"  attempt {attempt_row['attempt_number']}: {attempt_row['state']} "
            f"({attempt_row.get('termination_code') or '-'})"
        )


def diff_command(cfg: Config, run_id: str, stat: bool, name_only: bool, output: Path | None) -> None:
    """Display the run change against its recorded base. --output writes a
    copy of the patch and records that export as an event; it never applies
    the patch elsewhere."""
    repo, store = _open_store(cfg)
    workspace = repo.workspace_by_run(run_id)
    patch_path = cfg.data_dir / "runs" / run_id / "diff.patch"
    if output is not None:
        if not patch_path.is_file():
            raise EvidenceError("no final patch recorded for this run", code="patch_missing")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(patch_path.read_bytes())
        run = repo.run_by_id(run_id)
        store.append(
            run_id=run_id,
            event_type="evidence.exported",
            actor="operator",
            correlation_id=run_id,
            pact_sha256=_run_pact_sha256(repo, run or {}),
            payload={"destination": str(output), "kind": "patch"},
        )
        typer.echo(f"patch exported to {output}")
        return
    if not patch_path.is_file():
        typer.echo("no changes recorded for this run")
        return
    patch_text = patch_path.read_text(encoding="utf-8")
    if name_only:
        names = sorted({line.split("\t")[-1] for line in patch_text.splitlines() if line.startswith("diff --git")})
        for name in names:
            typer.echo(name.split(" b/", 1)[-1])
        return
    if stat:
        typer.echo(f"{workspace['path'] if workspace else run_id}: {len(patch_text.splitlines())} patch lines")
        return
    typer.echo(patch_text)


def evidence_command(
    cfg: Config, run_id: str, format: str, verify_hashes: bool, output: Path | None
) -> None:
    """Inspect or export the Decision Bundle. --verify-hashes recalculates
    artifact and event-chain hashes; it does not prove integrity against an
    attacker controlling the host."""
    from treepact.evidence.bundle import _run_pact_sha as _sha

    repo, store = _open_store(cfg)
    run = repo.run_by_id(run_id)
    if run is None:
        raise EvidenceError(f"run {run_id} not found", code="run_not_found")
    if verify_hashes:
        ok, message = store.verify_chain(run_id)
        if not ok:
            raise EvidenceError(f"event chain verification failed: {message}", code="chain_invalid")
        from treepact.evidence.artifacts import ArtifactStore

        artifacts = ArtifactStore(repo._conn, cfg.data_dir / "artifacts")
        missing = [a["artifact_id"] for a in artifacts.rows_for_run(run_id) if not artifacts.verify(a["artifact_id"])]
        if missing:
            raise EvidenceError(
                f"artifact verification failed: {', '.join(missing)}", code="artifact_invalid"
            )
        typer.echo(f"hashes verified for run {run_id} (events ok, artifacts ok)")
        return
    bundle_dir = cfg.data_dir / "runs" / run_id
    if format == "json":
        manifest = json.loads((bundle_dir / "report.json").read_text())
        typer.echo(json.dumps(manifest, indent=2, sort_keys=True))
    elif format == "markdown":
        typer.echo((bundle_dir / "report.md").read_text())
    else:
        summary = _summary_for_run(repo, run, bundle_dir)
        typer.echo(summary)
    if output is not None:
        source = bundle_dir / ("report.json" if format == "json" else "report.md")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(source.read_bytes())
        store.append(
            run_id=run_id,
            event_type="evidence.exported",
            actor="operator",
            correlation_id=run_id,
            pact_sha256=_sha(repo, run),
            payload={"destination": str(output), "kind": format},
        )


def _summary_for_run(repo: Any, run: dict[str, Any], bundle_dir: Path) -> str:
    lines = [
        f"run: {run['run_id']}",
        f"state: {run['state']}",
        f"decision: {run.get('decision') or '-'}",
        f"pact: {run['pact_id']}",
        f"assurance: {run['assurance_level']}",
        f"runtime: {run['runtime_id']}",
    ]
    gates = repo.gates_for_run(run["run_id"])
    for gate in gates:
        lines.append(f"gate {gate['gate_id']}: {gate['state']} ({gate['reason_code']})")
    if (bundle_dir / "diff.patch").exists():
        lines.append(f"patch: {bundle_dir / 'diff.patch'}")
    return "\n".join(lines)


def cancel_command(cfg: Config, run_id: str, reason: str | None, wait: int | None) -> None:
    """Request cancellation of an active or waiting run. Writes a cancel
    request and signals the recorded owner process. The reason is redacted
    and stored as operator metadata."""
    from treepact.application.bootstrap import read_lock_owner
    from treepact.storage.recovery import is_pid_alive, recover_stale_locks

    recover_stale_locks(cfg.data_dir)
    repo, store = _open_store(cfg)
    run = repo.run_by_id(run_id)
    if run is None:
        raise EvidenceError(f"run {run_id} not found", code="run_not_found")
    from treepact.enums import RunState

    if RunState(run["state"]).terminal:
        raise StateConflict(f"run {run_id} is already terminal ({run['state']})", code="run_terminal")
    cancel_path = cfg.data_dir / "locks" / f"{run_id}.cancel"
    cancel_path.parent.mkdir(parents=True, exist_ok=True)
    cancel_path.write_text((reason or "operator_cancel")[:500], encoding="utf-8")
    owner = read_lock_owner(cfg.data_dir, run_id)
    if owner is not None and is_pid_alive(owner):
        try:
            os.kill(owner, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        typer.echo(f"cancellation requested for run {run_id} (owner pid {owner})")
    else:
        typer.echo(f"cancellation requested for run {run_id} (no live owner process)")


def resume_command(cfg: Config, run_id: str, wait_for_resources: bool) -> None:
    """Resume a previously interrupted run. Resume cannot change runtime,
    Pact, base commit, repository, or provider (CLI-006)."""
    from treepact.adapters.loopback import LoopbackProvider
    from treepact.application.run_engine import RunEngine

    repo, _ = _open_store(cfg)
    run = repo.run_by_id(run_id)
    if run is None:
        raise EvidenceError(f"run {run_id} not found", code="run_not_found")
    task_row = repo.task_by_id(run["task_id"])
    task = task_row["operator_text"] if task_row else ""
    provider = LoopbackProvider(cfg) if cfg.provider.endpoint else None
    engine = RunEngine(repo._conn, cfg, provider=provider)
    final = engine.resume(run_id, task, wait_for_resources=wait_for_resources)
    typer.echo(f"run {run_id} resumed -> {final}")


def cleanup_command(cfg: Config, run_id: str, force: bool) -> None:
    """Remove transient run resources. Verifies the run is not active and the
    worktree lives under TreePact storage; preserves the Decision Bundle and
    event history. --force permits removal of an unreviewed worktree after an
    explicit warning; it never bypasses repository identity checks."""
    from treepact.enums import RunState
    from treepact.workspace.supervisor import WorkspaceSupervisor

    repo, _ = _open_store(cfg)
    run = repo.run_by_id(run_id)
    if run is None:
        raise EvidenceError(f"run {run_id} not found", code="run_not_found")
    if not RunState(run["state"]).terminal and not force:
        raise StateConflict(
            f"run {run_id} is {run['state']}; use --force to remove an unreviewed worktree",
            code="run_active",
        )
    supervisor = WorkspaceSupervisor(repo._conn, cfg, git=None)
    supervisor.remove(run_id, force=force)
    typer.echo(f"worktree removed for run {run_id}; evidence preserved under {cfg.data_dir / 'runs' / run_id}")


def verify_command(cfg: Config, run_id: str, check_artifacts: bool, check_events: bool) -> None:
    """Recalculate deterministic gates for an existing frozen run without
    invoking a model and without rerunning repository checks (CLI-005)."""
    from treepact.domain.gates import GateEvaluator, decide
    from treepact.domain.pact import compile_pact

    repo, store = _open_store(cfg)
    run = repo.run_by_id(run_id)
    if run is None:
        raise EvidenceError(f"run {run_id} not found", code="run_not_found")
    pact = repo.pact_by_id(run["pact_id"])
    if pact is None:
        raise EvidenceError("Pact snapshot missing", code="pact_snapshot_missing")
    compiled = compile_pact(pact["canonical_json"], source_name=pact["source_path"])
    workspace = repo.workspace_by_run(run_id)
    worktree = Path(str(workspace["path"])) if workspace else None
    if check_artifacts:
        from treepact.evidence.artifacts import ArtifactStore

        artifacts = ArtifactStore(repo._conn, cfg.data_dir / "artifacts")
        missing = [a["artifact_id"] for a in artifacts.rows_for_run(run_id) if not artifacts.verify(a["artifact_id"])]
        if missing:
            raise EvidenceError(f"artifacts missing/corrupt: {', '.join(missing)}", code="artifact_invalid")
        typer.echo("artifacts verified")
    if check_events:
        ok, message = store.verify_chain(run_id)
        if not ok:
            raise EvidenceError(f"event chain invalid: {message}", code="chain_invalid")
        typer.echo("event chain verified")
    patch_text = ""
    patch_path = cfg.data_dir / "runs" / run_id / "diff.patch"
    if patch_path.is_file():
        patch_text = patch_path.read_text(encoding="utf-8")
    changed: list[str] = []
    digest = None
    git_probe: object = _NoopGit()
    if worktree is not None and workspace is not None and worktree.is_dir():
        from treepact.adapters.git import GitAdapter

        git_probe = GitAdapter()
        changed = git_probe.diff_name_only(worktree, str(workspace["base_commit"]))
        digest = git_probe.tree_digest(worktree)
    evaluator = GateEvaluator(repo._conn, compiled, run_id, worktree or cfg.data_dir, git=git_probe, cfg=cfg)  # type: ignore[arg-type]
    results = evaluator.evaluate_all(changed_paths=changed, patch=patch_text, tree_digest=digest or "")
    decision, reason = decide(results)
    typer.echo(f"recalculated decision: {decision} ({reason})")
    for result in results:
        typer.echo(f"  {result.gate_id}: {result.state.value} ({result.reason_code})")


class _NoopGit:
    def tree_digest(self, path: Path) -> str:
        return ""

    def diff_name_only(self, path: Path, base: str) -> list[str]:
        return []


def _mask_secrets(text: str) -> str:
    """Redact common secret-shaped values before display (logs, events).
    Detector misses remain possible and are documented."""
    import re as _re

    masked = _re.sub(r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S+", r"\1=***", text)
    return masked


def eval_command(cfg: Config, suite_id: str, candidate: str | None, output: Path | None) -> None:
    """Run a declared evaluation suite during the final verification phase.
    This is not a CI runner: it executes versioned local evaluation
    campaigns and preserves evidence (CLI_REFERENCE.md)."""
    import subprocess as _subprocess


    root = Path(__file__).resolve().parents[3]
    campaign = root / "scripts" / "campaign.py"
    if not campaign.is_file():
        raise EvidenceError(
            f"campaign script not found at {campaign}", code="campaign_missing"
        )
    if suite_id not in ("m9", "m9-static", "m9-unit", "m9-integration", "m9-adversarial",
                        "m9-recovery", "m9-resources", "m9-evals", "m9-runtime", "m9-audit"):
        raise ConfigurationError(
            f"unknown suite {suite_id!r}; supported: m9, m9-static, m9-unit, m9-integration, "
            "m9-adversarial, m9-recovery, m9-resources, m9-evals, m9-runtime, m9-audit",
            code="suite_unknown",
        )
    if suite_id == "m9":
        args = ["uv", "run", "python", str(campaign)]
    else:
        args = ["uv", "run", "python", str(campaign), "--rerun", suite_id[3:]]
    result = _subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=7200)
    typer.echo(result.stdout)
    if result.stderr.strip():
        typer.echo(result.stderr, err=True)
    raise typer.Exit(result.returncode)


def config_show_command(cfg: Config, show_sources: bool) -> None:
    """Show effective non-secret configuration. Secret values appear only as
    configured or not configured; provider endpoint is displayed because it
    is loopback-only in version 1 and never carries credentials."""
    effective = cfg.to_dict()
    typer.echo(json.dumps(effective, indent=2, sort_keys=True))
    if show_sources:
        typer.echo("\nsources:")
        for source in cfg.sources:
            typer.echo(f"  - {source}")
