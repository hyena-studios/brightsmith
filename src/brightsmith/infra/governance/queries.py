"""Query infrastructure and read API for product governance tables.

Houses the private table-access helpers (_get_governance_table, _write_records,
_query_table), GovernanceReadError, and all get_* query functions.
"""

from __future__ import annotations

import logging

from brightsmith.infra.governance.schemas import _GRAIN_PREFIXES, _TABLE_CONFIGS
from brightsmith.infra.governance.serializers import normalize_table_name
from brightsmith.infra.grain import compute_grain_id

logger = logging.getLogger(__name__)


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
    return get_or_create_table(catalog, "governance_product", table_name, schema)


def _write_records(table_name: str, records: list[dict]) -> dict:
    """Write records to a governance table via promote().

    Computes grain IDs and uses promote() for idempotent append.
    Returns promote result dict.
    """
    from brightsmith.infra.promote import promote

    if not records:
        return {"promoted": 0, "skipped": 0, "snapshot_id": None}

    _, grain_fields = _TABLE_CONFIGS[table_name]
    prefix = _GRAIN_PREFIXES.get(table_name, table_name.upper()[:4])

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
# Query functions
# ---------------------------------------------------------------------------


class GovernanceReadError(Exception):
    """Raised when a governance table read fails.

    This exists so a *read failure* can never be confused with *no data*. A
    swallowed read (the old ``return []``) made every governance read failure
    look like "nothing recorded yet", which let checks such as
    governance-reviewer's "rules have been executed" pass vacuously. Callers
    MUST distinguish this error from an empty result set.
    """

    def __init__(self, table_name: str, original: Exception):
        self.table_name = table_name
        self.original = original
        super().__init__(
            f"Failed to read governance table '{table_name}': {original}"
        )


def _query_table(table_name: str, sql: str, params: list | None = None) -> list[dict]:
    """Run a DuckDB query against a governance table.

    Returns an empty list ONLY when the table genuinely has no rows. Any read
    failure (missing/corrupt table, engine error) raises
    :class:`GovernanceReadError` so it is never silently reported as "no data".
    """
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
        return [dict(zip(columns, row, strict=False)) for row in rows]
    except Exception as e:
        logger.error("Query failed on governance.%s", table_name, exc_info=True)
        raise GovernanceReadError(table_name, e) from e


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


def get_contract_columns(contract_name: str | None = None) -> list[dict]:
    """Get contract column records, optionally filtered by contract name."""
    if contract_name:
        rows = _query_table("contract_columns", """
            SELECT * FROM arrow_table
            WHERE contract_name = $1
            ORDER BY ordinal_position
        """, [contract_name])
    else:
        rows = _query_table("contract_columns", """
        SELECT * FROM arrow_table
        ORDER BY contract_name, ordinal_position
    """)
    for row in rows:
        row.setdefault("business_term", row.get("business_term_id"))
    return rows


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


def get_sessions(
    spec_name: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Get session-log records, most recent first, optionally filtered by spec."""
    conditions = ["1=1"]
    params: list = []
    idx = 0

    if spec_name:
        idx += 1
        conditions.append(f"spec_name = ${idx}")
        params.append(spec_name)
    idx += 1
    where = " AND ".join(conditions)

    return _query_table("sessions", f"""
        SELECT * FROM arrow_table
        WHERE {where}
        ORDER BY started_at DESC
        LIMIT ${idx}
    """, params + [limit])


def get_dq_rules(spec_name: str, *, table_name: str | None = None) -> list[dict]:
    """Get current DQ rules for a spec (latest version per rule_id)."""
    if table_name:
        table_name = normalize_table_name(table_name)
        return _query_table("dq_rules", """
            SELECT * FROM arrow_table
            WHERE spec_name = $1 AND table_name = $2
              AND (spec_name, rule_id, version) IN (
                SELECT spec_name, rule_id, MAX(version)
                FROM arrow_table
                WHERE spec_name = $1 AND table_name = $2
                GROUP BY spec_name, rule_id
              )
            ORDER BY rule_id
        """, [spec_name, table_name])
    return _query_table("dq_rules", """
        SELECT * FROM arrow_table
        WHERE spec_name = $1
          AND (spec_name, rule_id, version) IN (
            SELECT spec_name, rule_id, MAX(version)
            FROM arrow_table
            WHERE spec_name = $1
            GROUP BY spec_name, rule_id
          )
        ORDER BY rule_id
    """, [spec_name])


def get_dq_acknowledgments(run_id: str | None = None, spec_name: str | None = None) -> list[dict]:
    """Get DQ acknowledgments, optionally filtered by run_id or spec_name."""
    if run_id:
        return _query_table("dq_acknowledgments", """
            SELECT * FROM arrow_table WHERE run_id = $1 ORDER BY acknowledged_at
        """, [run_id])
    if spec_name:
        return _query_table("dq_acknowledgments", """
            SELECT * FROM arrow_table WHERE spec_name = $1 ORDER BY acknowledged_at
        """, [spec_name])
    return _query_table("dq_acknowledgments", """
        SELECT * FROM arrow_table ORDER BY acknowledged_at DESC LIMIT 100
    """)


def get_cab_decisions(
    spec_name: str | None = None,
    table_name: str | None = None,
    decision_id: str | None = None,
) -> list[dict]:
    """Get CAB decisions with optional filters."""
    if decision_id:
        return _query_table("cab_decisions", """
            SELECT * FROM arrow_table WHERE decision_id = $1
        """, [decision_id])
    if spec_name:
        return _query_table("cab_decisions", """
            SELECT * FROM arrow_table WHERE spec_name = $1 ORDER BY created_at DESC
        """, [spec_name])
    if table_name:
        return _query_table("cab_decisions", """
            SELECT * FROM arrow_table WHERE table_name = $1 ORDER BY created_at DESC
        """, [table_name])
    return _query_table("cab_decisions", """
        SELECT * FROM arrow_table ORDER BY created_at DESC LIMIT 100
    """)


def get_golden_dataset(spec_name: str) -> list[dict]:
    """Get golden dataset values for a spec."""
    return _query_table("golden_datasets", """
        SELECT * FROM arrow_table WHERE spec_name = $1 ORDER BY column_name
    """, [spec_name])


def get_run_history(limit: int = 20) -> list[dict]:
    """Get pipeline run history, most recent first."""
    return _query_table("run_history", """
        SELECT * FROM arrow_table ORDER BY started_at DESC LIMIT $1
    """, [limit])


def get_chaos_manifest(run_id: str) -> dict | None:
    """Get a chaos manifest by run_id."""
    results = _query_table("chaos_manifests", """
        SELECT * FROM arrow_table WHERE run_id = $1
    """, [run_id])
    return results[0] if results else None


def get_document(doc_type: str, doc_name: str, *, version: int | None = None) -> dict | None:
    """Get a document by type and name. Returns latest version if version not specified."""
    if version:
        results = _query_table("documents", """
            SELECT * FROM arrow_table
            WHERE doc_type = $1 AND doc_name = $2 AND version = $3
        """, [doc_type, doc_name, version])
    else:
        results = _query_table("documents", """
            SELECT * FROM arrow_table
            WHERE doc_type = $1 AND doc_name = $2
            ORDER BY version DESC LIMIT 1
        """, [doc_type, doc_name])
    return results[0] if results else None


def get_documents_by_type(doc_type: str, *, spec_name: str | None = None) -> list[dict]:
    """Get all documents of a given type (latest version per doc_name)."""
    if spec_name:
        return _query_table("documents", """
            SELECT * FROM arrow_table
            WHERE doc_type = $1 AND spec_name = $2
              AND (doc_type, doc_name, version) IN (
                SELECT doc_type, doc_name, MAX(version)
                FROM arrow_table
                WHERE doc_type = $1 AND spec_name = $2
                GROUP BY doc_type, doc_name
              )
            ORDER BY doc_name
        """, [doc_type, spec_name])
    return _query_table("documents", """
        SELECT * FROM arrow_table
        WHERE doc_type = $1
          AND (doc_type, doc_name, version) IN (
            SELECT doc_type, doc_name, MAX(version)
            FROM arrow_table
            WHERE doc_type = $1
            GROUP BY doc_type, doc_name
          )
        ORDER BY doc_name
    """, [doc_type])


def get_data_dictionary(
    table_name: str | None = None,
    zone: str | None = None,
) -> list[dict]:
    """Get data dictionary entries, optionally filtered by table_name or zone."""
    if table_name:
        return _query_table("data_dictionary", """
            SELECT * FROM arrow_table
            WHERE table_name = $1
            ORDER BY ordinal_position
        """, [table_name])
    if zone:
        return _query_table("data_dictionary", """
            SELECT * FROM arrow_table
            WHERE zone = $1
            ORDER BY table_name, ordinal_position
        """, [zone])
    return _query_table("data_dictionary", """
        SELECT * FROM arrow_table ORDER BY table_name, ordinal_position
    """)


def get_model_entities(
    level: str | None = None,
    zone: str | None = None,
    entity_group: str | None = None,
) -> list[dict]:
    """Get model entities with optional filters."""
    conditions = ["1=1"]
    params: list = []
    idx = 0

    if level:
        idx += 1
        conditions.append(f"level = ${idx}")
        params.append(level)
    if zone:
        idx += 1
        conditions.append(f"zone = ${idx}")
        params.append(zone)
    if entity_group:
        idx += 1
        conditions.append(f"entity_group = ${idx}")
        params.append(entity_group)

    where = " AND ".join(conditions)
    return _query_table("model_entities", f"""
        SELECT * FROM arrow_table WHERE {where} ORDER BY entity_id
    """, params or None)


def get_model_columns(
    entity_id: str | None = None,
    level: str | None = None,
) -> list[dict]:
    """Get model columns with optional filters."""
    if entity_id and level:
        return _query_table("model_columns", """
            SELECT * FROM arrow_table
            WHERE entity_id = $1 AND level = $2
            ORDER BY ordinal_position
        """, [entity_id, level])
    if entity_id:
        return _query_table("model_columns", """
            SELECT * FROM arrow_table
            WHERE entity_id = $1
            ORDER BY level, ordinal_position
        """, [entity_id])
    if level:
        return _query_table("model_columns", """
            SELECT * FROM arrow_table
            WHERE level = $1
            ORDER BY entity_id, ordinal_position
        """, [level])
    return _query_table("model_columns", """
        SELECT * FROM arrow_table ORDER BY entity_id, level, ordinal_position
    """)


def get_model_relationships(
    level: str | None = None,
    entity_group: str | None = None,
) -> list[dict]:
    """Get model relationships with optional filters."""
    if level and entity_group:
        return _query_table("model_relationships", """
            SELECT * FROM arrow_table
            WHERE level = $1 AND entity_group = $2
            ORDER BY relationship_id
        """, [level, entity_group])
    if level:
        return _query_table("model_relationships", """
            SELECT * FROM arrow_table WHERE level = $1 ORDER BY relationship_id
        """, [level])
    if entity_group:
        return _query_table("model_relationships", """
            SELECT * FROM arrow_table WHERE entity_group = $1 ORDER BY relationship_id
        """, [entity_group])
    return _query_table("model_relationships", """
        SELECT * FROM arrow_table ORDER BY level, relationship_id
    """)


def get_policies(
    policy_type: str | None = None,
    target_zone: str | None = None,
    target_table: str | None = None,
) -> list[dict]:
    """Get policies with optional filters."""
    if policy_type:
        return _query_table("policies", """
            SELECT * FROM arrow_table WHERE policy_type = $1 ORDER BY policy_name
        """, [policy_type])
    if target_zone:
        return _query_table("policies", """
            SELECT * FROM arrow_table WHERE target_zone = $1 ORDER BY policy_name
        """, [target_zone])
    if target_table:
        return _query_table("policies", """
            SELECT * FROM arrow_table WHERE target_table = $1 ORDER BY policy_name
        """, [target_table])
    return _query_table("policies", """
        SELECT * FROM arrow_table ORDER BY policy_type, policy_name
    """)


def get_scorecard_data(spec_name: str) -> dict | None:
    """Get scorecard data by joining dq_runs + dq_rule_results + dq_rules.

    Returns a dict with run info and enriched rule results (with category/priority
    from dq_rules table if available).
    """
    latest_run = get_latest_dq_run(spec_name)
    if not latest_run:
        return None

    run_id = latest_run.get("run_id", "")
    rule_results = get_dq_rule_results(run_id)
    rules = get_dq_rules(spec_name)

    # Build rule lookup for enrichment
    rule_lookup = {r["rule_id"]: r for r in rules}

    # Enrich rule results with dq_rules data
    enriched = []
    for rr in rule_results:
        rule_def = rule_lookup.get(rr.get("rule_id", ""), {})
        enriched.append({
            **rr,
            "rule_sql": rule_def.get("sql", ""),
            "rule_status": rule_def.get("status", ""),
            "rule_version": rule_def.get("version"),
        })

    return {
        "run": latest_run,
        "results": enriched,
        "rules_count": len(rules),
    }


def get_governance_summary() -> dict:
    """Comprehensive governance summary for Brightforge dashboard.

    Returns aggregated DQ scores, governance completeness, pipeline progress,
    and zone-level rollups in a single call.
    """
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
