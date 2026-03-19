"""Tests for idempotent promote pattern."""

import tempfile
from pathlib import Path

from pyiceberg.schema import Schema
from pyiceberg.types import IntegerType, NestedField, StringType

from brightsmith.infra.grain import compute_grain_id
from brightsmith.infra.iceberg_setup import (
    append_data,
    filter_existing_records,
    get_catalog,
    get_or_create_table,
    read_with_duckdb,
)
from brightsmith.infra.promote import promote

SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="entity", field_type=StringType(), required=True),
    NestedField(field_id=3, name="value", field_type=IntegerType(), required=False),
)

GRAIN_FIELDS = ["entity"]


def _setup_table(tmp_dir: Path):
    """Create a fresh Iceberg table for testing."""
    warehouse = tmp_dir / "warehouse"
    catalog_path = tmp_dir / "catalog.db"
    catalog = get_catalog(warehouse, catalog_path)
    table = get_or_create_table(catalog, "test", "promote_test", SCHEMA)
    return table


def _make_records(entities: list[str], values: list[int]) -> list[dict]:
    """Create records with grain-based IDs."""
    records = []
    for entity, value in zip(entities, values):
        row = {"entity": entity, "value": value}
        row["record_id"] = compute_grain_id(row, GRAIN_FIELDS, prefix="PT")
        records.append(row)
    return records


def test_first_promote_appends_all():
    """Promoting to an empty table should append all records."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL", "MSFT", "GOOG"], [100, 200, 300])

        result = promote(table, records, id_field="record_id")
        assert result["promoted"] == 3
        assert result["skipped"] == 0
        assert result["snapshot_id"] is not None

        rows = read_with_duckdb(table)
        assert len(rows) == 3


def test_second_promote_skips_duplicates():
    """Re-promoting the same data should produce 0 new rows."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL", "MSFT"], [100, 200])

        result1 = promote(table, records, id_field="record_id")
        assert result1["promoted"] == 2

        result2 = promote(table, records, id_field="record_id")
        assert result2["promoted"] == 0
        assert result2["skipped"] == 2
        assert result2["snapshot_id"] is None

        rows = read_with_duckdb(table)
        assert len(rows) == 2


def test_promote_appends_only_new():
    """Mix of existing and new records should append only new."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        batch1 = _make_records(["AAPL", "MSFT"], [100, 200])
        promote(table, batch1, id_field="record_id")

        batch2 = _make_records(["AAPL", "MSFT", "GOOG"], [100, 200, 300])
        result = promote(table, batch2, id_field="record_id")
        assert result["promoted"] == 1  # only GOOG
        assert result["skipped"] == 2   # AAPL, MSFT

        rows = read_with_duckdb(table)
        assert len(rows) == 3


def test_promote_returns_snapshot_id():
    """Promote should return the Iceberg snapshot ID when rows are added."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL"], [100])

        result = promote(table, records, id_field="record_id")
        assert isinstance(result["snapshot_id"], int)


def test_promote_returns_skip_count():
    """Skip count should be accurate."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL", "MSFT", "GOOG"], [100, 200, 300])
        promote(table, records, id_field="record_id")

        result = promote(table, records, id_field="record_id")
        assert result["skipped"] == 3
        assert result["promoted"] == 0


def test_promote_empty_records():
    """Promoting empty list should return zeros."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        result = promote(table, [], id_field="record_id")
        assert result["promoted"] == 0
        assert result["skipped"] == 0
        assert result["snapshot_id"] is None


def test_filter_existing_records_column_selective():
    """filter_existing_records should work with the ID column only."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL"], [100])
        append_data(table, records)

        new_records, skipped = filter_existing_records(table, records, "record_id")
        assert skipped == 1
        assert len(new_records) == 0


def test_grain_ids_are_deterministic():
    """Same entity should produce the same record_id across runs."""
    r1 = _make_records(["AAPL"], [100])
    r2 = _make_records(["AAPL"], [100])
    assert r1[0]["record_id"] == r2[0]["record_id"]
