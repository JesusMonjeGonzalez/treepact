"""Decision Bundle generation for a terminal run (STATE_AND_DATA_MODEL.md).

The bundle is the machine-readable manifest plus generated human report.
Generation persists `report.generated` and `artifact.created` events and is
atomic with the run's terminal transition. If bundle generation fails after
gates were calculated, the run ends as `infrastructure_error` and the CLI
returns 20 (handled at the command layer).
"""

from __future__ import annotations

import json
import sqlite3

from treepact import BUNDLE_SCHEMA_VERSION
from treepact.config import Config, run_bundle_dir
from treepact.evidence.artifacts import ArtifactStore
from treepact.evidence.events import EventStore
from treepact.evidence.projectors import project_json, project_markdown
from treepact.storage.repository import Repository


def generate_bundle(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict[str, str]:
    """Generate report.json and report.md for a terminal run inside the
    caller's transaction. Returns relative paths of generated files."""
    repo = Repository(conn)
    run = repo.run_by_id(run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    pact_record = repo.pact_by_id(run["pact_id"])
    if pact_record is not None:
        run = dict(run)
        run["pact_sha256"] = pact_record["sha256"]
        run["adapter_version"] = "0.0.0"
        run["runtime_version"] = None
        run["external_session_id"] = None
    task = repo.task_by_id(run["task_id"])
    project = repo.project_by_id(task["project_id"]) if task else None
    attempts = repo.attempts_for_run(run_id)
    checks = repo.checks_for_run(run_id)
    gates = repo.gates_for_run(run_id)
    workspace = repo.workspace_by_run(run_id)
    denials = _policy_denials(repo, run_id)

    store = EventStore(conn)
    artifacts = ArtifactStore(conn, cfg.data_dir / "artifacts").rows_for_run(run_id)
    event_head = store.head_digest(run_id)

    manifest = project_json(
        run=run,
        task=task,
        attempts=attempts,
        checks=checks,
        gates=gates,
        denials=denials,
        artifacts=artifacts,
        event_head=event_head,
        assurance_level=run.get("assurance_level") or "TP0",
        bundle_schema_version=BUNDLE_SCHEMA_VERSION,
        project=project,
        workspace=workspace,
    )

    bundle_dir = run_bundle_dir(cfg, run_id)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = bundle_dir / "report.json"
    report_md_path = bundle_dir / "report.md"
    report_json_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_md_path.write_text(project_markdown(manifest), encoding="utf-8")

    store.append(
        run_id=run_id,
        event_type="report.generated",
        actor="treepact",
        correlation_id=f"bundle-{run_id}",
        pact_sha256=_run_pact_sha(repo, run),
        payload={"report_kind": "bundle", "path_relative": f"runs/{run_id}/report.json"},
    )
    return {"report.json": str(report_json_path), "report.md": str(report_md_path)}


def _policy_denials(repo: Repository, run_id: str) -> list[dict[str, object]]:
    rows = repo._conn.execute(
        "SELECT p.tool_name, d.rule_id, d.reason_code FROM policy_decisions d "
        "JOIN tool_proposals p ON p.proposal_id = d.proposal_id "
        "WHERE p.run_id = ? AND d.outcome = 'deny' ORDER BY d.created_at ASC",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _run_pact_sha(repo: Repository, run: dict[str, object]) -> str:
    pact = repo.pact_by_id(str(run["pact_id"]))
    return pact["sha256"] if pact else ""
