"""TreePact CLI entrypoint (Typer). The CLI parses operator intent and calls
application commands; it never manipulates the database or worktree
directly."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import typer

from treepact import (
    BUNDLE_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    PACT_SCHEMA_VERSION,
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    __version__,
)
from treepact.cli import commands
from treepact.cli.guard import _fail, guarded
from treepact.config import Config, load_config
from treepact.errors import TreePactError

app = typer.Typer(
    name="treepact",
    no_args_is_help=True,
    add_completion=False,
    help="Local, verifiable change control for coding agents. Evidence before merge.",
    context_settings={"help_option_names": ["--help"]},
)

F = TypeVar("F", bound=Callable[..., object])


class AppContext:
    def __init__(self, config: Config, json_output: bool = False) -> None:
        self.config = config
        self.json_output = json_output


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None, "--config", help="Use an explicit user configuration file."
    ),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Use an explicit TreePact data root for development or recovery."
    ),
    log_level: str = typer.Option(
        "", "--log-level", help="error, warning, info or debug."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable output where supported."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI output."),
    version: bool = typer.Option(False, "--version", help="Print TreePact and schema versions."),
) -> None:
    if version:
        typer.echo(
            f"treepact {__version__} | pact schema {PACT_SCHEMA_VERSION}, "
            f"event schema {EVENT_SCHEMA_VERSION}, bundle schema {BUNDLE_SCHEMA_VERSION}, "
            f"runtime manifest {RUNTIME_MANIFEST_SCHEMA_VERSION}"
        )
        raise typer.Exit(0)
    if no_color:
        typer.echo("note: --no-color is accepted; output is plain text.", err=True)
    try:
        cfg = load_config(explicit_config=config, explicit_data_dir=data_dir)
    except TreePactError as exc:
        _fail(exc)
        return
    if log_level:
        cfg = cfg.replace(log_level=log_level)
    ctx.obj = AppContext(config=cfg, json_output=json_output)


def get_config(ctx: typer.Context) -> Config:
    app_ctx = ctx.find_root().obj
    if not isinstance(app_ctx, AppContext):
        raise TreePactError("missing application context", code="cli_context")
    return app_ctx.config


app.add_typer(commands.provider_app, name="provider")


@app.command("init")
@guarded
def cmd_init(
    ctx: typer.Context,
    repo: Path | None = typer.Option(None, "--repo", help="Repository path (default: current directory)."),
    project_id: str | None = typer.Option(None, "--project-id", help="Explicit project identifier."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing Pact draft."),
) -> None:
    commands.init_command(get_config(ctx), repo, project_id, force)


@app.command("validate")
@guarded
def cmd_validate(
    ctx: typer.Context,
    repo: Path | None = typer.Option(None, "--repo", help="Repository path (default: current directory)."),
    pact: Path | None = typer.Option(None, "--pact", help="Explicit Pact file path."),
    explain: bool = typer.Option(False, "--explain", help="Explain each validation finding."),
    print_canonical: bool = typer.Option(False, "--print-canonical", help="Print the canonical Pact and hash."),
) -> None:
    commands.validate_command(get_config(ctx), repo, pact, explain, print_canonical)


@app.command("doctor")
@guarded
def cmd_doctor(
    ctx: typer.Context,
    repo: Path | None = typer.Option(None, "--repo", help="Repository path (default: current directory)."),
    deep: bool = typer.Option(False, "--deep", help="Run safe loopback and filesystem diagnostics."),
) -> None:
    commands.doctor_command(get_config(ctx), repo, deep)


@app.command("run")
@guarded
def cmd_run(
    ctx: typer.Context,
    task: str = typer.Argument(..., help="Task text describing what should be achieved."),
    repo: Path | None = typer.Option(None, "--repo", help="Repository path (default: current directory)."),
    mode: str = typer.Option("observe", "--mode", help="observe or repair."),
    runtime: str = typer.Option("native", "--runtime", help="native, opencode or claude-code."),
    model_profile: str | None = typer.Option(None, "--model-profile", help="Requested model profile."),
    max_attempts: int | None = typer.Option(None, "--max-attempts", min=1, max=3, help="Lower the Pact attempt limit."),
    max_minutes: int | None = typer.Option(None, "--max-minutes", min=1, help="Lower the Pact time limit."),
    wait_for_resources: bool = typer.Option(False, "--wait-for-resources", help="Wait instead of failing on unavailable resources."),
    label: str | None = typer.Option(None, "--label", help="Optional run label."),
) -> None:
    commands.run_command(
        get_config(ctx), task, repo, mode, runtime, model_profile, max_attempts, max_minutes,
        wait_for_resources, label,
    )


@app.command("status")
@guarded
def cmd_status(
    ctx: typer.Context,
    run_id: str | None = typer.Argument(None, help="Run ID (default: latest active run)."),
    watch: bool = typer.Option(False, "--watch", help="Follow progress events."),
    interval: float = typer.Option(2.0, "--interval", help="Poll interval in seconds when watching."),
) -> None:
    commands.status_command(get_config(ctx), run_id, watch, interval)


@app.command("runs")
@guarded
def cmd_runs(
    ctx: typer.Context,
    repo: Path | None = typer.Option(None, "--repo", help="Filter by repository."),
    state: str | None = typer.Option(None, "--state", help="Filter by run state."),
    limit: int = typer.Option(20, "--limit", min=1, max=500, help="Maximum rows."),
    since: str | None = typer.Option(None, "--since", help="ISO timestamp lower bound."),
) -> None:
    commands.runs_command(get_config(ctx), repo, state, limit, since)


@app.command("projects")
@guarded
def cmd_projects(
    ctx: typer.Context,
    active_only: bool = typer.Option(False, "--active-only", help="Only projects with active runs."),
) -> None:
    commands.projects_command(get_config(ctx), active_only)


@app.command("logs")
@guarded
def cmd_logs(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run ID."),
    follow: bool = typer.Option(False, "--follow", help="Follow new output."),
    events: bool = typer.Option(False, "--events", help="Show the append-only event projection."),
    attempt: int | None = typer.Option(None, "--attempt", help="Filter by attempt number."),
    check: str | None = typer.Option(None, "--check", help="Filter by check ID."),
    since: str | None = typer.Option(None, "--since", help="ISO timestamp lower bound."),
) -> None:
    commands.logs_command(get_config(ctx), run_id, follow, events, attempt, check, since)


@app.command("diff")
@guarded
def cmd_diff(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run ID."),
    stat: bool = typer.Option(False, "--stat", help="Show diffstat only."),
    name_only: bool = typer.Option(False, "--name-only", help="Show changed file names only."),
    output: Path | None = typer.Option(None, "--output", help="Write a copy of the patch (records an export event)."),
) -> None:
    commands.diff_command(get_config(ctx), run_id, stat, name_only, output)


@app.command("evidence")
@guarded
def cmd_evidence(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run ID."),
    format: str = typer.Option("summary", "--format", help="summary, json or markdown."),
    verify_hashes: bool = typer.Option(False, "--verify-hashes", help="Recalculate artifact and event-chain hashes."),
    output: Path | None = typer.Option(None, "--output", help="Write the bundle to a path."),
) -> None:
    commands.evidence_command(get_config(ctx), run_id, format, verify_hashes, output)


@app.command("cancel")
@guarded
def cmd_cancel(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run ID."),
    reason: str | None = typer.Option(None, "--reason", help="Redacted operator reason."),
    wait: int | None = typer.Option(None, "--wait", help="Seconds to wait for graceful stop."),
) -> None:
    commands.cancel_command(get_config(ctx), run_id, reason, wait)


@app.command("resume")
@guarded
def cmd_resume(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run ID."),
    wait_for_resources: bool = typer.Option(False, "--wait-for-resources", help="Wait instead of failing on unavailable resources."),
) -> None:
    commands.resume_command(get_config(ctx), run_id, wait_for_resources)


@app.command("cleanup")
@guarded
def cmd_cleanup(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run ID."),
    force: bool = typer.Option(False, "--force", help="Remove an unreviewed worktree after explicit warning."),
) -> None:
    commands.cleanup_command(get_config(ctx), run_id, force)


@app.command("verify")
@guarded
def cmd_verify(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run ID."),
    check_artifacts: bool = typer.Option(False, "--check-artifacts", help="Verify artifact digests."),
    check_events: bool = typer.Option(False, "--check-events", help="Verify event-chain hashes."),
) -> None:
    commands.verify_command(get_config(ctx), run_id, check_artifacts, check_events)


@app.command("eval")
@guarded
def cmd_eval(
    ctx: typer.Context,
    suite_id: str = typer.Argument(..., help="Evaluation suite ID."),
    candidate: str | None = typer.Option(None, "--candidate", help="Frozen candidate digest."),
    output: Path | None = typer.Option(None, "--output", help="Result output path."),
) -> None:
    commands.eval_command(get_config(ctx), suite_id, candidate, output)


config_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Show effective non-secret configuration.",
)


@app.command("config")
@guarded
def cmd_config(ctx: typer.Context) -> None:
    """Alias root for `treepact config show`."""
    typer.echo("usage: treepact config show [--sources]", err=True)
    raise typer.Exit(2)


@config_app.command("show")
@guarded
def cmd_config_show(
    ctx: typer.Context,
    show_sources: bool = typer.Option(False, "--sources", help="Show configuration sources."),
) -> None:
    commands.config_show_command(get_config(ctx), show_sources)


app.add_typer(config_app, name="config")


def run_cli() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    app()
