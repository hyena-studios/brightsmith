"""Write API for product governance tables."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from brightsmith.infra.governance.queries import _query_table, _write_records
from brightsmith.infra.governance.serializers import normalize_table_name, normalize_zone

logger = logging.getLogger(__name__)


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
    now = datetime.now(UTC)
    canonical_tables = [normalize_table_name(table) for table in output_tables]
    record = {
        "spec_name": spec_name,
        "zone": normalize_zone(zone),
        "status": status,
        "output_tables": json.dumps(canonical_tables),
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
    now = datetime.now(UTC)
    record = {
        "run_id": run_id,
        "spec_name": spec_name,
        "table_name": normalize_table_name(table_name),
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
            executed_at = datetime.now(UTC)

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
    content: str | None = None,
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
        "content": content,
        "event_time": event_time or datetime.now(UTC),
    }
    return _write_records("pipeline_events", [record])


def sync_contract(contract: dict, contract_file_path: str) -> dict:
    """Sync a contract dict to contract_metadata and contract_columns tables."""
    from brightsmith.infra.governance.resolution import validate_contract_column_references

    meta = contract.get("metadata", {})
    schema = contract.get("schema", {})
    quality = contract.get("quality", {})
    table_name = normalize_table_name(schema.get("table", ""))
    namespace = normalize_zone(schema.get("namespace", table_name.split(".")[0] if "." in table_name else ""))

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
        "updated_at": datetime.now(UTC),
    }
    result = _write_records("contract_metadata", [record])

    # Write column-level records
    columns = schema.get("columns", [])
    contract_name = meta.get("name", "")
    version = meta.get("version", "1.0.0")
    now = datetime.now(UTC)

    col_records = []
    for i, col in enumerate(columns):
        validate_contract_column_references(col)
        col_records.append({
            "contract_name": contract_name,
            "table_name": table_name,
            "zone": namespace,
            "column_name": col.get("name", ""),
            "ordinal_position": i,
            "data_type": col.get("type"),
            "is_nullable": col.get("nullable", True),
            "is_cde": col.get("is_cde", False),
            "cde_rationale": col.get("cde_rationale"),
            "is_pii": col.get("is_pii", False),
            "pii_rationale": col.get("pii_rationale"),
            "business_term_id": col.get("business_term_id", col.get("business_term")),
            "cde_criteria_ids": json.dumps(col.get("cde_criteria_ids", [])),
            "criticality_classification_id": col.get("criticality_classification_id"),
            "policy_ids": json.dumps(col.get("policy_ids", [])),
            "pii_classification_id": col.get("pii_classification_id"),
            "description": col.get("description"),
            "version": version,
            "updated_at": now,
        })

    if col_records:
        col_result = _write_records("contract_columns", col_records)
        result["columns_promoted"] = col_result.get("promoted", 0)
        result["columns_skipped"] = col_result.get("skipped", 0)

    return result


def sync_glossary_term(term: dict) -> dict:
    """Import a glossary term into enterprise standards and legacy table."""
    from brightsmith.infra.governance.enterprise import write_business_term

    write_business_term(
        term_id=term.get("term_id", ""),
        term=term.get("name", term.get("term", "")),
        description=term.get("definition"),
        status=term.get("approval_status", "approved"),
        metadata={
            "category": term.get("category", ""),
            "source": term.get("source", ""),
            "used_in_specs": term.get("used_in_specs", []),
        },
    )
    record = {
        "term_id": term.get("term_id", ""),
        "term": term.get("name", term.get("term", "")),
        "definition": term.get("definition", ""),
        "category": term.get("category", ""),
        "source": term.get("source", ""),
        "approval_status": term.get("approval_status", term.get("status", "")),
        "used_in_specs": json.dumps(term.get("used_in_specs", [])),
        "updated_at": datetime.now(UTC),
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
        "event_time": event_time or datetime.now(UTC),
    }
    return _write_records("agent_activity", [record])


def log_agent_finding(
    spec_name: str,
    agent_id: str,
    summary: str,
    detail: str | None = None,
    severity: str = "info",
    *,
    strict: bool = True,
    **kwargs,
) -> dict | None:
    """Convenience wrapper for :func:`write_agent_activity`.

    The governance DB is authoritative for the agent-activity feed, so a write
    failure is loud by default. When ``strict`` is True (the default) any
    failure PROPAGATES, so the calling pipeline step fails instead of silently
    dropping the finding — the same loud-failure doctrine the DQ and contract
    gates now follow.

    Pass ``strict=False`` for best-effort logging (logs a warning and returns
    ``None`` on failure) where a logging hiccup must not fail the surrounding
    work.
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
        if strict:
            raise
        logger.warning(
            "Failed to log agent finding for %s/%s: %s",
            spec_name, agent_id, summary,
            exc_info=True,
        )
        return None


def write_session(
    session_id: str,
    title: str,
    summary: str,
    author: str,
    *,
    spec_name: str | None = None,
    agents_involved: list[str] | None = None,
    artifacts: list[str] | None = None,
    content: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    event_time: datetime | None = None,
) -> dict:
    """Write a session log record to the governance ``sessions`` table.

    Restores the deleted ``docs/sessions/`` practice as an Iceberg-authoritative
    record. Idempotent on ``session_id`` (grain) — re-writing the same session is
    a no-op via ``promote`` dedup.
    """
    now = datetime.now(UTC)
    record = {
        "session_id": session_id,
        "spec_name": spec_name,
        "title": title,
        "summary": summary,
        "author": author,
        "agents_involved": json.dumps(agents_involved) if agents_involved else None,
        "artifacts": json.dumps(artifacts) if artifacts else None,
        "content": content,
        "started_at": started_at or now,
        "ended_at": ended_at,
        "event_time": event_time or now,
    }
    return _write_records("sessions", [record])


def log_session(
    session_id: str,
    title: str,
    summary: str,
    author: str,
    *,
    strict: bool = True,
    **kwargs,
) -> dict | None:
    """Convenience wrapper for :func:`write_session` (mirrors :func:`log_agent_finding`).

    Strict by default: a write failure PROPAGATES so a session that cannot be
    recorded fails loudly rather than vanishing. Pass ``strict=False`` for
    best-effort logging (warns and returns ``None`` on failure).
    """
    try:
        return write_session(
            session_id=session_id,
            title=title,
            summary=summary,
            author=author,
            **kwargs,
        )
    except Exception:
        if strict:
            raise
        logger.warning(
            "Failed to log session %s (%s): %s",
            session_id, title, summary,
            exc_info=True,
        )
        return None


def write_dq_rules(
    spec_name: str,
    table_name: str,
    rules: list[dict],
) -> dict:
    """Write DQ rule definitions to the dq_rules table.

    Each rule dict should have: rule_id, category, priority, description, sql, threshold.
    Status defaults to 'proposed'. Version is determined automatically by querying
    MAX(version) for each rule_id and incrementing.
    """
    if not rules:
        return {"promoted": 0, "skipped": 0}

    # Query existing versions for this spec to determine next version per rule
    existing = _query_table("dq_rules", """
        SELECT rule_id, MAX(version) as max_version
        FROM arrow_table
        WHERE spec_name = $1
        GROUP BY rule_id
    """, [spec_name])
    version_map = {r["rule_id"]: r["max_version"] for r in existing}

    now = datetime.now(UTC)
    records = []
    for r in rules:
        rule_id = r.get("rule_id", "")
        version = r.get("version")
        if version is None:
            version = (version_map.get(rule_id, 0) or 0) + 1
        records.append({
            "spec_name": spec_name,
            "table_name": normalize_table_name(table_name),
            "rule_id": rule_id,
            "category": r.get("category", ""),
            "priority": r.get("priority", "P3"),
            "description": r.get("description", ""),
            "sql": r.get("sql", ""),
            "threshold": r.get("threshold", ""),
            "status": r.get("status", "proposed"),
            "version": version,
            "approved_by": r.get("approved_by"),
            "approved_at": r.get("approved_at"),
            "updated_at": now,
        })
    return _write_records("dq_rules", records)


def write_dq_acknowledgment(
    run_id: str,
    rule_id: str,
    spec_name: str,
    acknowledged_by: str,
    reason: str,
    *,
    acknowledged_at: datetime | None = None,
) -> dict:
    """Write a DQ failure acknowledgment."""
    record = {
        "run_id": run_id,
        "rule_id": rule_id,
        "spec_name": spec_name,
        "acknowledged_by": acknowledged_by,
        "reason": reason,
        "acknowledged_at": acknowledged_at or datetime.now(UTC),
    }
    return _write_records("dq_acknowledgments", [record])


def write_cab_decision(
    decision_id: str,
    spec_name: str,
    table_name: str,
    classification: str,
    classification_reasons: list[str],
    decision: str,
    *,
    contract_version_before: str | None = None,
    contract_version_after: str | None = None,
    schema_diff: dict | None = None,
    blast_radius: dict | None = None,
    decided_by: str | None = None,
    decided_at: datetime | None = None,
    notes: str | None = None,
    rationale: str | None = None,
    fork_config: dict | None = None,
    human_override: dict | None = None,
) -> dict:
    """Write a CAB decision record."""
    record = {
        "decision_id": decision_id,
        "spec_name": spec_name,
        "table_name": normalize_table_name(table_name),
        "classification": classification,
        "classification_reasons": json.dumps(classification_reasons),
        "contract_version_before": contract_version_before,
        "contract_version_after": contract_version_after,
        "schema_diff": json.dumps(schema_diff) if schema_diff else None,
        "blast_radius": json.dumps(blast_radius) if blast_radius else None,
        "decision": decision,
        "decided_by": decided_by,
        "decided_at": decided_at,
        "notes": notes,
        "rationale": rationale,
        "fork_config": json.dumps(fork_config) if fork_config else None,
        "human_override": json.dumps(human_override) if human_override else None,
        "created_at": datetime.now(UTC),
    }
    return _write_records("cab_decisions", [record])


def write_golden_dataset_values(
    spec_name: str,
    table_name: str,
    values: list[dict],
) -> dict:
    """Write golden dataset values.

    Each value dict should have: value_description, column_name, expected_value, filters.
    Filters are normalized via json.dumps(sort_keys=True, separators=(',', ':'))
    before grain computation to ensure deterministic hashes.
    """
    now = datetime.now(UTC)
    records = []
    for v in values:
        # Normalize filters for deterministic grain
        filters = v.get("filters", {})
        if isinstance(filters, str):
            filters_str = filters
        else:
            filters_str = json.dumps(filters, sort_keys=True, separators=(",", ":"))

        records.append({
            "spec_name": spec_name,
            "table_name": normalize_table_name(table_name),
            "value_description": v.get("value_description", ""),
            "column_name": v.get("column_name", ""),
            "expected_value": str(v.get("expected_value", "")),
            "tolerance_pct": v.get("tolerance_pct"),
            "tolerance_type": v.get("tolerance_type"),
            "filters": filters_str,
            "last_verified_at": v.get("last_verified_at"),
            "last_verified_passed": v.get("last_verified_passed"),
            "updated_at": now,
        })
    return _write_records("golden_datasets", records)


def write_run_history(
    run_id: str,
    started_at: datetime,
    status: str,
    zones_summary: dict,
    *,
    completed_at: datetime | None = None,
    duration_seconds: float | None = None,
    golden_datasets_summary: dict | None = None,
    options: dict | None = None,
    error_message: str | None = None,
) -> dict:
    """Write a pipeline run history record."""
    record = {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": duration_seconds,
        "status": status,
        "zones_summary": json.dumps(zones_summary),
        "golden_datasets_summary": json.dumps(golden_datasets_summary) if golden_datasets_summary else None,
        "options": json.dumps(options) if options else None,
        "error_message": error_message,
        "updated_at": datetime.now(UTC),
    }
    return _write_records("run_history", [record])


def write_chaos_manifest(
    run_id: str,
    source_table: str,
    shadow_table: str,
    total_rows: int,
    corruption_rate: float,
    rows_corrupted: int,
    columns_corrupted: int,
    total_corruptions: int,
    *,
    seed: int | None = None,
    dimensions_covered: list[str] | None = None,
    corruptions_sample: list[dict] | None = None,
) -> dict:
    """Write a chaos monkey manifest record."""
    record = {
        "run_id": run_id,
        "source_table": normalize_table_name(source_table),
        "shadow_table": normalize_table_name(shadow_table),
        "total_rows": total_rows,
        "corruption_rate": corruption_rate,
        "seed": seed,
        "rows_corrupted": rows_corrupted,
        "columns_corrupted": columns_corrupted,
        "total_corruptions": total_corruptions,
        "dimensions_covered": json.dumps(dimensions_covered) if dimensions_covered else None,
        "corruptions_sample": json.dumps(corruptions_sample) if corruptions_sample else None,
        "created_at": datetime.now(UTC),
    }
    return _write_records("chaos_manifests", [record])


def write_document(
    doc_type: str,
    doc_name: str,
    title: str,
    content: str,
    *,
    version: int | None = None,
    spec_name: str | None = None,
    agent_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Write a prose governance document.

    If version is not provided, auto-increments by querying MAX(version)
    for the given doc_type + doc_name.
    """
    if version is None:
        existing = _query_table("documents", """
            SELECT MAX(version) as max_version
            FROM arrow_table
            WHERE doc_type = $1 AND doc_name = $2
        """, [doc_type, doc_name])
        max_v = existing[0]["max_version"] if existing and existing[0]["max_version"] is not None else 0
        version = max_v + 1

    record = {
        "doc_type": doc_type,
        "doc_name": doc_name,
        "spec_name": spec_name,
        "agent_id": agent_id,
        "title": title,
        "content": content,
        "version": version,
        "metadata": json.dumps(metadata) if metadata else None,
        "created_at": datetime.now(UTC),
    }
    return _write_records("documents", [record])


def write_data_dictionary(
    table_name: str,
    zone: str,
    columns: list[dict],
) -> dict:
    """Write data dictionary column records for a table.

    Each column dict should have: column_name, and optionally data_type,
    definition, nullable, is_grain, ordinal_position.
    """
    now = datetime.now(UTC)
    records = []
    for i, col in enumerate(columns):
        records.append({
            "table_name": normalize_table_name(table_name),
            "zone": normalize_zone(zone),
            "column_name": col.get("column_name", col.get("name", "")),
            "data_type": col.get("data_type", col.get("type")),
            "definition": col.get("definition", col.get("description")),
            "nullable": col.get("nullable"),
            "is_grain": col.get("is_grain", col.get("grain", False)),
            "ordinal_position": col.get("ordinal_position", i),
            "updated_at": now,
        })
    return _write_records("data_dictionary", records)


