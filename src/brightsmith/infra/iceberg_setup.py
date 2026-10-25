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
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import (
    NamespaceAlreadyExistsError,
    NoSuchNamespaceError,
    NoSuchTableError,
    TableAlreadyExistsError,
)
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

    A row is "relocated" when its ``metadata_location`` is an ABSOLUTE path that
    lives under neither the current project root nor the catalog's own data
    directory. Whether the file exists is deliberately NOT consulted: a copied
    project whose original is still on disk has every metadata file present, and
    used to be waved through — then read and wrote the ORIGINAL warehouse
    silently (2026-09-08 audit §2 P0). Relative paths (resolved against CWD) are
    never flagged, so fresh, in-place, and committed-relative warehouses pass.

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

    roots = _allowed_roots(root, db)
    return [
        (ns, table_name, meta_loc)
        for ns, table_name, meta_loc in rows
        if _is_foreign(meta_loc, roots)
    ]


def _allowed_roots(project_root: str, catalog_path: Path, *extra: str | Path) -> list[str]:
    """Directories a correctly-rooted ``metadata_location`` may live under.

    The project root, the catalog's own directory, the configured data and
    governance warehouses, and any warehouse the caller is explicitly opening.
    A candidate that is the filesystem root or an ANCESTOR of the project root
    is dropped: it would admit a sibling copy of the project (a catalog at
    ``<root>/catalog.db`` once made ``<root>/..`` an allowed root) or switch the
    guard off entirely.
    """
    candidates = [
        project_root,
        Path(catalog_path).parent,
        config.WAREHOUSE_PATH,
        config.GOVERNANCE_WAREHOUSE,
        *extra,
    ]
    root = os.path.realpath(project_root)
    roots: list[str] = []
    for c in candidates:
        r = os.path.realpath(c)
        if r == os.sep or (r != root and _path_under(root, r)):
            continue
        roots.append(r)
    return roots


def _is_foreign(meta_loc: str | None, roots: list[str]) -> bool:
    """True if ``meta_loc`` points outside every allowed root.

    Relative paths resolve against the working directory (how committed
    relative warehouses work) and are foreign only if they climb out of it.
    """
    if not meta_loc:
        return False
    if not os.path.isabs(meta_loc):
        return os.path.normpath(meta_loc).split(os.sep)[0] == os.pardir
    resolved = os.path.realpath(meta_loc)
    return not any(_path_under(meta_loc, r) or _path_under(resolved, r) for r in roots)


def list_catalog_table_locations(
    catalog_path: str | Path,
    project_name: str | None = None,
) -> dict[tuple[str, str], str]:
    """Cheaply read every (namespace, table) -> metadata_location pair
    straight from the SQLite catalog backing store.

    A single lightweight SQL query — no PyIceberg ``Table`` construction, no
    ``metadata.json`` parsing, no relocation-guard overhead (mirrors
    :func:`detect_relocation`'s raw-SQLite read pattern). Intended as a cheap
    staleness signal (audit finding M6): a caller that caches Iceberg view
    registrations (e.g. ``BaseMCPServer``'s persistent query connection) can
    compare this dict against its last-seen snapshot to decide whether an
    expensive rebuild (which DOES need the guarded ``catalog.load_table()``
    path) is required, without paying that cost on every call.

    NOT a substitute for ``catalog.load_table()`` — this bypasses the
    relocation guard entirely, so the returned locations must only be used
    for comparison/staleness-detection, never treated as verified-safe paths
    to read from directly.

    Returns an empty dict if the catalog DB or its ``iceberg_tables`` table
    doesn't exist yet (fresh/empty project) — same tolerance as
    ``detect_relocation``.
    """
    name = project_name if project_name is not None else config.PROJECT_NAME
    db = Path(catalog_path)
    if not db.exists():
        return {}

    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='iceberg_tables'"
        )
        if cur.fetchone() is None:
            return {}
        rows = con.execute(
            "SELECT table_namespace, table_name, metadata_location "
            "FROM iceberg_tables WHERE catalog_name = ?",
            (name,),
        ).fetchall()
    except sqlite3.DatabaseError:
        # A catalog we cannot read yields no signal — same tolerance as
        # detect_relocation; the caller's caching layer treats this like a
        # fresh/empty catalog rather than raising.
        return {}
    finally:
        con.close()

    return {(ns, tbl): loc for ns, tbl, loc in rows if loc}


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
        f"metadata outside this project.\n"
        f"  Baked prefix(es): {', '.join(prefixes)}\n"
        f"  Current project root: {cfg.PROJECT_ROOT}\n"
        f"  Example: {sample[0]}.{sample[1]} -> {sample[2]}\n"
        f"Reads would silently return EMPTY results, or read and write another copy "
        f"of the warehouse. Repair the baked paths with:\n"
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


def _assert_table_not_relocated(
    catalog_path: str | Path,
    identifier: Any,
    warehouse_path: str | Path | None = None,
) -> None:
    """Raise if the SPECIFIC table's metadata path carries a foreign baked prefix,
    whether or not that file still exists. Narrow by design: only the table being
    loaded is checked."""
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
    extra = [warehouse_path] if warehouse_path is not None else []
    if _is_foreign(meta_loc, _allowed_roots(root, db, *extra)):
        raise WarehouseRelocationError(_relocation_message([(namespace, table_name, meta_loc)]))


# Process-wide catalog cache. Building a SqlCatalog spins up a SQLAlchemy engine
# with a SQLite connection pool; without caching, every governance read (and any
# other per-call get_catalog site) creates a fresh engine that is never disposed
# — a steady resource leak and needless per-call overhead. Keyed on the RESOLVED
# (warehouse, catalog, project_name) triple so a reconfigure that changes any of
# them yields a distinct catalog. Tests that reconfigure between cases should call
# reset_catalog_cache().
_CATALOG_CACHE: dict[tuple[str, str, str], SqlCatalog] = {}


def reset_catalog_cache() -> None:
    """Drop all cached catalogs (disposing their engines).

    Call this when the config's project name or warehouse/catalog paths change
    within a single process (chiefly in tests) so a later get_catalog does not
    hand back a catalog bound to the previous configuration.
    """
    for cat in _CATALOG_CACHE.values():
        engine = getattr(cat, "engine", None)
        if engine is not None:
            engine.dispose()
    _CATALOG_CACHE.clear()


def get_catalog(warehouse_path: str | Path, catalog_path: str | Path) -> SqlCatalog:
    """Return a PyIceberg SqlCatalog backed by SQLite (cached per config triple).

    Creates the catalog DB and warehouse directory if they don't exist. Repeated
    calls with the same resolved (warehouse, catalog, project_name) return the
    SAME catalog instance — see :data:`_CATALOG_CACHE` and
    :func:`reset_catalog_cache`.

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

    cache_key = (str(warehouse_path), str(catalog_path), config.PROJECT_NAME)
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return cached

    catalog = SqlCatalog(
        config.PROJECT_NAME,
        **{
            "uri": f"sqlite:///{catalog_path}",
            "warehouse": str(warehouse_path),
        },
    )

    _orig_load_table = catalog.load_table

    def _guarded_load_table(identifier, *args, **kwargs):
        _assert_table_not_relocated(catalog_path, identifier, warehouse_path)
        return _orig_load_table(identifier, *args, **kwargs)

    catalog.load_table = _guarded_load_table  # type: ignore[method-assign]
    _CATALOG_CACHE[cache_key] = catalog
    return catalog


def get_or_create_table(catalog: Catalog, namespace: str, table_name: str, schema: Schema) -> Table:
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


def load_table_if_exists(catalog: Catalog, namespace: str, table_name: str) -> Table | None:
    """Load a table, or return None if it (or its namespace) does not exist.

    The read-path counterpart of :func:`get_or_create_table`: status and
    validation commands use it so that looking at a catalog never adds tables
    to it (a read-only command once created ``governance.agent_activity`` in a
    consumer's committed catalog — 2026-09-08 audit §3).
    """
    try:
        return catalog.load_table(f"{namespace}.{table_name}")
    except (NoSuchTableError, NoSuchNamespaceError):
        return None


def add_missing_columns(table: Table, schema: Schema) -> Table:
    """Add any column in ``schema`` that ``table`` lacks (write path only).

    Lets a framework upgrade add an optional column to a table that an older
    version created, instead of failing every write with an unknown-key error.
    Runs only when a declared column is missing; ``union_by_name`` may then
    also apply a safe type promotion (e.g. int to long) to a declared column.
    """
    existing = {f.name for f in table.schema().fields}
    if all(f.name in existing for f in schema.fields):
        return table
    with table.update_schema() as update:
        update.union_by_name(schema)
    table.refresh()
    return table


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
    arrow_table = records_to_arrow(table, records, strict=strict)
    table.append(arrow_table)
    table.refresh()
    return list(table.snapshots())[-1].snapshot_id


def records_to_arrow(table: Table, records: list[dict], strict: bool = True) -> pa.Table:
    """Coerce ``records`` to an Arrow table in ``table``'s schema.

    Shared by :func:`append_data` and ``promote``'s conflict check, so a record is
    compared in exactly the types it would be written in (ISO date strings become
    ``date``, ints widen per the schema, and so on). See :func:`append_data` for
    ``strict``.
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
    return pa.table(columns, schema=arrow_schema)


def read_with_duckdb(
    table: Table,
    snapshot_id: int | None = None,
    columns: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Read an Iceberg table via PyIceberg scan → Arrow → DuckDB.

    Args:
        table: Iceberg table to read.
        snapshot_id: Optional snapshot to pin the read to (unchanged behavior).
        columns: Optional column projection. Passed to PyIceberg's
            ``scan(selected_fields=...)``, which prunes columns at the scan
            layer (not read off disk at all) rather than reading every column
            and discarding some in Python. ``None`` (default) reads every
            column — identical to the pre-existing behavior.
        limit: Optional row cap. Passed to PyIceberg's ``scan(limit=...)``,
            which stops the scan once satisfied rather than materializing the
            whole table and slicing in Python. ``None`` (default) reads every
            row — identical to the pre-existing behavior.

    Note: PyIceberg's ``selected_fields`` does NOT preserve the caller's
    column order — it returns fields in the table's schema order. The
    returned dicts' keys are therefore built from the Arrow result's actual
    schema (``arrow_table.schema.names``), not the ``columns`` argument, so
    keys always line up with values regardless of the order requested.
    """
    scan_kwargs: dict[str, Any] = {}
    if snapshot_id is not None:
        scan_kwargs["snapshot_id"] = snapshot_id
    if columns is not None:
        scan_kwargs["selected_fields"] = tuple(columns)
    if limit is not None:
        scan_kwargs["limit"] = limit

    arrow_table = table.scan(**scan_kwargs).to_arrow()  # noqa: F841 — DuckDB resolves this from local scope

    with duckdb.connect() as con:
        result = con.sql("SELECT * FROM arrow_table").fetchall()
    result_columns = arrow_table.schema.names
    return [dict(zip(result_columns, row, strict=False)) for row in result]


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
    _require_ids(records, id_field)

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
    with duckdb.connect() as con:
        con.register("new_records", new_arrow)
        con.register("existing_ids", existing_arrow)

        result = con.execute(f"""
            SELECT n.*
            FROM new_records n
            LEFT JOIN existing_ids e ON n.{id_field} = e.{id_field}
            WHERE e.{id_field} IS NULL
        """).to_arrow_table()

    new_records = result.to_pylist()
    skipped = len(records) - len(new_records)
    return new_records, skipped


def _require_ids(records: list[dict], id_field: str) -> None:
    """Reject records whose ``id_field`` is missing or null.

    SQL ``NULL = NULL`` never matches, so a null id defeats the anti-join and the
    same row is appended again on every run (2026-09-08 audit §2 P0).
    """
    missing = sum(1 for r in records if r.get(id_field) is None)
    if missing:
        raise ValueError(
            f"{missing} of {len(records)} record(s) have no '{id_field}'. Every derived "
            f"row needs a grain id: compute_grain_id(row, grain_fields, prefix)."
        )


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
