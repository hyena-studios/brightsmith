"""DuckDB + Iceberg infrastructure utilities.

PyIceberg handles all writes (table creation, appends). DuckDB handles analytical
reads via the Arrow bridge pattern.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.types import DateType

from brightsmith import config

RELOCATE_COMMAND = "python -m brightsmith.infra.relocate --apply"


class WarehouseRelocationError(RuntimeError):
    """Raised when the Iceberg catalog points at metadata files baked with a
    project-root prefix that no longer exists on disk.

    This is the signature of a moved/cloned/containerized warehouse: the catalog
    rows survive the move but their absolute ``metadata_location`` paths point at
    the OLD location, so PyIceberg/DuckDB would otherwise read **silently empty**
    results. We raise loudly instead, naming the one-command repair.
    """


def _path_under(path: str, root: str) -> bool:
    """True if ``path`` is ``root`` or lives beneath it."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def detect_relocation(
    catalog_path: str | Path,
    project_root: str | Path | None = None,
    project_name: str | None = None,
) -> list[tuple[str, str, str]]:
    """Scan the SQLite catalog for relocated metadata rows.

    A row is "relocated" when its ``metadata_location`` is an ABSOLUTE path whose
    file does NOT exist on disk AND whose prefix is not under the current project
    root. Relative paths (resolved against CWD) and rows whose file exists are NOT
    flagged — this keeps fresh/in-place warehouses, new tables, and committed
    relative warehouses from raising.

    Returns the list of offending ``(namespace, table, metadata_location)`` rows.
    """
    root = str(Path(project_root).resolve()) if project_root is not None else str(Path(config.PROJECT_ROOT))
    name = project_name if project_name is not None else config.PROJECT_NAME

    db = Path(catalog_path)
    if not db.exists():
        return []

    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='iceberg_tables'"
        )
        if cur.fetchone() is None:
            return []
        rows = con.execute(
            "SELECT table_namespace, table_name, metadata_location "
            "FROM iceberg_tables WHERE catalog_name = ?",
            (name,),
        ).fetchall()
    except sqlite3.DatabaseError:
        # A catalog we cannot read is not a relocation signal.
        return []
    finally:
        con.close()

    offending: list[tuple[str, str, str]] = []
    for ns, table_name, meta_loc in rows:
        if not meta_loc or not os.path.isabs(meta_loc):
            continue  # relative paths resolve against CWD — not a relocation
        if os.path.exists(meta_loc):
            continue  # file is present — in-place, fine
        if not _path_under(meta_loc, root):
            offending.append((ns, table_name, meta_loc))
    return offending


def _baked_prefix(meta_loc: str) -> str:
    """Best-effort extraction of the stale project-root prefix from a path,
    for use in the error message. Falls back to the directory of the file."""
    marker = f"{os.sep}data{os.sep}"
    idx = meta_loc.find(marker)
    if idx != -1:
        return meta_loc[: idx + 1]
    return str(Path(meta_loc).parent)


def _relocation_message(offending: list[tuple[str, str, str]]) -> str:
    import brightsmith.config as cfg

    prefixes = sorted({_baked_prefix(loc) for _, _, loc in offending})
    sample = offending[0]
    return (
        f"Iceberg warehouse looks relocated: {len(offending)} catalog row(s) point at "
        f"metadata files that do not exist on disk.\n"
        f"  Baked prefix(es): {', '.join(prefixes)}\n"
        f"  Current project root: {cfg.PROJECT_ROOT}\n"
        f"  Example: {sample[0]}.{sample[1]} -> {sample[2]}\n"
        f"Reads would silently return EMPTY results. Repair the baked paths with:\n"
        f"    {RELOCATE_COMMAND}\n"
        f"(or commit relative paths with: python -m brightsmith.infra.relocate --relative)"
    )


def _assert_not_relocated(catalog_path: str | Path) -> None:
    """Raise :class:`WarehouseRelocationError` if ANY catalog row is relocated.

    Blanket health check used by the headless-runner preflight and the pipeline
    gate. ``get_catalog`` itself uses the narrower per-table guard below so that a
    single stale legacy table never blocks reads of healthy tables.
    """
    offending = detect_relocation(catalog_path)
    if offending:
        raise WarehouseRelocationError(_relocation_message(offending))


def _normalize_identifier(identifier: Any) -> tuple[str, str] | None:
    """Return ``(namespace, table_name)`` for a PyIceberg identifier, or None."""
    if isinstance(identifier, str):
        parts = identifier.split(".")
    elif isinstance(identifier, (tuple, list)):
        parts = [str(p) for p in identifier]
    else:
        return None
    if len(parts) < 2:
        return None
    return ".".join(parts[:-1]), parts[-1]


def _assert_table_not_relocated(catalog_path: str | Path, identifier: Any) -> None:
    """Raise if the SPECIFIC table's metadata file is missing because of a foreign
    baked prefix. Narrow by design: only the table being loaded is checked."""
    norm = _normalize_identifier(identifier)
    if norm is None:
        return
    namespace, table_name = norm

    import brightsmith.config as cfg

    root = str(Path(cfg.PROJECT_ROOT))
    db = Path(catalog_path)
    if not db.exists():
        return
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='iceberg_tables'"
        )
        if cur.fetchone() is None:
            return
        row = con.execute(
            "SELECT metadata_location FROM iceberg_tables "
            "WHERE catalog_name = ? AND table_namespace = ? AND table_name = ?",
            (config.PROJECT_NAME, namespace, table_name),
        ).fetchone()
    except sqlite3.DatabaseError:
        return
    finally:
        con.close()

    if not row:
        return
    meta_loc = row[0]
    if not meta_loc or not os.path.isabs(meta_loc):
        return  # relative — resolves against CWD
    if os.path.exists(meta_loc):
        return  # present — fine
    if _path_under(meta_loc, root):
        return  # missing but correctly rooted — not a relocation
    raise WarehouseRelocationError(_relocation_message([(namespace, table_name, meta_loc)]))


def get_catalog(warehouse_path: str | Path, catalog_path: str | Path) -> SqlCatalog:
    """Return a PyIceberg SqlCatalog backed by SQLite.

    Creates the catalog DB and warehouse directory if they don't exist.

    The returned catalog's ``load_table`` is guarded: loading a table whose
    ``metadata_location`` points at a file that does not exist because its baked
    prefix differs from the current project root raises
    :class:`WarehouseRelocationError` (with the exact ``relocate`` command) rather
    than letting the scan return silently-empty results. The guard is per-table —
    healthy tables and brand-new tables load normally even if some other row in
    the catalog is stale.
    """
    warehouse_path = Path(warehouse_path).resolve()
    catalog_path = Path(catalog_path).resolve()
    warehouse_path.mkdir(parents=True, exist_ok=True)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)

    catalog = SqlCatalog(
        config.PROJECT_NAME,
        **{
            "uri": f"sqlite:///{catalog_path}",
            "warehouse": str(warehouse_path),
        },
    )

    _orig_load_table = catalog.load_table

    def _guarded_load_table(identifier, *args, **kwargs):
        _assert_table_not_relocated(catalog_path, identifier)
        return _orig_load_table(identifier, *args, **kwargs)

    catalog.load_table = _guarded_load_table  # type: ignore[method-assign]
    return catalog


def get_or_create_table(catalog: SqlCatalog, namespace: str, table_name: str, schema: Schema) -> Table:
    """Get an existing Iceberg table or create it, creating the namespace if needed."""
    try:
        catalog.create_namespace(namespace)
    except NamespaceAlreadyExistsError:
        pass

    identifier = f"{namespace}.{table_name}"
    try:
        return catalog.create_table(identifier, schema=schema)
    except TableAlreadyExistsError:
        return catalog.load_table(identifier)


def append_data(table: Table, records: list[dict], strict: bool = True) -> int:
    """Append records to an Iceberg table. Returns the new snapshot ID.

    Args:
        table: Target Iceberg table.
        records: Records to append.
        strict: When True (default), raise ``ValueError`` if any record carries a
            key that matches no schema field. This catches misspelled columns,
            which would otherwise be silently dropped (and the schema field they
            were meant to populate silently filled with ``None``). Pass
            ``strict=False`` for the legacy lax behaviour (extra keys ignored).
    """
    iceberg_schema = table.schema()
    field_names = {f.name for f in iceberg_schema.fields}
    if strict:
        for r in records:
            unknown = set(r) - field_names
            if unknown:
                raise ValueError(
                    f"append_data: record contains key(s) not in the table schema: "
                    f"{sorted(unknown)}. Schema fields: {sorted(field_names)}. "
                    f"This usually means a misspelled column. Pass strict=False to "
                    f"ignore extra keys."
                )
    date_fields = {f.name for f in iceberg_schema.fields if isinstance(f.field_type, DateType)}
    columns = {}
    for field in iceberg_schema.fields:
        values = [r.get(field.name) for r in records]
        if field.name in date_fields:
            values = [datetime.date.fromisoformat(v) if isinstance(v, str) else v for v in values]
        columns[field.name] = values

    arrow_schema = schema_to_pyarrow(iceberg_schema)
    arrow_table = pa.table(columns, schema=arrow_schema)
    table.append(arrow_table)
    table.refresh()
    return list(table.snapshots())[-1].snapshot_id


def read_with_duckdb(
    table: Table,
    snapshot_id: int | None = None,
) -> list[dict]:
    """Read an Iceberg table via PyIceberg scan → Arrow → DuckDB."""
    if snapshot_id is not None:
        arrow_table = table.scan(snapshot_id=snapshot_id).to_arrow()  # noqa: F841 — DuckDB resolves this from local scope
    else:
        arrow_table = table.scan().to_arrow()  # noqa: F841 — DuckDB resolves this from local scope

    con = duckdb.connect()
    result = con.sql("SELECT * FROM arrow_table").fetchall()
    columns = [field.name for field in table.schema().fields]
    return [dict(zip(columns, row, strict=False)) for row in result]


def filter_existing_records(
    table: Table,
    records: list[dict],
    id_field: str = "record_id",
) -> tuple[list[dict], int]:
    """Filter out records that already exist in the Iceberg table.

    Uses DuckDB anti-join for scalability.

    Returns:
        (new_records, skipped_count)
    """
    if not records:
        return [], 0

    # In-batch dedup FIRST: the same id_field value appearing twice in one call
    # must append exactly once. The anti-join below only compares against rows
    # ALREADY in the table, so without this two in-batch duplicates would both
    # survive and violate grain uniqueness at write time.
    deduped: list[dict] = []
    seen_ids: set = set()
    for r in records:
        rid = r.get(id_field)
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        deduped.append(r)

    existing_arrow = table.scan(selected_fields=(id_field,)).to_arrow()

    new_arrow = pa.Table.from_pylist(deduped)
    con = duckdb.connect()
    con.register("new_records", new_arrow)
    con.register("existing_ids", existing_arrow)

    result = con.execute(f"""
        SELECT n.*
        FROM new_records n
        LEFT JOIN existing_ids e ON n.{id_field} = e.{id_field}
        WHERE e.{id_field} IS NULL
    """).to_arrow_table()
    con.close()

    new_records = result.to_pylist()
    skipped = len(records) - len(new_records)
    return new_records, skipped


def get_snapshots(table: Table) -> list[dict]:
    """Return snapshot metadata for the table."""
    table.refresh()
    snapshots = []
    for s in table.snapshots():
        snapshots.append({
            "snapshot_id": s.snapshot_id,
            "timestamp_ms": s.timestamp_ms,
            "parent_snapshot_id": s.parent_snapshot_id,
            "operation": s.summary.operation.value if s.summary else None,
        })
    return snapshots
