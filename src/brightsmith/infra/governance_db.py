"""Governance admin database — structured Iceberg tables for governance state.

Provides 7 governance Iceberg tables that agents write to alongside file
outputs. Brightforge queries these instead of parsing loose files.

Tables:
    governance.spec_registry      — Hub: spec -> tables, DQ scores, completeness
    governance.dq_runs            — DQ execution runs (aggregate)
    governance.dq_rule_results    — Individual rule outcomes per run
    governance.pipeline_events    — Pipeline step execution log
    governance.contract_metadata  — Synced from YAML contracts
    governance.glossary_terms     — Synced from business-glossary.json
    governance.agent_activity     — Structured agent findings/decisions

Usage:
    from brightsmith.infra.governance_db import (
        write_spec_registry, write_dq_run, write_dq_rule_results,
        write_pipeline_event, sync_contract, sync_glossary_term,
        write_agent_activity, log_agent_finding,
        get_current_specs, get_governance_summary,
    )

CLI:
    python -m brightsmith.infra.governance_db status
    python -m brightsmith.infra.governance_db sync
    python -m brightsmith.infra.governance_db export
    python -m brightsmith.infra.governance_db query <table>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    FloatType,
    IntegerType,
    NestedField,
    StringType,
    TimestamptzType,
)

from brightsmith.infra.grain import compute_grain_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SPEC_REGISTRY_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="zone", field_type=StringType(), required=True),
    NestedField(field_id=4, name="status", field_type=StringType(), required=True),
    NestedField(field_id=5, name="output_tables", field_type=StringType(), required=True),
    NestedField(field_id=6, name="dq_score_pct", field_type=FloatType(), required=False),
    NestedField(field_id=7, name="dq_rules_total", field_type=IntegerType(), required=False),
    NestedField(field_id=8, name="dq_rules_passing", field_type=IntegerType(), required=False),
    NestedField(field_id=9, name="dq_rules_failing", field_type=IntegerType(), required=False),
    NestedField(field_id=10, name="dq_p0_passed", field_type=BooleanType(), required=False),
    NestedField(field_id=11, name="has_contract", field_type=BooleanType(), required=False),
    NestedField(field_id=12, name="has_lineage", field_type=BooleanType(), required=False),
    NestedField(field_id=13, name="has_golden_dataset", field_type=BooleanType(), required=False),
    NestedField(field_id=14, name="has_data_dictionary", field_type=BooleanType(), required=False),
    NestedField(field_id=15, name="has_cde_tags", field_type=BooleanType(), required=False),
    NestedField(field_id=16, name="pipeline_step_current", field_type=StringType(), required=False),
    NestedField(field_id=17, name="pipeline_steps_total", field_type=IntegerType(), required=False),
    NestedField(field_id=18, name="pipeline_steps_completed", field_type=IntegerType(), required=False),
    NestedField(field_id=19, name="spec_file_path", field_type=StringType(), required=False),
    NestedField(field_id=20, name="updated_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=21, name="updated_by", field_type=StringType(), required=True),
)

DQ_RUNS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="run_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="executed_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=6, name="rules_total", field_type=IntegerType(), required=True),
    NestedField(field_id=7, name="rules_passed", field_type=IntegerType(), required=True),
    NestedField(field_id=8, name="rules_failed", field_type=IntegerType(), required=True),
    NestedField(field_id=9, name="rules_errored", field_type=IntegerType(), required=True),
    NestedField(field_id=10, name="rules_warning", field_type=IntegerType(), required=True),
    NestedField(field_id=11, name="score_pct", field_type=FloatType(), required=True),
    NestedField(field_id=12, name="p0_passed", field_type=BooleanType(), required=True),
    NestedField(field_id=13, name="p0_total", field_type=IntegerType(), required=False),
    NestedField(field_id=14, name="p0_failed", field_type=IntegerType(), required=False),
    NestedField(field_id=15, name="p1_total", field_type=IntegerType(), required=False),
    NestedField(field_id=16, name="p1_failed", field_type=IntegerType(), required=False),
    NestedField(field_id=17, name="duration_ms", field_type=IntegerType(), required=False),
    NestedField(field_id=18, name="result_file_path", field_type=StringType(), required=False),
    NestedField(field_id=19, name="updated_at", field_type=TimestamptzType(), required=True),
)

DQ_RULE_RESULTS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="run_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="rule_id", field_type=StringType(), required=True),
    NestedField(field_id=5, name="category", field_type=StringType(), required=True),
    NestedField(field_id=6, name="priority", field_type=StringType(), required=True),
    NestedField(field_id=7, name="description", field_type=StringType(), required=True),
    NestedField(field_id=8, name="passed", field_type=BooleanType(), required=True),
    NestedField(field_id=9, name="raw_value", field_type=StringType(), required=False),
    NestedField(field_id=10, name="threshold", field_type=StringType(), required=False),
    NestedField(field_id=11, name="violations", field_type=IntegerType(), required=False),
    NestedField(field_id=12, name="execution_time_ms", field_type=IntegerType(), required=False),
    NestedField(field_id=13, name="error_message", field_type=StringType(), required=False),
    NestedField(field_id=14, name="executed_at", field_type=TimestamptzType(), required=True),
)

PIPELINE_EVENTS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="step_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="event_type", field_type=StringType(), required=True),
    NestedField(field_id=5, name="agent_id", field_type=StringType(), required=False),
    NestedField(field_id=6, name="output_path", field_type=StringType(), required=False),
    NestedField(field_id=7, name="skip_reason", field_type=StringType(), required=False),
    NestedField(field_id=8, name="approval_decision", field_type=StringType(), required=False),
    NestedField(field_id=9, name="approval_by", field_type=StringType(), required=False),
    NestedField(field_id=10, name="notes", field_type=StringType(), required=False),
    NestedField(field_id=11, name="event_time", field_type=TimestamptzType(), required=True),
)

CONTRACT_METADATA_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="contract_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="spec_name", field_type=StringType(), required=False),
    NestedField(field_id=4, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="zone", field_type=StringType(), required=True),
    NestedField(field_id=6, name="version", field_type=StringType(), required=True),
    NestedField(field_id=7, name="status", field_type=StringType(), required=True),
    NestedField(field_id=8, name="column_count", field_type=IntegerType(), required=False),
    NestedField(field_id=9, name="grain_columns", field_type=StringType(), required=False),
    NestedField(field_id=10, name="has_dq_rules", field_type=BooleanType(), required=False),
    NestedField(field_id=11, name="has_golden_dataset", field_type=BooleanType(), required=False),
    NestedField(field_id=12, name="freshness_sla_hours", field_type=IntegerType(), required=False),
    NestedField(field_id=13, name="contract_file_path", field_type=StringType(), required=True),
    NestedField(field_id=14, name="updated_at", field_type=TimestamptzType(), required=True),
)

GLOSSARY_TERMS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="term_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="term", field_type=StringType(), required=True),
    NestedField(field_id=4, name="definition", field_type=StringType(), required=True),
    NestedField(field_id=5, name="category", field_type=StringType(), required=True),
    NestedField(field_id=6, name="source", field_type=StringType(), required=True),
    NestedField(field_id=7, name="approval_status", field_type=StringType(), required=True),
    NestedField(field_id=8, name="used_in_specs", field_type=StringType(), required=False),
    NestedField(field_id=9, name="updated_at", field_type=TimestamptzType(), required=True),
)

AGENT_ACTIVITY_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="agent_id", field_type=StringType(), required=True),
    NestedField(field_id=4, name="pipeline_step", field_type=StringType(), required=False),
    NestedField(field_id=5, name="activity_type", field_type=StringType(), required=True),
    NestedField(field_id=6, name="severity", field_type=StringType(), required=True),
    NestedField(field_id=7, name="summary", field_type=StringType(), required=True),
    NestedField(field_id=8, name="detail", field_type=StringType(), required=False),
    NestedField(field_id=9, name="references", field_type=StringType(), required=False),
    NestedField(field_id=10, name="related_table", field_type=StringType(), required=False),
    NestedField(field_id=11, name="related_rule_id", field_type=StringType(), required=False),
    NestedField(field_id=12, name="resolution_status", field_type=StringType(), required=False),
    NestedField(field_id=13, name="resolved_by", field_type=StringType(), required=False),
    NestedField(field_id=14, name="resolved_at", field_type=TimestamptzType(), required=False),
    NestedField(field_id=15, name="event_time", field_type=TimestamptzType(), required=True),
)

# Table name -> (schema, grain_fields) mapping
_TABLE_CONFIGS: dict[str, tuple[Schema, list[str]]] = {
    "spec_registry": (SPEC_REGISTRY_SCHEMA, ["spec_name", "status", "updated_at"]),
    "dq_runs": (DQ_RUNS_SCHEMA, ["run_id"]),
    "dq_rule_results": (DQ_RULE_RESULTS_SCHEMA, ["run_id", "rule_id"]),
    "pipeline_events": (PIPELINE_EVENTS_SCHEMA, ["spec_name", "step_name", "event_type", "event_time"]),
    "contract_metadata": (CONTRACT_METADATA_SCHEMA, ["contract_name", "version"]),
    "glossary_terms": (GLOSSARY_TERMS_SCHEMA, ["term_id", "updated_at"]),
    "agent_activity": (AGENT_ACTIVITY_SCHEMA, ["spec_name", "agent_id", "activity_type", "summary", "event_time"]),
}


# ---------------------------------------------------------------------------
# Table access
# ---------------------------------------------------------------------------


def _get_governance_table(table_name: str):
    """Lazily create and return a governance Iceberg table."""
    from brightsmith.config import CATALOG_PATH, GOVERNANCE_WAREHOUSE
    from brightsmith.infra.iceberg_setup import get_catalog, get_or_create_table

    if table_name not in _TABLE_CONFIGS:
        raise ValueError(f"Unknown governance table: {table_name}")

    schema, _ = _TABLE_CONFIGS[table_name]
    catalog = get_catalog(GOVERNANCE_WAREHOUSE, CATALOG_PATH)
    return get_or_create_table(catalog, "governance", table_name, schema)


def _write_records(table_name: str, records: list[dict]) -> dict:
    """Write records to a governance table via promote().

    Computes grain IDs and uses promote() for idempotent append.
    Returns promote result dict.
    """
    from brightsmith.infra.promote import promote

    if not records:
        return {"promoted": 0, "skipped": 0, "snapshot_id": None}

    _, grain_fields = _TABLE_CONFIGS[table_name]
    prefix = table_name.upper()[:4]

    for record in records:
        # Compute grain ID — need string representation of timestamps
        grain_row = {}
        for f in grain_fields:
            val = record.get(f, "")
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            grain_row[f] = val
        record["record_id"] = compute_grain_id(grain_row, grain_fields, prefix=prefix)

    table = _get_governance_table(table_name)
    return promote(table, records)


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------


def write_spec_registry(
    spec_name: str,
    zone: str,
    status: str,
    output_tables: list[str],
    updated_by: str,
    *,
    dq_score_pct: float | None = None,
    dq_rules_total: int | None = None,
    dq_rules_passing: int | None = None,
    dq_rules_failing: int | None = None,
    dq_p0_passed: bool | None = None,
    has_contract: bool | None = None,
    has_lineage: bool | None = None,
    has_golden_dataset: bool | None = None,
    has_data_dictionary: bool | None = None,
    has_cde_tags: bool | None = None,
    pipeline_step_current: str | None = None,
    pipeline_steps_total: int | None = None,
    pipeline_steps_completed: int | None = None,
    spec_file_path: str | None = None,
) -> dict:
    """Write a spec registry row. Append-only; latest row wins."""
    now = datetime.now(timezone.utc)
    record = {
        "spec_name": spec_name,
        "zone": zone,
        "status": status,
        "output_tables": json.dumps(output_tables),
        "dq_score_pct": dq_score_pct,
        "dq_rules_total": dq_rules_total,
        "dq_rules_passing": dq_rules_passing,
        "dq_rules_failing": dq_rules_failing,
        "dq_p0_passed": dq_p0_passed,
        "has_contract": has_contract,
        "has_lineage": has_lineage,
        "has_golden_dataset": has_golden_dataset,
        "has_data_dictionary": has_data_dictionary,
        "has_cde_tags": has_cde_tags,
        "pipeline_step_current": pipeline_step_current,
        "pipeline_steps_total": pipeline_steps_total,
        "pipeline_steps_completed": pipeline_steps_completed,
        "spec_file_path": spec_file_path,
        "updated_at": now,
        "updated_by": updated_by,
    }
    return _write_records("spec_registry", [record])


def write_dq_run(
    run_id: str,
    spec_name: str,
    table_name: str,
    executed_at: datetime,
    rules_total: int,
    rules_passed: int,
    rules_failed: int,
    rules_errored: int,
    score_pct: float,
    p0_passed: bool,
    *,
    rules_warning: int = 0,
    p0_total: int | None = None,
    p0_failed: int | None = None,
    p1_total: int | None = None,
    p1_failed: int | None = None,
    duration_ms: int | None = None,
    result_file_path: str | None = None,
) -> dict:
    """Write a DQ run summary row."""
    now = datetime.now(timezone.utc)
    record = {
        "run_id": run_id,
        "spec_name": spec_name,
        "table_name": table_name,
        "executed_at": executed_at,
        "rules_total": rules_total,
        "rules_passed": rules_passed,
        "rules_failed": rules_failed,
        "rules_errored": rules_errored,
        "rules_warning": rules_warning,
        "score_pct": score_pct,
        "p0_passed": p0_passed,
        "p0_total": p0_total,
        "p0_failed": p0_failed,
        "p1_total": p1_total,
        "p1_failed": p1_failed,
        "duration_ms": duration_ms,
        "result_file_path": result_file_path,
        "updated_at": now,
    }
    return _write_records("dq_runs", [record])


def write_dq_rule_results(run_id: str, spec_name: str, results: list[dict]) -> dict:
    """Write individual DQ rule results for a run.

    Args:
        run_id: FK to dq_runs.
        spec_name: FK to spec_registry.
        results: List of result dicts from dq_runner (rule_id, category, passed, etc.).
    """
    records = []
    for r in results:
        executed_at = r.get("executed_at")
        if isinstance(executed_at, str):
            executed_at = datetime.fromisoformat(executed_at)
        elif executed_at is None:
            executed_at = datetime.now(timezone.utc)

        records.append({
            "run_id": run_id,
            "spec_name": spec_name,
            "rule_id": r.get("rule_id", ""),
            "category": r.get("category", ""),
            "priority": r.get("priority", "P3"),
            "description": r.get("description", r.get("detail", "")),
            "passed": r.get("passed", False),
            "raw_value": str(r.get("raw_value")) if r.get("raw_value") is not None else None,
            "threshold": r.get("threshold"),
            "violations": r.get("violations"),
            "execution_time_ms": r.get("execution_time_ms"),
            "error_message": r.get("error"),
            "executed_at": executed_at,
        })
    return _write_records("dq_rule_results", records)


def write_pipeline_event(
    spec_name: str,
    step_name: str,
    event_type: str,
    *,
    agent_id: str | None = None,
    output_path: str | None = None,
    skip_reason: str | None = None,
    approval_decision: str | None = None,
    approval_by: str | None = None,
    notes: str | None = None,
    event_time: datetime | None = None,
) -> dict:
    """Write a pipeline step event."""
    record = {
        "spec_name": spec_name,
        "step_name": step_name,
        "event_type": event_type,
        "agent_id": agent_id,
        "output_path": output_path,
        "skip_reason": skip_reason,
        "approval_decision": approval_decision,
        "approval_by": approval_by,
        "notes": notes,
        "event_time": event_time or datetime.now(timezone.utc),
    }
    return _write_records("pipeline_events", [record])


def sync_contract(contract: dict, contract_file_path: str) -> dict:
    """Sync a contract YAML dict to the contract_metadata table."""
    meta = contract.get("metadata", {})
    schema = contract.get("schema", {})
    quality = contract.get("quality", {})
    table_name = schema.get("table", "")
    namespace = schema.get("namespace", table_name.split(".")[0] if "." in table_name else "")

    record = {
        "contract_name": meta.get("name", ""),
        "spec_name": meta.get("spec") or None,
        "table_name": table_name,
        "zone": namespace,
        "version": meta.get("version", "1.0.0"),
        "status": meta.get("status", "draft"),
        "column_count": len(schema.get("columns", [])),
        "grain_columns": json.dumps(schema.get("grain", {}).get("columns", [])),
        "has_dq_rules": bool(quality.get("dq_rules", {}).get("rules_file")),
        "has_golden_dataset": bool(quality.get("accuracy", {}).get("golden_dataset")),
        "freshness_sla_hours": quality.get("freshness", {}).get("max_staleness_hours"),
        "contract_file_path": contract_file_path,
        "updated_at": datetime.now(timezone.utc),
    }
    return _write_records("contract_metadata", [record])


def sync_glossary_term(term: dict) -> dict:
    """Sync a single glossary term to the glossary_terms table."""
    record = {
        "term_id": term.get("term_id", ""),
        "term": term.get("name", term.get("term", "")),
        "definition": term.get("definition", ""),
        "category": term.get("category", ""),
        "source": term.get("source", ""),
        "approval_status": term.get("approval_status", term.get("status", "")),
        "used_in_specs": json.dumps(term.get("used_in_specs", [])),
        "updated_at": datetime.now(timezone.utc),
    }
    return _write_records("glossary_terms", [record])


def write_agent_activity(
    spec_name: str,
    agent_id: str,
    activity_type: str,
    severity: str,
    summary: str,
    *,
    pipeline_step: str | None = None,
    detail: str | None = None,
    references: list[str] | None = None,
    related_table: str | None = None,
    related_rule_id: str | None = None,
    resolution_status: str | None = None,
    resolved_by: str | None = None,
    resolved_at: datetime | None = None,
    event_time: datetime | None = None,
) -> dict:
    """Write an agent activity record."""
    record = {
        "spec_name": spec_name,
        "agent_id": agent_id,
        "pipeline_step": pipeline_step,
        "activity_type": activity_type,
        "severity": severity,
        "summary": summary,
        "detail": detail,
        "references": json.dumps(references) if references else None,
        "related_table": related_table,
        "related_rule_id": related_rule_id,
        "resolution_status": resolution_status,
        "resolved_by": resolved_by,
        "resolved_at": resolved_at,
        "event_time": event_time or datetime.now(timezone.utc),
    }
    return _write_records("agent_activity", [record])


def log_agent_finding(
    spec_name: str,
    agent_id: str,
    summary: str,
    detail: str | None = None,
    severity: str = "info",
    **kwargs,
) -> dict | None:
    """Convenience wrapper for write_agent_activity.

    Fault-tolerant: logs a warning on failure, never raises.
    """
    try:
        return write_agent_activity(
            spec_name=spec_name,
            agent_id=agent_id,
            activity_type=kwargs.pop("activity_type", "finding"),
            severity=severity,
            summary=summary,
            detail=detail,
            **kwargs,
        )
    except Exception:
        logger.warning(
            "Failed to log agent finding for %s/%s: %s",
            spec_name, agent_id, summary,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


def _query_table(table_name: str, sql: str, params: list | None = None) -> list[dict]:
    """Run a DuckDB query against a governance table."""
    import duckdb

    try:
        table = _get_governance_table(table_name)
        arrow_table = table.scan().to_arrow()
        if arrow_table.num_rows == 0:
            return []
        con = duckdb.connect()
        if params:
            rel = con.sql(sql, params=params)
        else:
            rel = con.sql(sql)
        columns = [desc[0] for desc in rel.description]
        rows = rel.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        logger.warning("Query failed on governance.%s", table_name, exc_info=True)
        return []


def get_current_specs() -> list[dict]:
    """Get current state of all specs (latest row per spec_name)."""
    return _query_table("spec_registry", """
        SELECT * FROM arrow_table
        WHERE (spec_name, updated_at) IN (
            SELECT spec_name, MAX(updated_at)
            FROM arrow_table
            GROUP BY spec_name
        )
        ORDER BY spec_name
    """)


def get_dq_runs(spec_name: str | None = None, limit: int = 20) -> list[dict]:
    """Get DQ run history."""
    if spec_name:
        return _query_table("dq_runs", """
            SELECT * FROM arrow_table
            WHERE spec_name = $1
            ORDER BY executed_at DESC
            LIMIT $2
        """, [spec_name, limit])
    return _query_table("dq_runs", """
        SELECT * FROM arrow_table
        ORDER BY executed_at DESC
        LIMIT $1
    """, [limit])


def get_latest_dq_run(spec_name: str) -> dict | None:
    """Get the most recent DQ run for a spec."""
    results = get_dq_runs(spec_name, limit=1)
    return results[0] if results else None


def get_dq_rule_results(run_id: str) -> list[dict]:
    """Get individual rule results for a DQ run."""
    return _query_table("dq_rule_results", """
        SELECT * FROM arrow_table
        WHERE run_id = $1
        ORDER BY rule_id
    """, [run_id])


def get_pipeline_events(spec_name: str) -> list[dict]:
    """Get pipeline events for a spec, ordered chronologically."""
    return _query_table("pipeline_events", """
        SELECT * FROM arrow_table
        WHERE spec_name = $1
        ORDER BY event_time
    """, [spec_name])


def get_contracts() -> list[dict]:
    """Get current contract metadata (latest version per contract)."""
    return _query_table("contract_metadata", """
        SELECT * FROM arrow_table
        WHERE (contract_name, updated_at) IN (
            SELECT contract_name, MAX(updated_at)
            FROM arrow_table
            GROUP BY contract_name
        )
        ORDER BY contract_name
    """)


def get_agent_activity(
    spec_name: str | None = None,
    agent_id: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Get agent activity records with optional filters."""
    conditions = ["1=1"]
    params: list = []
    idx = 0

    if spec_name:
        idx += 1
        conditions.append(f"spec_name = ${idx}")
        params.append(spec_name)
    if agent_id:
        idx += 1
        conditions.append(f"agent_id = ${idx}")
        params.append(agent_id)
    if severity:
        idx += 1
        conditions.append(f"severity = ${idx}")
        params.append(severity)
    idx += 1
    where = " AND ".join(conditions)

    return _query_table("agent_activity", f"""
        SELECT * FROM arrow_table
        WHERE {where}
        ORDER BY event_time DESC
        LIMIT ${idx}
    """, params + [limit])


def get_governance_summary() -> dict:
    """Comprehensive governance summary for Brightforge dashboard.

    Returns aggregated DQ scores, governance completeness, pipeline progress,
    and zone-level rollups in a single call.
    """
    import duckdb

    summary: dict = {
        "specs": [],
        "dq_overall": {"score_pct": 0.0, "rules_total": 0, "rules_passing": 0, "rules_failing": 0, "p0_passed": True},
        "governance_completeness": {"total_specs": 0, "with_dq": 0, "with_contract": 0, "with_lineage": 0, "with_golden_dataset": 0},
        "zones": {},
        "open_blockers": [],
    }

    specs = get_current_specs()
    if not specs:
        return summary

    summary["specs"] = specs
    total = len(specs)
    summary["governance_completeness"]["total_specs"] = total

    total_rules = 0
    total_passing = 0
    total_failing = 0
    all_p0 = True

    for s in specs:
        zone = s.get("zone", "unknown")
        summary["zones"].setdefault(zone, {"specs": 0, "complete": 0, "dq_score": 0.0})
        summary["zones"][zone]["specs"] += 1
        if s.get("status") == "COMPLETE":
            summary["zones"][zone]["complete"] += 1

        rt = s.get("dq_rules_total") or 0
        rp = s.get("dq_rules_passing") or 0
        rf = s.get("dq_rules_failing") or 0
        total_rules += rt
        total_passing += rp
        total_failing += rf
        if s.get("dq_p0_passed") is False:
            all_p0 = False

        if rt > 0:
            summary["governance_completeness"]["with_dq"] += 1
        if s.get("has_contract"):
            summary["governance_completeness"]["with_contract"] += 1
        if s.get("has_lineage"):
            summary["governance_completeness"]["with_lineage"] += 1
        if s.get("has_golden_dataset"):
            summary["governance_completeness"]["with_golden_dataset"] += 1

    summary["dq_overall"]["rules_total"] = total_rules
    summary["dq_overall"]["rules_passing"] = total_passing
    summary["dq_overall"]["rules_failing"] = total_failing
    summary["dq_overall"]["p0_passed"] = all_p0
    if total_rules > 0:
        summary["dq_overall"]["score_pct"] = round(total_passing / total_rules * 100, 1)

    for zone_data in summary["zones"].values():
        if zone_data["specs"] > 0:
            # Average DQ score would require per-spec data; just count completion
            pass

    # Open blockers from agent_activity
    summary["open_blockers"] = get_agent_activity(severity="blocker")

    return summary


# ---------------------------------------------------------------------------
# Sync: backfill from existing file artifacts
# ---------------------------------------------------------------------------


def sync_from_files() -> dict:
    """Backfill governance tables from existing file artifacts.

    Idempotent via promote() — safe to run repeatedly.

    Returns counts of records synced per table.
    """
    from brightsmith.config import (
        DQ_RESULTS_DIR,
        DQ_RULES_DIR,
        PIPELINE_STATE_DIR,
        PROJECT_ROOT,
    )

    counts: dict[str, int] = {}

    # 1. DQ results -> dq_runs + dq_rule_results
    dq_synced = 0
    dq_rules_synced = 0
    if DQ_RESULTS_DIR.exists():
        for path in sorted(DQ_RESULTS_DIR.glob("*.json")):
            if "-ack-" in path.name:
                continue
            try:
                data = json.loads(path.read_text())
                run_id = data.get("run_id", "")
                spec = data.get("spec", "")
                executed_at_str = data.get("executed_at", "")
                executed_at = datetime.fromisoformat(executed_at_str) if executed_at_str else datetime.now(timezone.utc)

                # Extract table names from rules
                rules = data.get("results", [])
                tables = set()
                for r in rules:
                    if r.get("spec"):
                        for rule_file in DQ_RULES_DIR.glob("*.json"):
                            rd = json.loads(rule_file.read_text())
                            if rd.get("spec") == r["spec"]:
                                tables.update(rd.get("tables", []))
                table_name = ", ".join(sorted(tables)) if tables else spec

                total = (data.get("rules_total")
                         or data.get("rules_executed")
                         or data.get("total_rules")
                         or len(rules))
                passed = data.get("rules_passed") or data.get("passed") or sum(1 for r in rules if r.get("passed"))
                failed = data.get("rules_failed") or data.get("failed") or sum(1 for r in rules if not r.get("passed"))
                errored = data.get("rules_errored", sum(1 for r in rules if r.get("error")))
                score = (passed / total * 100) if total > 0 else 0.0

                # p0_passed can be bool or string like "PASSED"
                p0_raw = data.get("p0_passed") or data.get("p0_gate")
                if isinstance(p0_raw, str):
                    p0_passed = p0_raw.upper() in ("PASSED", "PASS", "TRUE")
                else:
                    p0_passed = bool(p0_raw) if p0_raw is not None else True

                result = write_dq_run(
                    run_id=run_id, spec_name=spec, table_name=table_name,
                    executed_at=executed_at, rules_total=total, rules_passed=passed,
                    rules_failed=failed, rules_errored=errored, score_pct=score,
                    p0_passed=p0_passed,
                    result_file_path=str(path.relative_to(PROJECT_ROOT)),
                )
                dq_synced += result.get("promoted", 0)

                # Individual rule results
                rule_results = write_dq_rule_results(run_id, spec, rules)
                dq_rules_synced += rule_results.get("promoted", 0)
            except Exception:
                logger.warning("Failed to sync DQ results from %s", path, exc_info=True)

    counts["dq_runs"] = dq_synced
    counts["dq_rule_results"] = dq_rules_synced

    # 2. Pipeline state -> pipeline_events + spec_registry
    pipeline_synced = 0
    registry_synced = 0
    if PIPELINE_STATE_DIR.exists():
        for path in sorted(PIPELINE_STATE_DIR.glob("*-pipeline.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem.replace("-pipeline", ""))
                zone = data.get("zone", "")
                steps = data.get("steps", {})

                # Pipeline events from step completions
                for step_name, step_data in steps.items():
                    status = step_data.get("status", "NOT_STARTED")
                    if status in ("COMPLETED", "SKIPPED"):
                        event_type = status
                        event_time_str = step_data.get("completed_at") or step_data.get("started_at")
                        event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(timezone.utc)
                        result = write_pipeline_event(
                            spec_name=spec, step_name=step_name,
                            event_type=event_type,
                            agent_id=step_data.get("agent"),
                            output_path=step_data.get("output"),
                            event_time=event_time,
                        )
                        pipeline_synced += result.get("promoted", 0)

                # Skipped steps
                for step_name, skip_data in data.get("skipped_steps", {}).items():
                    event_time_str = skip_data.get("skipped_at")
                    event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(timezone.utc)
                    result = write_pipeline_event(
                        spec_name=spec, step_name=step_name,
                        event_type="SKIPPED",
                        skip_reason=skip_data.get("reason"),
                        event_time=event_time,
                    )
                    pipeline_synced += result.get("promoted", 0)

                # Approvals
                for artifact, approval in data.get("approvals", {}).items():
                    event_time_str = approval.get("decided_at")
                    event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(timezone.utc)
                    result = write_pipeline_event(
                        spec_name=spec, step_name=artifact,
                        event_type="APPROVED" if approval.get("status") == "APPROVED" else "FAILED",
                        approval_decision=approval.get("status"),
                        approval_by=approval.get("decided_by"),
                        notes=approval.get("notes"),
                        event_time=event_time,
                    )
                    pipeline_synced += result.get("promoted", 0)

                # Spec registry from pipeline state
                completed = sum(1 for s in steps.values() if s.get("status") == "COMPLETED")
                total_steps = len(steps)
                spec_status = data.get("status", "IN_PROGRESS")
                output_tables = data.get("output_tables", [])

                result = write_spec_registry(
                    spec_name=spec, zone=zone, status=spec_status,
                    output_tables=output_tables, updated_by="sync",
                    pipeline_steps_total=total_steps,
                    pipeline_steps_completed=completed,
                    spec_file_path=f"docs/specs/{spec}.md",
                )
                registry_synced += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to sync pipeline state from %s", path, exc_info=True)

    counts["pipeline_events"] = pipeline_synced
    counts["spec_registry"] = registry_synced

    # 3. Contracts -> contract_metadata
    contract_synced = 0
    contracts_dir = PROJECT_ROOT / "governance" / "data-contracts"
    if contracts_dir.exists():
        for path in sorted(contracts_dir.glob("*.yaml")):
            try:
                import yaml
                text = path.read_text()
                # Handle multi-document YAML (some contracts have --- separators)
                docs = [d for d in yaml.safe_load_all(text) if d is not None]
                for data in docs:
                    # Normalize: some contracts use "contract:" wrapper, others use "metadata:"
                    if "contract" in data and "metadata" not in data:
                        inner = data["contract"]
                        data = {
                            "metadata": {
                                "name": inner.get("name", ""),
                                "version": inner.get("version", "1.0.0"),
                                "status": inner.get("status", "active"),
                                "spec": inner.get("spec", ""),
                            },
                            "schema": {
                                "table": inner.get("name", ""),
                                "namespace": inner.get("name", "").split(".")[0] if "." in inner.get("name", "") else "",
                                "grain": {"columns": inner.get("grain", [])},
                                "columns": inner.get("columns", []),
                            },
                            "quality": inner.get("quality", {}),
                        }
                    rel_path = str(path.relative_to(PROJECT_ROOT))
                    result = sync_contract(data, rel_path)
                    contract_synced += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to sync contract from %s", path, exc_info=True)
    counts["contract_metadata"] = contract_synced

    # 4. Glossary -> glossary_terms
    glossary_synced = 0
    glossary_path = PROJECT_ROOT / "governance" / "business-glossary.json"
    if glossary_path.exists():
        try:
            data = json.loads(glossary_path.read_text())
            for term in data.get("terms", []):
                result = sync_glossary_term(term)
                glossary_synced += result.get("promoted", 0)
        except Exception:
            logger.warning("Failed to sync glossary", exc_info=True)
    counts["glossary_terms"] = glossary_synced

    # 5. Enrich spec_registry with DQ scores and governance completeness flags
    #    by cross-referencing the data we just synced
    enriched = 0
    if PIPELINE_STATE_DIR.exists():
        for path in sorted(PIPELINE_STATE_DIR.glob("*-pipeline.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem.replace("-pipeline", ""))
                zone = data.get("zone", "")
                output_tables = data.get("output_tables", [])

                # DQ scores from latest results file
                dq_kwargs: dict = {}
                if DQ_RESULTS_DIR.exists():
                    dq_files = sorted(DQ_RESULTS_DIR.glob(f"{spec}-*.json"), reverse=True)
                    dq_files = [f for f in dq_files if "-ack-" not in f.name]
                    if dq_files:
                        dq_data = json.loads(dq_files[0].read_text())
                        total = (dq_data.get("rules_total")
                                 or dq_data.get("rules_executed")
                                 or dq_data.get("total_rules")
                                 or 0)
                        passed = dq_data.get("rules_passed") or dq_data.get("passed") or 0
                        failed = dq_data.get("rules_failed") or dq_data.get("failed") or 0
                        # p0_passed can be bool or string like "PASSED"
                        p0_raw = dq_data.get("p0_passed") or dq_data.get("p0_gate")
                        if isinstance(p0_raw, str):
                            p0_passed = p0_raw.upper() in ("PASSED", "PASS", "TRUE")
                        else:
                            p0_passed = bool(p0_raw) if p0_raw is not None else True
                        dq_kwargs = {
                            "dq_score_pct": (passed / total * 100) if total > 0 else 0.0,
                            "dq_rules_total": total,
                            "dq_rules_passing": passed,
                            "dq_rules_failing": failed,
                            "dq_p0_passed": p0_passed,
                        }

                # Governance completeness flags
                contracts_dir_path = PROJECT_ROOT / "governance" / "data-contracts"
                has_contract = False
                if contracts_dir_path.exists():
                    # Check by spec name or table name slug
                    spec_slug = spec.replace("_", "-")
                    has_contract = bool(
                        any(contracts_dir_path.glob(f"*{spec_slug}*"))
                        or any(
                            contracts_dir_path.glob(f"*{tbl.replace('.', '-').replace('_', '-')}*")
                            for tbl in output_tables
                        )
                    )

                lineage_dir = PROJECT_ROOT / "governance" / "lineage"
                has_lineage = lineage_dir.exists() and any(lineage_dir.glob(f"*{spec}*"))

                gd_dir = PROJECT_ROOT / "governance" / "golden-datasets"
                has_golden = gd_dir.exists() and any(gd_dir.glob(f"*{spec}*"))

                steps = data.get("steps", {})
                completed = sum(1 for s in steps.values() if s.get("status") in ("COMPLETED", "SKIPPED"))

                result = write_spec_registry(
                    spec_name=spec, zone=zone,
                    status=data.get("status", "IN_PROGRESS"),
                    output_tables=output_tables, updated_by="sync",
                    pipeline_steps_total=len(steps),
                    pipeline_steps_completed=completed,
                    spec_file_path=f"docs/specs/{spec}.md",
                    has_contract=has_contract,
                    has_lineage=has_lineage,
                    has_golden_dataset=has_golden,
                    **dq_kwargs,
                )
                enriched += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to enrich spec registry for %s", path, exc_info=True)
    counts["spec_registry_enriched"] = enriched

    return counts


# ---------------------------------------------------------------------------
# Export: regenerate file artifacts from Iceberg tables
# ---------------------------------------------------------------------------


def export_to_files() -> dict:
    """Regenerate file artifacts from governance Iceberg tables.

    Returns counts of files generated per type.
    """
    from brightsmith.config import DQ_SCORECARDS_DIR, PROJECT_ROOT

    counts: dict[str, int] = {}

    # Export DQ scorecards from dq_runs + dq_rule_results
    specs = get_current_specs()
    scorecard_count = 0
    for spec in specs:
        spec_name = spec.get("spec_name", "")
        latest_run = get_latest_dq_run(spec_name)
        if not latest_run:
            continue

        rule_results = get_dq_rule_results(latest_run.get("run_id", ""))
        if not rule_results:
            continue

        # Build a run_result dict compatible with dq_scorecard.generate_scorecard
        run_result = {
            "run_id": latest_run.get("run_id"),
            "spec": spec_name,
            "executed_at": str(latest_run.get("executed_at", "")),
            "rules_total": latest_run.get("rules_total", 0),
            "rules_passed": latest_run.get("rules_passed", 0),
            "rules_failed": latest_run.get("rules_failed", 0),
            "rules_errored": latest_run.get("rules_errored", 0),
            "p0_passed": latest_run.get("p0_passed", True),
            "results": [
                {
                    "rule_id": r.get("rule_id"),
                    "category": r.get("category"),
                    "passed": r.get("passed"),
                    "raw_value": r.get("raw_value"),
                    "threshold": r.get("threshold"),
                    "detail": r.get("description", ""),
                    "violations": r.get("violations"),
                    "execution_time_ms": r.get("execution_time_ms"),
                    "error": r.get("error_message"),
                    "executed_at": str(r.get("executed_at", "")),
                }
                for r in rule_results
            ],
        }

        try:
            from brightsmith.infra.dq_scorecard import generate_scorecard
            generate_scorecard(run_result, spec_name)
            scorecard_count += 1
        except Exception:
            logger.warning("Failed to export scorecard for %s", spec_name, exc_info=True)

    counts["scorecards"] = scorecard_count
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_status() -> None:
    """Print governance database summary."""
    summary = get_governance_summary()

    specs = summary.get("specs", [])
    dq = summary.get("dq_overall", {})
    gc = summary.get("governance_completeness", {})

    print("Governance Database Status")
    print("=" * 60)

    # DQ summary
    print(f"\nDQ Score: {dq.get('score_pct', 0):.1f}%")
    print(f"  Rules: {dq.get('rules_passing', 0)}/{dq.get('rules_total', 0)} passing")
    print(f"  P0 Gate: {'PASS' if dq.get('p0_passed', True) else 'FAIL'}")

    # Governance completeness
    total = gc.get("total_specs", 0)
    print(f"\nGovernance Completeness ({total} specs):")
    if total > 0:
        print(f"  DQ rules:       {gc.get('with_dq', 0)}/{total} ({gc.get('with_dq', 0)/total*100:.0f}%)")
        print(f"  Contracts:      {gc.get('with_contract', 0)}/{total} ({gc.get('with_contract', 0)/total*100:.0f}%)")
        print(f"  Lineage:        {gc.get('with_lineage', 0)}/{total} ({gc.get('with_lineage', 0)/total*100:.0f}%)")
        print(f"  Golden datasets:{gc.get('with_golden_dataset', 0)}/{total} ({gc.get('with_golden_dataset', 0)/total*100:.0f}%)")

    # Per-spec table
    if specs:
        print(f"\n{'Spec':<35} {'Zone':<8} {'Status':<12} {'DQ%':>6} {'Steps':>8}")
        print("-" * 75)
        for s in specs:
            dq_pct = f"{s.get('dq_score_pct', 0):.0f}%" if s.get("dq_rules_total") else "N/A"
            steps_done = s.get("pipeline_steps_completed") or 0
            steps_total = s.get("pipeline_steps_total") or 0
            steps_str = f"{steps_done}/{steps_total}" if steps_total else "N/A"
            print(f"{s.get('spec_name', '?'):<35} {s.get('zone', '?'):<8} {s.get('status', '?'):<12} {dq_pct:>6} {steps_str:>8}")

    # Open blockers
    blockers = summary.get("open_blockers", [])
    if blockers:
        print(f"\nOpen Blockers ({len(blockers)}):")
        for b in blockers[:5]:
            print(f"  [{b.get('agent_id')}] {b.get('spec_name')}: {b.get('summary', '')[:60]}")

    # Zone summary
    zones = summary.get("zones", {})
    if zones:
        print(f"\n{'Zone':<12} {'Specs':>6} {'Complete':>10}")
        print("-" * 30)
        for zone, data in sorted(zones.items()):
            print(f"{zone:<12} {data.get('specs', 0):>6} {data.get('complete', 0):>10}")


def cmd_sync() -> None:
    """Backfill governance tables from existing file artifacts."""
    print("Syncing governance tables from file artifacts...")
    counts = sync_from_files()
    print("\nSync complete:")
    for table, count in sorted(counts.items()):
        print(f"  {table}: {count} records synced")
    total = sum(counts.values())
    print(f"\nTotal: {total} records")


def cmd_export() -> None:
    """Regenerate file artifacts from governance tables."""
    print("Exporting file artifacts from governance tables...")
    counts = export_to_files()
    print("\nExport complete:")
    for artifact_type, count in sorted(counts.items()):
        print(f"  {artifact_type}: {count} generated")


def cmd_query(table_name: str) -> None:
    """Run an ad-hoc query against a governance table."""
    import duckdb

    if table_name not in _TABLE_CONFIGS:
        print(f"Unknown table: {table_name}")
        print(f"Available: {', '.join(sorted(_TABLE_CONFIGS.keys()))}")
        sys.exit(1)

    try:
        table = _get_governance_table(table_name)
        arrow_table = table.scan().to_arrow()
        if arrow_table.num_rows == 0:
            print(f"governance.{table_name}: 0 rows")
            return

        con = duckdb.connect()
        con.sql("SELECT * FROM arrow_table ORDER BY 1 DESC LIMIT 20").show()
        print(f"\n({arrow_table.num_rows} total rows)")
    except Exception as e:
        print(f"Error querying governance.{table_name}: {e}")
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="governance_db",
        description="Brightsmith Governance Admin Database",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Governance database summary")
    subparsers.add_parser("sync", help="Backfill from existing file artifacts")
    subparsers.add_parser("export", help="Regenerate file artifacts from tables")

    query_parser = subparsers.add_parser("query", help="Query a governance table")
    query_parser.add_argument("table", help=f"Table name: {', '.join(sorted(_TABLE_CONFIGS.keys()))}")

    args = parser.parse_args()

    commands = {
        "status": cmd_status,
        "sync": cmd_sync,
        "export": cmd_export,
        "query": lambda: cmd_query(args.table),
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
