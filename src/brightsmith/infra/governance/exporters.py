"""Explicit governance file exporters.

All functions read Iceberg governance tables and then render files for human
review or compatibility. Runtime writers should not call these implicitly.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def export_contracts_to_files(output_dir: Path | None = None) -> list[Path]:
    """Export product contracts from Iceberg rows to YAML files."""
    from brightsmith.config import PROJECT_ROOT
    from brightsmith.infra.governance.product import get_contract_columns, get_contracts

    out_dir = output_dir or PROJECT_ROOT / "governance" / "data-contracts"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for contract in get_contracts():
        name = contract["contract_name"]
        columns = get_contract_columns(name)
        doc = {
            "apiVersion": "brightsmith/v1",
            "kind": "DataContract",
            "metadata": {
                "name": name,
                "version": contract.get("version"),
                "status": contract.get("status"),
                "spec": contract.get("spec_name"),
            },
            "schema": {
                "table": contract.get("table_name"),
                "namespace": contract.get("zone"),
                "grain": {"columns": json.loads(contract.get("grain_columns") or "[]")},
                "columns": [
                    {
                        "name": col.get("column_name"),
                        "type": col.get("data_type"),
                        "nullable": col.get("is_nullable"),
                        "business_term_id": col.get("business_term_id"),
                        "is_cde": col.get("is_cde"),
                        "cde_rationale": col.get("cde_rationale"),
                        "cde_criteria_ids": json.loads(col.get("cde_criteria_ids") or "[]"),
                        "criticality_classification_id": col.get("criticality_classification_id"),
                        "policy_ids": json.loads(col.get("policy_ids") or "[]"),
                        "pii_classification_id": col.get("pii_classification_id"),
                        "is_pii": col.get("is_pii"),
                        "pii_rationale": col.get("pii_rationale"),
                        "description": col.get("description"),
                    }
                    for col in columns
                ],
            },
        }
        path = out_dir / f"{name}.yaml"
        path.write_text(yaml.dump(doc, default_flow_style=False, sort_keys=False))
        paths.append(path)
    return paths


def export_dq_results_to_files(output_dir: Path | None = None) -> list[Path]:
    """Export DQ run results from Iceberg to JSON files."""
    from brightsmith.config import DQ_RESULTS_DIR
    from brightsmith.infra.governance.product import get_dq_rule_results, get_dq_runs

    out_dir = output_dir or DQ_RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for run in get_dq_runs(limit=10_000):
        run_id = run["run_id"]
        payload = {**run, "results": get_dq_rule_results(run_id)}
        spec = run.get("spec_name") or "all"
        path = out_dir / f"{spec}-{run_id}.json"
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        paths.append(path)
    return paths


def export_dq_scorecards_to_files(output_dir: Path | None = None) -> list[Path]:
    """Export DQ scorecards from Iceberg run data."""
    from brightsmith.config import DQ_SCORECARDS_DIR
    from brightsmith.infra.dq_runner import get_latest_results
    from brightsmith.infra.dq_scorecard import generate_scorecard
    from brightsmith.infra.governance.product import get_current_specs

    out_dir = output_dir or DQ_SCORECARDS_DIR
    paths: list[Path] = []
    for spec in get_current_specs():
        spec_name = spec.get("spec_name")
        if not spec_name:
            continue
        result = get_latest_results(spec_name)
        if result:
            path = generate_scorecard(result, spec_name)
            if output_dir and path.parent != out_dir:
                target = out_dir / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(path.read_text())
                path = target
            paths.append(path)
    return paths


def _reconstruct_pipeline_state(
    spec_row: dict, events: list[dict], existing: dict | None,
) -> dict:
    """Rebuild the legacy pipeline-state shape from Iceberg events.

    Emits the SAME shape `PipelineGate` writes and reads — keyed `spec` (not
    `spec_name`), carrying the real `zone`, and a reconstructed `steps` dict so
    `check_zone_transition` and `audit_report` can consume it. Steps are derived
    by replaying events with latest-event-per-step winning (the governance DB
    appends a row per event, audit P3).

    `output_hash` is not in the event schema; it is preserved from an existing
    state file when present, otherwise omitted (never fabricated) so the
    output-hash tamper-detection feature keeps working.
    """
    from brightsmith.infra.pipeline_gate import _get_steps

    existing = existing or {}
    spec_name = spec_row.get("spec_name")
    zone = spec_row.get("zone") or existing.get("zone") or ""
    # spec_registry does not carry mode; preserve an existing file's mode,
    # else default greenfield (bronze greenfield/backfill share a step list).
    mode = existing.get("mode", "greenfield")

    # Canonical scaffold: agent/requires/blocking/skippable per step, NOT_STARTED.
    steps: dict = {}
    try:
        canonical = _get_steps(zone, mode)  # type: ignore[arg-type]
    except KeyError:
        canonical = ()
    for step in canonical:
        steps[step.name] = {
            "status": "NOT_STARTED",
            "agent": step.agent,
            "requires": list(step.requires),
            "blocking": step.blocking,
            "skippable": step.skippable,
        }

    existing_steps = existing.get("steps", {})
    skipped_steps: dict = dict(existing.get("skipped_steps", {}))
    approvals: dict = dict(existing.get("approvals", {}))

    # Latest event per step name (events arrive ordered by event_time ascending).
    latest: dict[str, dict] = {}
    for ev in events:
        step_name = ev.get("step_name")
        if step_name:
            latest[step_name] = ev

    for step_name, ev in latest.items():
        etype = ev.get("event_type")
        event_time = str(ev.get("event_time")) if ev.get("event_time") is not None else None

        if etype in ("APPROVED", "FAILED"):
            approvals[step_name] = {
                "status": "APPROVED" if etype == "APPROVED" else "CHANGES_REQUESTED",
                "decided_by": ev.get("approval_by"),
                "decided_at": event_time,
                "document": "",
                "notes": ev.get("notes") or "",
            }
            continue

        entry = steps.setdefault(step_name, {"status": "NOT_STARTED", "agent": ev.get("agent_id")})
        if etype == "STARTED":
            entry["status"] = "IN_PROGRESS"
            entry["started_at"] = event_time
        elif etype == "COMPLETED":
            entry["status"] = "COMPLETED"
            entry["completed_at"] = event_time
            if ev.get("output_path"):
                entry["output"] = ev["output_path"]
        elif etype == "SKIPPED":
            entry["status"] = "SKIPPED"
            skipped_steps[step_name] = {
                "reason": ev.get("skip_reason") or "",
                "evidence": "",
                "skipped_at": event_time,
            }

    # Preserve recorded output hashes from any existing file (not in event schema).
    for step_name, prev in existing_steps.items():
        if prev.get("output_hash") and step_name in steps:
            steps[step_name].setdefault("output_hash", prev["output_hash"])
            steps[step_name].setdefault("output", prev.get("output", ""))

    return {
        "spec": spec_name,
        "zone": zone,
        "mode": mode,
        "started": existing.get("started") or str(spec_row.get("updated_at") or ""),
        "steps": steps,
        "skipped_steps": skipped_steps,
        "approvals": approvals,
    }


def export_pipeline_state_to_files(output_dir: Path | None = None) -> list[Path]:
    """Export pipeline events to JSON state snapshots in the legacy gate shape.

    Reconstructs the `{spec, zone, mode, started, steps, skipped_steps,
    approvals}` shape that `PipelineGate` reads (and `check_zone_transition` /
    `audit_report` consume) by replaying `get_pipeline_events`.
    """
    from brightsmith.config import PIPELINE_STATE_DIR
    from brightsmith.infra.governance.product import get_current_specs, get_pipeline_events

    out_dir = output_dir or PIPELINE_STATE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for spec in get_current_specs():
        spec_name = spec.get("spec_name")
        if not spec_name:
            continue
        path = out_dir / f"{spec_name}-pipeline.json"
        existing: dict | None = None
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                existing = None
        payload = _reconstruct_pipeline_state(spec, get_pipeline_events(spec_name), existing)
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        paths.append(path)
    return paths


def export_cab_decisions_to_files(output_dir: Path | None = None) -> list[Path]:
    """Export CAB decisions from Iceberg to JSON files."""
    from brightsmith.config import CAB_DECISIONS_DIR
    from brightsmith.infra.governance.product import get_cab_decisions

    out_dir = output_dir or CAB_DECISIONS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for decision in get_cab_decisions():
        path = out_dir / f"{decision['decision_id']}.json"
        path.write_text(json.dumps(decision, indent=2, default=str) + "\n")
        paths.append(path)
    return paths


def export_documents_to_files(output_dir: Path | None = None) -> list[Path]:
    """Export governance documents from Iceberg."""
    from brightsmith.config import PROJECT_ROOT
    from brightsmith.infra.governance.queries import _query_table

    out_dir = output_dir or PROJECT_ROOT / "governance" / "documents"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for doc in _query_table("documents", "SELECT * FROM arrow_table ORDER BY doc_type, doc_name, version"):
        path = out_dir / f"{doc['doc_type']}-{doc['doc_name']}-v{doc['version']}.md"
        path.write_text(doc.get("content") or "")
        paths.append(path)
    return paths


def export_all_governance_files() -> dict[str, list[Path]]:
    """Export all generated governance file types from Iceberg."""
    return {
        "contracts": export_contracts_to_files(),
        "dq_results": export_dq_results_to_files(),
        "dq_scorecards": export_dq_scorecards_to_files(),
        "pipeline_state": export_pipeline_state_to_files(),
        "cab_decisions": export_cab_decisions_to_files(),
        "documents": export_documents_to_files(),
    }


def export_to_files() -> dict:
    """Compatibility wrapper returning generated file counts by type."""
    exported = export_all_governance_files()
    return {name: len(paths) for name, paths in exported.items()}
