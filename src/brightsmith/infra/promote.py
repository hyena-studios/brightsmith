"""Idempotent promote pattern for zone transformers.

Grain-based dedup with conflict detection: a record whose grain id is new is
appended; a record whose grain id exists with the SAME payload is skipped;
a record whose grain id exists with a DIFFERENT payload is a conflict, handled
by ``on_conflict``. Re-running with the same data produces 0 new rows and 0
conflicts. A corrected value is never reported as a successful skip.

Usage:
    from brightsmith.infra.promote import promote

    result = promote(table, records, id_field="record_id")
    # {"promoted": 25, "skipped": 275, "conflicts": 0, "replaced": 0,
    #  "snapshot_id": 12345}
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Literal

import duckdb
import pyarrow as pa
from pyiceberg.expressions import In
from pyiceberg.table import Table

from brightsmith.infra.iceberg_setup import _require_ids, records_to_arrow

logger = logging.getLogger(__name__)

OnConflict = Literal["error", "skip", "replace"]

# Columns the framework stamps per run. A re-run with a fresh timestamp is
# still a no-op, so these never count toward a record's payload.
FRAMEWORK_FIELDS = frozenset({"promoted_at", "ingested_at", "load_date"})

# How many conflicting ids to name in an error message.
_SAMPLE = 5


class PromoteConflictError(ValueError):
    """Records arrived for existing grains with different values.

    Raised by :func:`promote` under the default ``on_conflict="error"``.
    ``conflicts`` holds the conflicting grain ids.
    """

    def __init__(self, table_name: str, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        sample = ", ".join(conflicts[:_SAMPLE])
        more = f" (+{len(conflicts) - _SAMPLE} more)" if len(conflicts) > _SAMPLE else ""
        super().__init__(
            f"promote into {table_name}: {len(conflicts)} record(s) already exist with "
            f"different values: {sample}{more}. A source restated a value, or the "
            f"transform changed. Pass on_conflict='replace' to overwrite them, "
            f"on_conflict='skip' to keep the stored values, or add a vintage column "
            f"to the grain if both versions should be kept."
        )


def promote(
    table: Table,
    records: list[dict],
    id_field: str = "record_id",
    spec_name: str = "",
    agent_name: str = "",
    on_conflict: OnConflict = "error",
    ignore_fields: tuple[str, ...] = (),
) -> dict:
    """Idempotent promote: append new grains, skip identical ones, surface changes.

    Args:
        table: Target Iceberg table.
        records: Records to promote (must include a non-null ``id_field``).
        id_field: Column containing the deterministic grain ID.
        spec_name: For lineage/audit tracking. When given, promote lineage is
            written and a lineage failure raises.
        agent_name: For lineage/audit tracking.
        on_conflict: What to do with a record whose grain exists with different
            values. ``"error"`` (default) raises :class:`PromoteConflictError`
            and writes nothing. ``"skip"`` keeps the stored values and counts the
            record under ``conflicts``. ``"replace"`` deletes the stored rows and
            appends the new ones in a single commit.
        ignore_fields: Extra columns excluded from the payload comparison, on top
            of ``id_field`` and :data:`FRAMEWORK_FIELDS`.

    Returns:
        ``{"promoted", "skipped", "conflicts", "replaced", "snapshot_id"}``.
        ``skipped`` counts identical records only; ``conflicts`` counts records
        whose values differ from the table (always 0 under ``"error"``).
    """
    if on_conflict not in ("error", "skip", "replace"):
        raise ValueError(f"on_conflict must be 'error', 'skip' or 'replace', not {on_conflict!r}")
    if not records:
        return _result(0, 0, 0, 0, None)
    _require_ids(records, id_field)

    table_name = ".".join(table.name())
    excluded = FRAMEWORK_FIELDS | {id_field, *ignore_fields}
    compared = [f.name for f in table.schema().fields if f.name not in excluded]

    # Coerce to the table's types before hashing, so a record compares exactly
    # as it would be written (ISO date strings, int widths, and so on).
    incoming = records_to_arrow(table, records)
    ids, digests = _incoming_digests(incoming, id_field, compared)
    stored = _stored_digests(table, incoming, id_field, compared)

    # One representative row per grain id: the first, or the last under
    # "replace" (the newest correction in the batch wins).
    rep: dict[str, int] = {}
    for i, rid in enumerate(ids):
        if rid not in rep or on_conflict == "replace":
            rep[rid] = i

    conflict_ids: set[str] = set()
    for i, rid in enumerate(ids):
        if digests[i] != digests[rep[rid]]:
            conflict_ids.add(rid)  # the batch disagrees with itself
    new_ids = [rid for rid in rep if rid not in stored]
    changed = [rid for rid in rep if rid in stored and stored[rid] != digests[rep[rid]]]
    conflict_ids.update(changed)

    if conflict_ids and on_conflict == "error":
        raise PromoteConflictError(table_name, sorted(conflict_ids))

    replaced = changed if on_conflict == "replace" else []
    write_idx = [rep[rid] for rid in new_ids] + [rep[rid] for rid in replaced]
    written = set(write_idx)
    # Skipped = input records whose values were already present (stored, or an
    # identical earlier copy in the batch). Records belonging to a conflicting
    # grain are counted under conflicts instead.
    skipped = sum(
        1
        for i, rid in enumerate(ids)
        if rid not in conflict_ids and i not in written
    )
    reported_conflicts = 0 if on_conflict == "error" else len(conflict_ids)

    if not write_idx:
        logger.info(
            "Promote %s: 0 new rows (%d identical, %d conflicting grain ids)",
            spec_name or table_name, skipped, reported_conflicts,
        )
        return _result(0, skipped, reported_conflicts, 0, None)

    arrow_rows = incoming.take(pa.array(write_idx, type=pa.int64()))
    with table.transaction() as tx:
        if replaced:
            tx.delete(_id_in(id_field, replaced))
        tx.append(arrow_rows)
    table.refresh()
    snapshot = table.current_snapshot()
    snapshot_id = snapshot.snapshot_id if snapshot else None

    logger.info(
        "Promote %s: %d new, %d replaced, %d identical, %d conflicting (snapshot %s)",
        spec_name or table_name, len(new_ids), len(replaced), skipped, reported_conflicts, snapshot_id,
    )

    if spec_name:
        _emit_promote_lineage(table, table_name, spec_name, agent_name, snapshot_id, len(write_idx), skipped)

    return _result(len(new_ids), skipped, reported_conflicts, len(replaced), snapshot_id)


def _result(promoted: int, skipped: int, conflicts: int, replaced: int, snapshot_id: int | None) -> dict:
    return {
        "promoted": promoted,
        "skipped": skipped,
        "conflicts": conflicts,
        "replaced": replaced,
        "snapshot_id": snapshot_id,
    }


def _id_in(id_field: str, ids: Iterable[str]) -> In:
    """``id_field IN ids``. PyIceberg's pydantic stubs hide the positional signature."""
    return In(id_field, ids)  # pyright: ignore[reportCallIssue]


def _digest_sql(compared: list[str]) -> str:
    """DuckDB expression hashing the compared columns of a row.

    Both sides are Arrow in the table's own schema, so a value renders the same
    way whether it came from the batch or from storage. ``struct_pack`` keeps
    column boundaries, so ``("ab", "c")`` and ``("a", "bc")`` differ.
    """
    if not compared:
        return "''"
    fields = ", ".join(f'{_q(c)} := {_q(c)}' for c in compared)
    return f"md5(CAST(struct_pack({fields}) AS VARCHAR))"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _incoming_digests(incoming: pa.Table, id_field: str, compared: list[str]) -> tuple[list, list[str]]:
    """Grain ids and payload digests of the batch, in input order."""
    with duckdb.connect() as con:
        con.register("incoming", incoming)
        rows = con.execute(f"SELECT {_q(id_field)}, {_digest_sql(compared)} FROM incoming").fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def _stored_digests(table: Table, incoming: pa.Table, id_field: str, compared: list[str]) -> dict:
    """``{id: digest}`` for stored rows whose id appears in the batch.

    The table is streamed batch by batch into a DuckDB semi-join, and only the
    id and digest of matching rows come back to Python. An unchanged re-run
    matches every id, so materializing those rows (as an earlier version of
    this function did) cost over a gigabyte on a 200k-row table.
    """
    if table.current_snapshot() is None:
        return {}
    stored = table.scan(selected_fields=(id_field, *compared)).to_arrow_batch_reader()
    with duckdb.connect() as con:
        con.register("incoming_ids", incoming.select([id_field]))
        con.register("stored", stored)
        rows = con.execute(
            f"SELECT {_q(id_field)}, {_digest_sql(compared)} FROM stored "
            f"WHERE {_q(id_field)} IN (SELECT {_q(id_field)} FROM incoming_ids)"
        ).fetchall()
    return dict(rows)


def _emit_promote_lineage(
    table: Table,
    table_name: str,
    spec_name: str,
    agent_name: str,
    snapshot_id: int | None,
    row_count: int,
    skipped: int,
) -> None:
    """Write promote START/COMPLETE events into the table's own catalog.

    Raises on failure: a caller that names a spec is asking for lineage, and a
    missing event is how the March–September 2026 lineage gap went unnoticed.
    """
    from brightsmith.infra.lineage import emit_complete, emit_start

    producer = agent_name or "promote"
    job = f"promote:{spec_name}"
    run_id = emit_start(
        job_name=job,
        input_tables=[],
        output_table=table_name,
        producer=producer,
        spec_reference=spec_name,
        agent_id=agent_name or None,
        catalog=table.catalog,
        strict=True,
    )
    emit_complete(
        run_id=run_id,
        job_name=job,
        output_table=table_name,
        producer=producer,
        snapshot_id=snapshot_id,
        row_count=row_count,
        skipped_duplicates=skipped,
        catalog=table.catalog,
        strict=True,
    )
