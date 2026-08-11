"""Human and JSON report projectors (STATE_AND_DATA_MODEL.md).

The human report is a projection of the machine-readable manifest and
captured evidence. It must not add unsupported claims: every statement
traces to events, checks, gates, or artifacts.
"""

from __future__ import annotations

import json
from typing import Any


def project_json(run: dict[str, Any], task: dict[str, Any] | None,
                 attempts: list[dict[str, Any]], checks: list[dict[str, Any]],
                 gates: list[dict[str, Any]], denials: list[dict[str, Any]],
                 artifacts: list[dict[str, Any]], event_head: str | None,
                 assurance_level: str, bundle_schema_version: int,
                 project: dict[str, Any] | None,
                 workspace: dict[str, Any] | None) -> dict[str, Any]:
    """Project stored state into the machine-readable Decision Bundle manifest
    (evidence.schema.json). Fields stay null/absent when no fact exists."""
    base_commit = run.get("base_commit") or ""
    workspace_state = "not_created"
    final_tree_digest = None
    final_tree_reason = None
    if workspace:
        workspace_state = workspace.get("state") or "active"
        final_tree_digest = workspace.get("current_tree_digest")
        if not final_tree_digest and workspace_state != "not_created":
            final_tree_reason = "tree_not_reconciled"

    resource_leases: list[dict[str, Any]] = []
    return {
        "bundle_schema_version": bundle_schema_version,
        "run_id": run["run_id"],
        "project_id": project["project_id"] if project else (task.get("project_id") if task else ""),
        "repository_identity": {
            "git_common_dir_digest": project["git_common_dir_identity"] if project else "",
            "root_label": project["display_name"] if project else "",
        },
        "base_commit": base_commit,
        "workspace_state": workspace_state,
        "final_tree_digest": final_tree_digest,
        "final_tree_reason_code": final_tree_reason,
        "pact_sha256": run.get("pact_sha256") or _pact_sha(run),
        "runtime": {
            "adapter_id": run.get("runtime_id") or "",
            "adapter_version": run.get("adapter_version") or "0.0.0",
            "runtime_version": run.get("runtime_version"),
            "provider_id": run.get("provider_id"),
            "model_profile": run.get("model_profile"),
            "external_session_id": run.get("external_session_id"),
        },
        "assurance_level": assurance_level,
        "attempts": [
            {
                "attempt_number": a["attempt_number"],
                "state": a["state"],
                "termination_code": a.get("termination_code"),
            }
            for a in attempts
        ],
        "checks": [
            {
                "check_id": c["check_id"],
                "phase": c["phase"],
                "state": c["state"],
                "input_snapshot_sha256": c["input_snapshot_sha256"],
                "exit_code": c.get("exit_code"),
                "artifact_refs": [
                    ref for ref in (
                        c.get("stdout_artifact_id"), c.get("stderr_artifact_id"),
                        c.get("result_artifact_id"),
                    ) if ref
                ],
            }
            for c in checks
        ],
        "gates": [
            {
                "gate_id": g["gate_id"],
                "state": g["state"],
                "reason_code": g["reason_code"],
                "evidence_refs": json.loads(g.get("evidence_refs_json") or "[]"),
            }
            for g in gates
        ],
        "policy_denials": [
            {
                "tool_name": d["tool_name"],
                "rule_id": d["rule_id"],
                "reason_code": d["reason_code"],
            }
            for d in denials
        ],
        "resource_summary": {
            "peak_process_memory_mb": run.get("peak_process_memory_mb"),
            "resource_wait_ms": run.get("resource_wait_ms") or 0,
            "pressure_events": run.get("pressure_events") or 0,
            "lease_outcome": run.get("lease_outcome") or "not_requested",
        },
        "decision": run.get("decision") or run.get("state"),
        "artifacts": [
            {
                "artifact_id": a["artifact_id"],
                "kind": a["kind"],
                "sha256": a["sha256"],
                "size_bytes": a["size_bytes"],
                "media_type": a["media_type"],
            }
            for a in artifacts
        ],
        "event_chain_head": event_head,
        "generated_at": run.get("updated_at") or "",
        "_unused_leases": resource_leases,
    }


def _pact_sha(run: dict[str, Any]) -> str:
    return ""


def project_markdown(manifest: dict[str, Any]) -> str:
    """Human-readable projection of the manifest. Adds no claims beyond it."""
    lines: list[str] = []
    lines.append(f"# TreePact run {manifest['run_id']}")
    lines.append("")
    lines.append(f"- Decision: **{manifest['decision']}**")
    lines.append(f"- Assurance level: {manifest['assurance_level']}")
    lines.append(f"- Runtime: {manifest['runtime']['adapter_id']}")
    provider = manifest["runtime"].get("provider_id")
    lines.append(f"- Provider: {provider or 'not recorded'}")
    profile = manifest["runtime"].get("model_profile")
    lines.append(f"- Model profile: {profile or 'not recorded'}")
    lines.append(f"- Pact hash: `{manifest['pact_sha256']}`")
    lines.append(f"- Base commit: `{manifest['base_commit']}`")
    lines.append(f"- Workspace state: {manifest['workspace_state']}")
    if manifest.get("final_tree_digest"):
        lines.append(f"- Final tree digest: `{manifest['final_tree_digest']}`")
    elif manifest.get("final_tree_reason_code"):
        lines.append(f"- Final tree: none ({manifest['final_tree_reason_code']})")
    lines.append(f"- Event chain head: `{manifest['event_chain_head'] or 'n/a'}`")
    lines.append("")

    lines.append("## Attempts")
    lines.append("")
    for attempt in manifest["attempts"]:
        term = attempt["termination_code"] or "-"
        lines.append(f"- Attempt {attempt['attempt_number']}: {attempt['state']} ({term})")
    lines.append("")

    lines.append("## Checks")
    lines.append("")
    if manifest["checks"]:
        for check in manifest["checks"]:
            exit_code = check["exit_code"] if check["exit_code"] is not None else "-"
            lines.append(
                f"- `{check['check_id']}` [{check['phase']}] {check['state']} exit={exit_code}"
            )
    else:
        lines.append("- no checks executed")
    lines.append("")

    lines.append("## Gates")
    lines.append("")
    if manifest["gates"]:
        for gate in manifest["gates"]:
            lines.append(f"- {gate['gate_id']}: {gate['state']} ({gate['reason_code']})")
    else:
        lines.append("- no gates calculated")
    lines.append("")

    if manifest["policy_denials"]:
        lines.append("## Policy denials")
        lines.append("")
        for denial in manifest["policy_denials"]:
            lines.append(
                f"- {denial['tool_name']}: {denial['rule_id']} ({denial['reason_code']})"
            )
        lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    if manifest["artifacts"]:
        for artifact in manifest["artifacts"]:
            lines.append(
                f"- {artifact['kind']} `{artifact['artifact_id']}` "
                f"({artifact['size_bytes']} bytes, sha256 `{artifact['sha256']}`)"
            )
    else:
        lines.append("- no artifacts")
    lines.append("")
    lines.append("*This report is a projection of captured facts; "
                 "accepted does not mean the code is correct or ready to merge.*")
    return "\n".join(lines)
