"""Runtime lineage event emitter, query utilities, and CLI.

Emits OpenLineage-compatible START/COMPLETE/FAIL events to the
governance.lineage_events Iceberg table. Every promote function
calls these to create a runtime audit trail.

Usage:
    from brightsmith.infra.lineage import emit_start, emit_complete, emit_fail

CLI:
    python -m brightsmith.infra.lineage status
    python -m brightsmith.infra.lineage history <job_name>
    python -m brightsmith.infra.lineage graph
    python -m brightsmith.infra.lineage generate-docs
    python -m brightsmith.infra.lineage verify --spec <spec_name>
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from brightsmith.config import CATALOG_PATH, PROJECT_NAME, PROJECT_ROOT
from brightsmith.infra.iceberg_setup import get_catalog, get_or_create_table

logger = logging.getLogger(__name__)

# Governance warehouse (separate from zone warehouses)
GOVERNANCE_WAREHOUSE = PROJECT_ROOT / "data" / "governance" / "iceberg_warehouse"

LINEAGE_EVENTS_SCHEMA = Schema(
    NestedField(field_id=1, name="event_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="run_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="event_type", field_type=StringType(), required=True),
    NestedField(field_id=4, name="job_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="job_namespace", field_type=StringType(), required=True),
    NestedField(field_id=6, name="producer", field_type=StringType(), required=True),
    NestedField(field_id=7, name="input_tables", field_type=StringType(), required=True),
    NestedField(field_id=8, name="output_table", field_type=StringType(), required=True),
    NestedField(field_id=9, name="output_snapshot_id", field_type=LongType(), required=False),
    NestedField(field_id=10, name="row_count", field_type=IntegerType(), required=False),
    NestedField(field_id=11, name="skipped_duplicates", field_type=IntegerType(), required=False),
    NestedField(field_id=12, name="dq_rules_passed", field_type=IntegerType(), required=False),
    NestedField(field_id=13, name="dq_rules_total", field_type=IntegerType(), required=False),
    NestedField(field_id=14, name="dq_p0_passed", field_type=BooleanType(), required=False),
    NestedField(field_id=15, name="duration_ms", field_type=IntegerType(), required=False),
    NestedField(field_id=16, name="error_message", field_type=StringType(), required=False),
    NestedField(field_id=17, name="event_time", field_type=TimestamptzType(), required=True),
    # Schema evolution — new nullable fields for lineage maturity
    NestedField(field_id=18, name="spec_reference", field_type=StringType(), required=False),
    NestedField(field_id=19, name="agent_id", field_type=StringType(), required=False),
    NestedField(field_id=20, name="transformation_steps", field_type=StringType(), required=False),
)


# ---------------------------------------------------------------------------
# Column-level lineage
# ---------------------------------------------------------------------------


@dataclass
class ColumnMapping:
    """Maps one output column to its source columns and transformation."""

    target_field: str
    input_fields: list[dict] = field(default_factory=list)
    """Each entry: {"namespace": "...", "name": "table", "field": "col"}."""
    transformation_type: str = "DIRECT"
    """DIRECT | AGGREGATION | DERIVED."""
    transformation_description: str | None = None


def build_column_lineage(mappings: list[ColumnMapping]) -> dict:
    """Build an OpenLineage columnLineage facet from column mappings.

    Returns a dict suitable for embedding in an OpenLineage output facet.
    """
    fields = {}
    for m in mappings:
        entry: dict = {
            "inputFields": m.input_fields,
            "transformationType": m.transformation_type,
        }
        if m.transformation_description:
            entry["transformationDescription"] = m.transformation_description
        fields[m.target_field] = entry
    return {"fields": fields}


# ---------------------------------------------------------------------------
# Iceberg table management
# ---------------------------------------------------------------------------


def _get_lineage_table():
    """Lazily create and return the governance.lineage_events table."""
    catalog = get_catalog(GOVERNANCE_WAREHOUSE, CATALOG_PATH)
    return get_or_create_table(catalog, "governance", "lineage_events", LINEAGE_EVENTS_SCHEMA)


def _write_event(record: dict) -> None:
    """Write a single event record to the lineage_events table."""
    table = _get_lineage_table()
    from pyiceberg.io.pyarrow import schema_to_pyarrow

    arrow_schema = schema_to_pyarrow(table.schema())
    columns = {}
    for f in table.schema().fields:
        columns[f.name] = [record.get(f.name)]
    arrow_table = pa.table(columns, schema=arrow_schema)
    table.append(arrow_table)


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def emit_start(
    job_name: str,
    input_tables: list[str],
    output_table: str,
    producer: str,
    spec_reference: str | None = None,
    agent_id: str | None = None,
) -> str:
    """Emit a START event. Returns run_id for pairing with COMPLETE/FAIL.

    Fault-tolerant: logs a warning and returns run_id even if write fails.
    """
    run_id = str(uuid.uuid4())
    try:
        _write_event({
            "event_id": str(uuid.uuid4()),
            "run_id": run_id,
            "event_type": "START",
            "job_name": job_name,
            "job_namespace": PROJECT_NAME,
            "producer": producer,
            "input_tables": json.dumps(input_tables),
            "output_table": output_table,
            "output_snapshot_id": None,
            "row_count": None,
            "skipped_duplicates": None,
            "dq_rules_passed": None,
            "dq_rules_total": None,
            "dq_p0_passed": None,
            "duration_ms": None,
            "error_message": None,
            "event_time": datetime.now(timezone.utc),
            "spec_reference": spec_reference,
            "agent_id": agent_id,
            "transformation_steps": None,
        })
        logger.debug("Lineage START emitted for %s (run_id=%s)", job_name, run_id)
    except Exception:
        logger.warning("Failed to emit lineage START for %s", job_name, exc_info=True)
    return run_id


def emit_complete(
    run_id: str,
    job_name: str,
    output_table: str,
    producer: str,
    snapshot_id: int | None = None,
    row_count: int | None = None,
    skipped_duplicates: int | None = None,
    dq_passed: int | None = None,
    dq_total: int | None = None,
    dq_p0_passed: bool | None = None,
    duration_ms: int | None = None,
    transformation_steps: list[dict] | None = None,
) -> None:
    """Emit a COMPLETE event. Fault-tolerant: logs warning on failure."""
    try:
        _write_event({
            "event_id": str(uuid.uuid4()),
            "run_id": run_id,
            "event_type": "COMPLETE",
            "job_name": job_name,
            "job_namespace": PROJECT_NAME,
            "producer": producer,
            "input_tables": "[]",
            "output_table": output_table,
            "output_snapshot_id": snapshot_id,
            "row_count": row_count,
            "skipped_duplicates": skipped_duplicates,
            "dq_rules_passed": dq_passed,
            "dq_rules_total": dq_total,
            "dq_p0_passed": dq_p0_passed,
            "duration_ms": duration_ms,
            "error_message": None,
            "event_time": datetime.now(timezone.utc),
            "spec_reference": None,
            "agent_id": None,
            "transformation_steps": json.dumps(transformation_steps) if transformation_steps else None,
        })
        logger.debug("Lineage COMPLETE emitted for %s (run_id=%s)", job_name, run_id)
    except Exception:
        logger.warning("Failed to emit lineage COMPLETE for %s", job_name, exc_info=True)


def emit_fail(
    run_id: str,
    job_name: str,
    output_table: str,
    producer: str,
    error_message: str,
    duration_ms: int | None = None,
) -> None:
    """Emit a FAIL event. Fault-tolerant: logs warning on failure."""
    try:
        _write_event({
            "event_id": str(uuid.uuid4()),
            "run_id": run_id,
            "event_type": "FAIL",
            "job_name": job_name,
            "job_namespace": PROJECT_NAME,
            "producer": producer,
            "input_tables": "[]",
            "output_table": output_table,
            "output_snapshot_id": None,
            "row_count": None,
            "skipped_duplicates": None,
            "dq_rules_passed": None,
            "dq_rules_total": None,
            "dq_p0_passed": None,
            "duration_ms": duration_ms,
            "error_message": error_message[:4000] if error_message else None,
            "event_time": datetime.now(timezone.utc),
            "spec_reference": None,
            "agent_id": None,
            "transformation_steps": None,
        })
        logger.debug("Lineage FAIL emitted for %s (run_id=%s)", job_name, run_id)
    except Exception:
        logger.warning("Failed to emit lineage FAIL for %s", job_name, exc_info=True)


# ---------------------------------------------------------------------------
# Query utilities
# ---------------------------------------------------------------------------


def _read_all_events() -> list[dict]:
    """Read all lineage events from the Iceberg table."""
    import duckdb

    try:
        table = _get_lineage_table()
        arrow_table = table.scan().to_arrow()
        if arrow_table.num_rows == 0:
            return []
        con = duckdb.connect()
        rows = con.sql("SELECT * FROM arrow_table ORDER BY event_time DESC").fetchall()
        columns = [f.name for f in table.schema().fields]
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        logger.warning("Could not read lineage events", exc_info=True)
        return []


def query_lineage_events(
    table_name: str,
    event_type: str = "COMPLETE",
    limit: int = 10,
) -> list[dict]:
    """Query lineage events for a specific output table.

    Returns events sorted by event_time descending.
    """
    import duckdb

    try:
        table = _get_lineage_table()
        arrow_table = table.scan().to_arrow()
        if arrow_table.num_rows == 0:
            return []
        con = duckdb.connect()
        rows = con.sql("""
            SELECT *
            FROM arrow_table
            WHERE output_table = $1
              AND event_type = $2
            ORDER BY event_time DESC
            LIMIT $3
        """, params=[table_name, event_type, limit]).fetchall()
        columns = [f.name for f in table.schema().fields]
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        logger.warning("Could not query lineage events for %s", table_name, exc_info=True)
        return []


def query_downstream_consumers(
    table_name: str,
    limit: int = 100,
) -> list[dict]:
    """Find lineage events where table_name appears as an input.

    This is the reverse of query_lineage_events — it finds downstream
    consumers of a table rather than events that produced it.

    Returns events sorted by event_time descending.
    """
    import duckdb

    try:
        table = _get_lineage_table()
        arrow_table = table.scan().to_arrow()
        if arrow_table.num_rows == 0:
            return []
        con = duckdb.connect()
        # Search for table_name in the JSON array string of input_tables
        pattern = f'%"{table_name}"%'
        rows = con.sql("""
            SELECT *
            FROM arrow_table
            WHERE input_tables LIKE $1
              AND event_type = 'START'
            ORDER BY event_time DESC
            LIMIT $2
        """, params=[pattern, limit]).fetchall()
        columns = [f.name for f in table.schema().fields]
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        logger.warning("Could not query downstream consumers for %s", table_name, exc_info=True)
        return []


def _job_name_to_slug(job_name: str) -> str:
    """Convert a job_name to a filename slug.

    base.financial_facts -> base-financial-facts
    ingest:my_source -> ingest-my_source
    """
    return job_name.replace(".", "-").replace(":", "-")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_status() -> None:
    """Show the latest event per job_name."""
    import duckdb

    try:
        table = _get_lineage_table()
        arrow_table = table.scan().to_arrow()
    except Exception as e:
        print(f"No lineage events table found: {e}")
        return

    if arrow_table.num_rows == 0:
        print("No lineage events recorded yet.")
        return

    con = duckdb.connect()
    rows = con.sql("""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY job_name ORDER BY event_time DESC) AS rn
            FROM arrow_table
            WHERE event_type IN ('COMPLETE', 'FAIL')
        )
        SELECT job_name, event_time, row_count, duration_ms, event_type, error_message
        FROM ranked
        WHERE rn = 1
        ORDER BY job_name
    """).fetchall()

    if not rows:
        print("No COMPLETE or FAIL events recorded yet.")
        return

    print(f"{'Job':<40} {'Last Run':<22} {'Rows':>8} {'Duration':>10} {'Status':<10}")
    print("-" * 94)
    for row in rows:
        job_name, event_time, row_count, duration_ms, event_type, error_msg = row
        time_str = event_time.strftime("%Y-%m-%d %H:%M:%S") if event_time else "N/A"
        rows_str = str(row_count) if row_count is not None else "N/A"
        dur_str = f"{duration_ms}ms" if duration_ms is not None else "N/A"
        status = event_type
        if event_type == "FAIL" and error_msg:
            status = f"FAIL: {error_msg[:30]}"
        print(f"{job_name:<40} {time_str:<22} {rows_str:>8} {dur_str:>10} {status:<10}")


def cmd_history(job_name: str) -> None:
    """Show all events for a specific job_name."""
    import duckdb

    try:
        table = _get_lineage_table()
        arrow_table = table.scan().to_arrow()
    except Exception as e:
        print(f"No lineage events table found: {e}")
        return

    if arrow_table.num_rows == 0:
        print("No lineage events recorded yet.")
        return

    con = duckdb.connect()
    rows = con.sql(f"""
        SELECT event_type, event_time, row_count, duration_ms, run_id,
               output_snapshot_id, dq_rules_passed, dq_rules_total, error_message
        FROM arrow_table
        WHERE job_name = '{job_name}'
        ORDER BY event_time DESC
    """).fetchall()

    if not rows:
        print(f"No events found for job: {job_name}")
        return

    print(f"History for: {job_name}")
    print(f"{'Type':<10} {'Time':<22} {'Rows':>8} {'Duration':>10} {'DQ':>8} {'Run ID':<38}")
    print("-" * 100)
    for row in rows:
        event_type, event_time, row_count, duration_ms, run_id, snap_id, dq_pass, dq_total, err = row
        time_str = event_time.strftime("%Y-%m-%d %H:%M:%S") if event_time else "N/A"
        rows_str = str(row_count) if row_count is not None else ""
        dur_str = f"{duration_ms}ms" if duration_ms is not None else ""
        dq_str = f"{dq_pass}/{dq_total}" if dq_total else ""
        print(f"{event_type:<10} {time_str:<22} {rows_str:>8} {dur_str:>10} {dq_str:>8} {run_id:<38}")


def cmd_graph() -> None:
    """Show table dependency graph from lineage events."""
    import duckdb

    try:
        table = _get_lineage_table()
        arrow_table = table.scan().to_arrow()
    except Exception as e:
        print(f"No lineage events table found: {e}")
        return

    if arrow_table.num_rows == 0:
        print("No lineage events recorded yet.")
        return

    con = duckdb.connect()
    rows = con.sql("""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY job_name ORDER BY event_time DESC) AS rn
            FROM arrow_table
            WHERE event_type = 'START'
        )
        SELECT job_name, input_tables, output_table
        FROM ranked
        WHERE rn = 1
        ORDER BY job_name
    """).fetchall()

    if not rows:
        print("No lineage graph available.")
        return

    # Build adjacency: output -> [(input, job_name)]
    edges: dict[str, list[tuple[str, str]]] = {}
    all_tables: set[str] = set()

    for job_name, input_tables_json, output_table in rows:
        inputs = json.loads(input_tables_json) if input_tables_json else []
        all_tables.add(output_table)
        for inp in inputs:
            all_tables.add(inp)
            edges.setdefault(inp, []).append((output_table, job_name))

    # Find roots (tables that are inputs but not outputs of anything)
    outputs = {out for job_name, _, out in rows}
    roots = sorted(all_tables - outputs)
    if not roots:
        roots = sorted(all_tables)[:3]

    def _print_tree(table: str, indent: int = 0, visited: set | None = None) -> None:
        if visited is None:
            visited = set()
        if table in visited:
            print(f"{'  ' * indent}{table} (cycle)")
            return
        visited.add(table)
        print(f"{'  ' * indent}{table}")
        for target, job in sorted(edges.get(table, [])):
            print(f"{'  ' * indent}  └─→ {target} ({job})")
            _print_tree(target, indent + 2, visited)

    for root in roots:
        _print_tree(root)
        print()


def cmd_generate_docs() -> None:
    """Generate governance/lineage/*.json from runtime lineage events."""
    import duckdb

    try:
        table = _get_lineage_table()
        arrow_table = table.scan().to_arrow()
    except Exception as e:
        print(f"No lineage events table found: {e}")
        return

    if arrow_table.num_rows == 0:
        print("No lineage events to generate docs from.")
        return

    con = duckdb.connect()

    # Get latest COMPLETE event per job, plus the matching START for input_tables
    complete_rows = con.sql("""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY job_name ORDER BY event_time DESC) AS rn
            FROM arrow_table
            WHERE event_type = 'COMPLETE'
        )
        SELECT * FROM ranked WHERE rn = 1
    """).fetchall()
    complete_columns = [desc[0] for desc in con.description]

    start_rows = con.sql("""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY job_name ORDER BY event_time DESC) AS rn
            FROM arrow_table
            WHERE event_type = 'START'
        )
        SELECT * FROM ranked WHERE rn = 1
    """).fetchall()
    start_columns = [desc[0] for desc in con.description]

    # Build start event lookup by job_name
    start_lookup: dict[str, dict] = {}
    for row in start_rows:
        event = dict(zip(start_columns, row))
        start_lookup[event["job_name"]] = event

    lineage_dir = PROJECT_ROOT / "governance" / "lineage"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    generated = 0

    for row in complete_rows:
        event = dict(zip(complete_columns, row))
        job_name = event["job_name"]
        start_event = start_lookup.get(job_name, {})
        input_tables_json = start_event.get("input_tables", "[]")

        # Build OpenLineage doc
        time_str = event["event_time"].strftime("%Y-%m-%dT%H:%M:%SZ") if event.get("event_time") else None

        run_facets: dict = {}

        # Spec reference facet
        spec_ref = event.get("spec_reference") or start_event.get("spec_reference")
        if spec_ref:
            run_facets["brightsmith_specReference"] = {"specFile": spec_ref}

        # Agent attribution facet
        agent = event.get("agent_id") or start_event.get("agent_id")
        if agent:
            run_facets["brightsmith_agentAttribution"] = {"agentId": agent}

        # DQ facet
        if event.get("dq_rules_total"):
            dq_facet: dict = {
                "rulesPassed": event["dq_rules_passed"],
                "rulesTotal": event["dq_rules_total"],
                "p0Passed": event.get("dq_p0_passed"),
            }
            # Try to find the DQ rules file
            spec_slug = _job_name_to_slug(job_name)
            dq_rules_path = PROJECT_ROOT / "governance" / "dq-rules" / f"{spec_slug}.json"
            if dq_rules_path.exists():
                dq_facet["rulesFile"] = f"governance/dq-rules/{spec_slug}.json"
            run_facets["brightsmith_dataQuality"] = dq_facet

        # Runtime metrics facet
        run_facets["brightsmith_runtimeMetrics"] = {
            "lastRunId": event.get("run_id"),
            "lastEventTime": time_str,
            "rowCount": event.get("row_count"),
            "snapshotId": event.get("output_snapshot_id"),
            "durationMs": event.get("duration_ms"),
            "skippedDuplicates": event.get("skipped_duplicates"),
        }

        # Job facets
        job_facets: dict = {
            "documentation": {"description": f"Transformation: {job_name}"},
            "sourceCode": {"sourceCodeLocation": event.get("producer", "")},
        }

        # Transformation steps facet
        steps_json = event.get("transformation_steps")
        if steps_json:
            try:
                steps = json.loads(steps_json)
                job_facets["brightsmith_transformationDetail"] = {"steps": steps}
            except (json.JSONDecodeError, TypeError):
                pass

        # Build inputs from START event
        inputs = []
        try:
            input_table_names = json.loads(input_tables_json)
            for inp_name in input_table_names:
                inputs.append({
                    "namespace": PROJECT_NAME,
                    "name": inp_name,
                })
        except (json.JSONDecodeError, TypeError):
            pass

        # Build output with schema if available
        output_facets: dict = {}
        try:
            from brightsmith.config import WAREHOUSE_PATH
            catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
            iceberg_table = catalog.load_table(event["output_table"])
            schema_fields = [
                {"name": f.name, "type": str(f.field_type)}
                for f in iceberg_table.schema().fields
            ]
            output_facets["schema"] = {"fields": schema_fields}
        except Exception:
            pass

        # Merge column lineage if file exists
        col_lineage_path = lineage_dir / f"{_job_name_to_slug(job_name)}-columns.json"
        if col_lineage_path.exists():
            try:
                col_lineage = json.loads(col_lineage_path.read_text())
                output_facets["columnLineage"] = col_lineage
            except Exception:
                pass

        doc = {
            "eventType": "COMPLETE",
            "eventTime": time_str,
            "run": {
                "runId": event.get("run_id", ""),
                "facets": run_facets,
            },
            "job": {
                "namespace": PROJECT_NAME,
                "name": job_name,
                "facets": job_facets,
            },
            "inputs": inputs,
            "outputs": [
                {
                    "namespace": PROJECT_NAME,
                    "name": event["output_table"],
                    "facets": output_facets,
                }
            ],
            "producer": event.get("producer", ""),
        }

        filename = f"{_job_name_to_slug(job_name)}.json"
        filepath = lineage_dir / filename
        filepath.write_text(json.dumps(doc, indent=2, default=str) + "\n")
        generated += 1
        print(f"  Generated {filename}")

    print(f"\nGenerated {generated} lineage doc(s) in governance/lineage/")


def cmd_verify(spec_name: str) -> int:
    """Verify lineage completeness for a spec. Returns 0 if passed, 1 if failed."""
    import duckdb

    print(f"Lineage verification for spec: {spec_name}")
    print("-" * 50)

    checks: list[tuple[str, bool, str]] = []  # (label, passed, detail)

    try:
        table = _get_lineage_table()
        arrow_table = table.scan().to_arrow()
    except Exception:
        print("[FAIL] Cannot access lineage_events table")
        return 1

    if arrow_table.num_rows == 0:
        print("[FAIL] No lineage events recorded")
        return 1

    con = duckdb.connect()

    # Find events matching this spec (by job_name containing spec_name, or by spec_reference)
    events = con.sql(f"""
        SELECT *
        FROM arrow_table
        WHERE job_name LIKE '%{spec_name}%'
           OR (spec_reference IS NOT NULL AND spec_reference LIKE '%{spec_name}%')
        ORDER BY event_time DESC
    """).fetchall()
    event_columns = [desc[0] for desc in con.description]
    events_dicts = [dict(zip(event_columns, row)) for row in events]

    # Check 1: Events exist
    if not events_dicts:
        checks.append(("Events exist", False, "No events found"))
    else:
        complete_count = sum(1 for e in events_dicts if e["event_type"] == "COMPLETE")
        start_count = sum(1 for e in events_dicts if e["event_type"] == "START")
        checks.append(("Events exist", True, f"{len(events_dicts)} events ({start_count} START, {complete_count} COMPLETE)"))

    # Get latest COMPLETE event
    latest_complete = next((e for e in events_dicts if e["event_type"] == "COMPLETE"), None)

    if latest_complete:
        # Check 2: Row count
        rc = latest_complete.get("row_count")
        if rc and rc > 0:
            checks.append(("Row count", True, f"{rc} rows"))
        else:
            checks.append(("Row count", False, f"row_count={rc}"))

        # Check 3: Snapshot ID
        snap = latest_complete.get("output_snapshot_id")
        if snap:
            checks.append(("Snapshot ID", True, str(snap)))
        else:
            checks.append(("Snapshot ID", None, "not recorded"))  # type: ignore[arg-type]

        # Check 4: DQ metrics
        dq_total = latest_complete.get("dq_rules_total")
        dq_passed = latest_complete.get("dq_rules_passed")
        if dq_total and dq_total > 0:
            checks.append(("DQ metrics", True, f"{dq_passed}/{dq_total} rules passed"))
        else:
            checks.append(("DQ metrics", None, "not recorded"))  # type: ignore[arg-type]

        # Check 5: Duration
        dur = latest_complete.get("duration_ms")
        if dur and dur > 0:
            checks.append(("Duration", True, f"{dur}ms"))
        else:
            checks.append(("Duration", None, "not recorded"))  # type: ignore[arg-type]

        # Check 6: No FAIL after last COMPLETE
        latest_event = events_dicts[0]
        if latest_event["event_type"] == "FAIL":
            checks.append(("No failures", False, f"Latest event is FAIL: {latest_event.get('error_message', '')[:50]}"))
        else:
            checks.append(("No failures", True, "latest event is COMPLETE"))
    else:
        checks.append(("Row count", False, "no COMPLETE event"))
        checks.append(("No failures", False, "no COMPLETE event"))

    # Check 7: Governance doc exists
    slug = _job_name_to_slug(spec_name)
    lineage_dir = PROJECT_ROOT / "governance" / "lineage"
    doc_path = lineage_dir / f"{slug}.json"
    if doc_path.exists():
        checks.append(("Governance doc", True, str(doc_path.name)))
    else:
        checks.append(("Governance doc", None, f"{slug}.json not found (run generate-docs)"))  # type: ignore[arg-type]

    # Print results
    p0_failed = False
    pass_count = 0
    warn_count = 0
    fail_count = 0

    for label, passed, detail in checks:
        if passed is True:
            print(f"[PASS] {label}: {detail}")
            pass_count += 1
        elif passed is False:
            print(f"[FAIL] {label}: {detail}")
            fail_count += 1
            if label in ("Events exist", "Row count", "No failures"):
                p0_failed = True
        else:
            print(f"[WARN] {label}: {detail}")
            warn_count += 1

    total = pass_count + warn_count + fail_count
    print(f"\nResult: {'FAIL' if p0_failed else 'PASS'} ({pass_count}/{total} passed", end="")
    if warn_count:
        print(f", {warn_count} warning(s)", end="")
    if fail_count:
        print(f", {fail_count} failure(s)", end="")
    print(")")

    return 1 if p0_failed else 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for lineage commands."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="lineage",
        description="Lineage event management for Brightsmith",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show latest event per job")
    hist_parser = subparsers.add_parser("history", help="Show event history for a job")
    hist_parser.add_argument("job_name", help="Job name to show history for")
    subparsers.add_parser("graph", help="Show table dependency graph")
    subparsers.add_parser("generate-docs", help="Generate governance/lineage/*.json from runtime data")
    verify_parser = subparsers.add_parser("verify", help="Verify lineage completeness for a spec")
    verify_parser.add_argument("--spec", required=True, help="Spec name to verify")

    args = parser.parse_args()

    commands = {
        "status": lambda: cmd_status(),
        "history": lambda: cmd_history(args.job_name),
        "graph": lambda: cmd_graph(),
        "generate-docs": lambda: cmd_generate_docs(),
        "verify": lambda: exit(cmd_verify(args.spec)),
    }

    commands[args.command]()


if __name__ == "__main__":
    main()
