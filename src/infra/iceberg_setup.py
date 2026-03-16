"""DuckDB + Iceberg infrastructure utilities.

PyIceberg handles all writes (table creation, appends). DuckDB handles analytical
reads via the Arrow bridge pattern.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.types import DateType

from src.config import PROJECT_NAME


def get_catalog(warehouse_path: str | Path, catalog_path: str | Path) -> SqlCatalog:
    """Return a PyIceberg SqlCatalog backed by SQLite.

    Creates the catalog DB and warehouse directory if they don't exist.
    """
    warehouse_path = Path(warehouse_path).resolve()
    catalog_path = Path(catalog_path).resolve()
    warehouse_path.mkdir(parents=True, exist_ok=True)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)

    return SqlCatalog(
        PROJECT_NAME,
        **{
            "uri": f"sqlite:///{catalog_path}",
            "warehouse": str(warehouse_path),
        },
    )


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


def append_data(table: Table, records: list[dict]) -> int:
    """Append records to an Iceberg table. Returns the new snapshot ID."""
    iceberg_schema = table.schema()
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
        arrow_table = table.scan(snapshot_id=snapshot_id).to_arrow()
    else:
        arrow_table = table.scan().to_arrow()

    con = duckdb.connect()
    result = con.sql("SELECT * FROM arrow_table").fetchall()
    columns = [field.name for field in table.schema().fields]
    return [dict(zip(columns, row)) for row in result]


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

    existing_arrow = table.scan(selected_fields=(id_field,)).to_arrow()

    new_arrow = pa.Table.from_pylist(records)
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
