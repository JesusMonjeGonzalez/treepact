"""M10 pilot metrics from the authoritative TreePact data root.

Computes the automated M10 targets and audits evidence for every terminal
run. Manual metrics (supervision time, false acceptances, incidents) are
read from verification/pilot/manual_metrics.json and marked as pending when
absent.

Usage:
  uv run python scripts/pilot_metrics.py [--data-dir DIR] [--final] [--output PATH]

Exit code 0 always (report-only); use --final to emit the closing scorecard.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any

from treepact.config import Config, ProviderConfig, ResourceConfig, RetentionConfig
from treepact.evidence.artifacts import ArtifactStore
from treepact.evidence.events import EventStore
from treepact.storage.connection import open_connection
from treepact.storage.migrations import MigrationRunner
from treepact.storage.repository import Repository

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANUAL_PATH = ROOT / "verification" / "pilot" / "manual_metrics.json"

TARGETS = {
    "total_runs": {"min": 30},
    "repos_used_repeatedly": {"min": 3},
    "supervision_reduction_median_pct": {"min": 30},
    "critical_incidents": {"max": 0},
    "false_acceptance_rate_pct": {"max": 5},
    "blocked_prohibited_actions": {"min": 5},
}


def load_manual() -> dict[str, Any]:
    if not MANUAL_PATH.exists():
        return {}
    return json.loads(MANUAL_PATH.read_text())


def compute(data_dir: pathlib.Path) -> dict[str, Any]:
    cfg = Config(data_dir=data_dir, config_path=None, log_level="info",
                 provider=ProviderConfig(), resources=ResourceConfig(),
                 retention=RetentionConfig(), sources=("metrics",))
    conn = open_connection(cfg.data_dir / "db" / "treepact.sqlite")
    MigrationRunner(conn).migrate()
    repo = Repository(conn)
    store = EventStore(conn)
    artifacts = ArtifactStore(conn, cfg.data_dir / "artifacts")

    runs = repo._conn.execute(
        "SELECT r.*, t.project_id, t.operator_text, t.mode "
        "FROM runs r JOIN tasks t ON t.task_id = r.task_id ORDER BY r.created_at"
    ).fetchall()

    per_project: dict[str, int] = {}
    per_state: dict[str, int] = {}
    per_decision: dict[str, int] = {}
    evidence_ok = 0
    evidence_bad: list[str] = []
    artifact_bad: list[str] = []
    denied_events = 0
    denied_by_rule: dict[str, int] = {}
    checks_run = 0
    attempts_total = 0

    for run in runs:
        run_id = run["run_id"]
        project_id = run["project_id"] or "?"
        per_project[project_id] = per_project.get(project_id, 0) + 1
        per_state[run["state"]] = per_state.get(run["state"], 0) + 1
        decision = run["decision"] or run["state"]
        per_decision[decision] = per_decision.get(decision, 0) + 1

        ok, message = store.verify_chain(run_id)
        if ok:
            evidence_ok += 1
        else:
            evidence_bad.append(f"{run_id}: {message}")

        for row in artifacts.rows_for_run(run_id):
            if not artifacts.verify(row["artifact_id"]):
                artifact_bad.append(f"{run_id}:{row['artifact_id']}")

        denied = repo._conn.execute(
            "SELECT payload_json FROM events WHERE run_id = ? AND event_type = 'policy.denied'",
            (run_id,),
        ).fetchall()
        for row in denied:
            denied_events += 1
            payload = json.loads(row["payload_json"])
            rule = payload.get("reason_code") or payload.get("rule_id") or "?"
            denied_by_rule[rule] = denied_by_rule.get(rule, 0) + 1

        checks_run += repo._conn.execute(
            "SELECT COUNT(*) FROM checks WHERE run_id = ? AND state != 'declared'", (run_id,)
        ).fetchone()[0]
        attempts_total += len(repo.attempts_for_run(run_id))

    manual = load_manual()
    supervision = manual.get("supervision_minutes_per_run", [])
    reductions = [
        (1 - entry["minutes"] / entry["baseline_without_treepact"]) * 100
        for entry in supervision
        if entry.get("baseline_without_treepact", 0) > 0
    ]
    reductions_sorted = sorted(reductions)
    median_reduction = (
        reductions_sorted[len(reductions_sorted) // 2]
        if reductions_sorted else None
    )

    accepted = per_decision.get("accepted", 0)
    false_acceptances = manual.get("false_acceptances", [])
    false_acceptance_rate = (len(false_acceptances) / accepted * 100) if accepted else 0.0

    metrics = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_dir": str(data_dir),
        "total_runs": len(runs),
        "repos_used": len(per_project),
        "runs_per_repo": per_project,
        "runs_by_state": per_state,
        "runs_by_decision": per_decision,
        "attempts_total": attempts_total,
        "checks_executed_by_treepact": checks_run,
        "blocked_prohibited_actions": denied_events,
        "blocked_by_rule": denied_by_rule,
        "evidence_chain_ok": evidence_ok,
        "evidence_chain_bad": evidence_bad,
        "artifact_digest_bad": artifact_bad,
        "manual": {
            "supervision_entries": len(supervision),
            "supervision_reduction_median_pct": median_reduction,
            "false_acceptances": len(false_acceptances),
            "false_acceptance_rate_pct": round(false_acceptance_rate, 2),
            "critical_incidents": len(manual.get("incidents", [])),
            "incidents": manual.get("incidents", []),
        },
        "targets": {},
    }

    for target, threshold in TARGETS.items():
        key, bucket = {
            "total_runs": ("total_runs", "auto"),
            "repos_used_repeatedly": ("repos_used", "auto"),
            "blocked_prohibited_actions": ("blocked_prohibited_actions", "auto"),
            "supervision_reduction_median_pct": (
                "manual.supervision_reduction_median_pct", "manual"),
            "critical_incidents": ("manual.critical_incidents", "manual"),
            "false_acceptance_rate_pct": ("manual.false_acceptance_rate_pct", "manual"),
        }[target]
        value = metrics
        for part in key.split("."):
            value = value[part] if isinstance(value, dict) else None
        op = "min" if "min" in threshold else "max"
        achieved = (value is not None) and (
            (value >= threshold["min"]) if op == "min" else (value <= threshold["max"])
        )
        metrics["targets"][target] = {
            "value": value,
            "threshold": threshold,
            "achieved": achieved,
            "source": bucket,
        }
    conn.close()
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="M10 pilot metrics")
    parser.add_argument("--data-dir", type=pathlib.Path, default=None)
    parser.add_argument("--output", type=pathlib.Path, default=None)
    parser.add_argument("--final", action="store_true",
                        help="emit the closing scorecard")
    args = parser.parse_args()

    data_dir = args.data_dir or pathlib.Path.home() / "Library" / "Application Support" / "TreePact"
    metrics = compute(data_dir)
    output = args.output or (ROOT / "verification" / "pilot" / "metrics.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, indent=2) + "\n")

    print(f"pilot metrics -> {output}")
    print(f"  runs: {metrics['total_runs']} (target >= {TARGETS['total_runs']['min']})")
    print(f"  repos used: {metrics['repos_used']} (target >= {TARGETS['repos_used_repeatedly']['min']})")
    print(f"  blocked prohibited actions: {metrics['blocked_prohibited_actions']}")
    print(f"  evidence chains ok: {metrics['evidence_chain_ok']}/{metrics['total_runs']}")
    if metrics["evidence_chain_bad"]:
        print("  EVIDENCE CHAIN PROBLEMS:", metrics["evidence_chain_bad"])
    if metrics["artifact_digest_bad"]:
        print("  ARTIFACT DIGEST PROBLEMS:", metrics["artifact_digest_bad"])
    manual = metrics["manual"]
    print(f"  supervision median reduction: {manual['supervision_reduction_median_pct']}% "
          f"({manual['supervision_entries']} entries)")
    print(f"  false acceptance rate: {manual['false_acceptance_rate_pct']}% "
          f"({manual['false_acceptances']})")
    print(f"  critical incidents: {manual['critical_incidents']}")
    if args.final:
        missing = [t for t, r in metrics["targets"].items() if not r["achieved"]]
        print("\nscorecard:")
        for target, result in metrics["targets"].items():
            print(f"  [{'x' if result['achieved'] else ' '}] {target}: {result['value']} "
                  f"({result['source']})")
        print(f"\nverdict: {'all targets met' if not missing else 'targets pending: ' + ', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
