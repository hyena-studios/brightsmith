"""Runtime lineage event emitter.

Emits OpenLineage-compatible START/COMPLETE/FAIL events to the
governance.lineage_events Iceberg table. Every promote function
calls these to create a runtime audit trail.

Usage:
    from brightsmith.infra.lineage import emit_start, emit_complete, emit_fail
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

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
)


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
    for field in table.schema().fields:
        columns[field.name] = [record.get(field.name)]
    arrow_table = pa.table(columns, schema=arrow_schema)
    table.append(arrow_table)


def emit_start(
    job_name: str,
    input_tables: list[str],
    output_table: str,
    producer: str,
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
        })
        logger.debug("Lineage FAIL emitted for %s (run_id=%s)", job_name, run_id)
    except Exception:
        logger.warning("Failed to emit lineage FAIL for %s", job_name, exc_info=True)
