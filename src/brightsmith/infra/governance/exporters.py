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
    from brightsmith.infra.dq_scorecard import generate_scorecard
    from brightsmith.infra.governance.product import get_current_specs
    from brightsmith.infra.dq_runner import get_latest_results

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


def export_pipeline_state_to_files(output_dir: Path | None = None) -> list[Path]:
    """Export pipeline events to JSON state snapshots."""
    from brightsmith.config import PIPELINE_STATE_DIR
    from brightsmith.infra.governance.product import get_current_specs, get_pipeline_events

    out_dir = output_dir or PIPELINE_STATE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for spec in get_current_specs():
        spec_name = spec.get("spec_name")
        if not spec_name:
            continue
        payload = {**spec, "events": get_pipeline_events(spec_name)}
        path = out_dir / f"{spec_name}-pipeline.json"
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
    from brightsmith.infra.governance.product import _query_table

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
