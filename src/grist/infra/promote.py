"""Idempotent promote pattern for zone transformers.

Append-only dedup: promotes only records whose grain-based ID does not
already exist in the target table. Re-running with the same data produces
0 new rows. Re-running with new data appends only the delta.

Usage:
    from grist.infra.promote import promote

    result = promote(table, records, id_field="record_id")
    # {"promoted": 25, "skipped": 275, "snapshot_id": 12345}
"""

from __future__ import annotations

import logging

from pyiceberg.table import Table

from grist.infra.iceberg_setup import append_data, filter_existing_records

logger = logging.getLogger(__name__)


def promote(
    table: Table,
    records: list[dict],
    id_field: str = "record_id",
    spec_name: str = "",
    agent_name: str = "",
) -> dict:
    """Idempotent promote: append only records not already in the table.

    Uses deterministic grain-based IDs to detect existing rows via a
    DuckDB anti-join. Same input → same hashes → dedup skips them → 0 rows.

    Args:
        table: Target Iceberg table.
        records: Records to promote (must include id_field column).
        id_field: Column containing the deterministic grain ID.
        spec_name: For lineage/audit tracking.
        agent_name: For lineage/audit tracking.

    Returns:
        {"promoted": N, "skipped": M, "snapshot_id": X | None}
    """
    if not records:
        return {"promoted": 0, "skipped": 0, "snapshot_id": None}

    new_records, skipped = filter_existing_records(table, records, id_field)

    if not new_records:
        logger.info(
            "Promote %s: 0 new rows (all %d already exist)",
            spec_name or "unknown", skipped,
        )
        return {"promoted": 0, "skipped": skipped, "snapshot_id": None}

    snapshot_id = append_data(table, new_records)

    logger.info(
        "Promote %s: %d new rows, %d skipped (snapshot %s)",
        spec_name or "unknown", len(new_records), skipped, snapshot_id,
    )

    # Emit lineage if spec_name provided
    if spec_name:
        try:
            from grist.infra.lineage import emit_complete, emit_start

            table_name = f"{table.identifier[0]}.{table.identifier[1]}" if len(table.identifier) >= 2 else str(table.identifier)
            run_id = emit_start(
                job_name=f"promote:{spec_name}",
                input_tables=[],
                output_table=table_name,
                producer=agent_name or "promote",
            )
            emit_complete(
                run_id=run_id,
                job_name=f"promote:{spec_name}",
                output_table=table_name,
                producer=agent_name or "promote",
                snapshot_id=snapshot_id,
                row_count=len(new_records),
                skipped_duplicates=skipped,
            )
        except Exception:
            logger.warning("Failed to emit promote lineage for %s", spec_name, exc_info=True)

    return {"promoted": len(new_records), "skipped": skipped, "snapshot_id": snapshot_id}
