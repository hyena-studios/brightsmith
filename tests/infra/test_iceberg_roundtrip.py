"""End-to-end Iceberg roundtrip tests: create → write → snapshot → query → time travel.

Validates that PyIceberg can write Iceberg tables and DuckDB can read them
on local file storage with zero external infrastructure.
"""

from __future__ import annotations

import datetime
import shutil
import tempfile
from pathlib import Path

import pytest
from pyiceberg.schema import Schema
from pyiceberg.types import DateType, DoubleType, NestedField, StringType

from brightsmith.infra.iceberg_setup import (
    append_data,
    get_or_create_table,
    get_catalog,
    get_snapshots,
    read_with_duckdb,
)

TEST_SCHEMA = Schema(
    NestedField(1, "company_id", StringType(), required=False),
    NestedField(2, "metric_name", StringType(), required=False),
    NestedField(3, "value", DoubleType(), required=False),
    NestedField(4, "reporting_period", StringType(), required=False),
    NestedField(5, "filed_date", DateType(), required=False),
)

BATCH_1 = [
    {"company_id": "COMP_A", "metric_name": "revenue", "value": 1_000_000.0, "reporting_period": "Q3-2024", "filed_date": datetime.date(2024, 11, 1)},
    {"company_id": "COMP_B", "metric_name": "total_assets", "value": 5_000_000.0, "reporting_period": "Q3-2024", "filed_date": datetime.date(2024, 11, 1)},
    {"company_id": "COMP_C", "metric_name": "net_income", "value": 250_000.0, "reporting_period": "Q3-2024", "filed_date": datetime.date(2024, 11, 1)},
]

BATCH_2 = [
    {"company_id": "COMP_A", "metric_name": "revenue", "value": 1_100_000.0, "reporting_period": "Q3-2024", "filed_date": datetime.date(2024, 12, 15)},
    {"company_id": "COMP_B", "metric_name": "total_assets", "value": 5_200_000.0, "reporting_period": "Q3-2024", "filed_date": datetime.date(2024, 12, 15)},
]

BATCH_3 = [
    {"company_id": "COMP_D", "metric_name": "revenue", "value": 750_000.0, "reporting_period": "Q3-2024", "filed_date": datetime.date(2025, 1, 10)},
]


@pytest.fixture
def iceberg_env(tmp_path):
    """Set up a temporary Iceberg environment with 3 snapshots of test data."""
    warehouse = tmp_path / "warehouse"
    catalog_db = tmp_path / "catalog.db"

    catalog = get_catalog(warehouse, catalog_db)
    table = get_or_create_table(catalog, "test_db", "financial_facts_test", TEST_SCHEMA)

    snap1 = append_data(table, BATCH_1)
    snap2 = append_data(table, BATCH_2)
    snap3 = append_data(table, BATCH_3)

    return {
        "table": table,
        "catalog": catalog,
        "warehouse": warehouse,
        "catalog_db": catalog_db,
        "snap1": snap1,
        "snap2": snap2,
        "snap3": snap3,
    }


# --- Roundtrip integrity ---


class TestRoundtripIntegrity:
    """Data written via PyIceberg matches data read via DuckDB."""

    def test_current_state_has_all_rows(self, iceberg_env):
        rows = read_with_duckdb(iceberg_env["table"])
        assert len(rows) == 6

    def test_current_state_values_match(self, iceberg_env):
        rows = read_with_duckdb(iceberg_env["table"])
        company_ids = sorted(r["company_id"] for r in rows)
        assert company_ids == ["COMP_A", "COMP_A", "COMP_B", "COMP_B", "COMP_C", "COMP_D"]

    def test_field_types_preserved(self, iceberg_env):
        rows = read_with_duckdb(iceberg_env["table"])
        row = next(r for r in rows if r["company_id"] == "COMP_A" and r["value"] == 1_000_000.0)
        assert isinstance(row["company_id"], str)
        assert isinstance(row["value"], float)
        assert isinstance(row["reporting_period"], str)
        assert isinstance(row["filed_date"], datetime.date)


# --- Snapshot isolation ---


class TestSnapshotIsolation:
    """Querying snapshot N returns exactly the rows from snapshots 1..N."""

    def test_snapshot_1_has_3_rows(self, iceberg_env):
        rows = read_with_duckdb(iceberg_env["table"], snapshot_id=iceberg_env["snap1"])
        assert len(rows) == 3

    def test_snapshot_1_contains_only_original_companies(self, iceberg_env):
        rows = read_with_duckdb(iceberg_env["table"], snapshot_id=iceberg_env["snap1"])
        companies = sorted(r["company_id"] for r in rows)
        assert companies == ["COMP_A", "COMP_B", "COMP_C"]

    def test_snapshot_1_has_original_values_not_amendments(self, iceberg_env):
        rows = read_with_duckdb(iceberg_env["table"], snapshot_id=iceberg_env["snap1"])
        comp_a = next(r for r in rows if r["company_id"] == "COMP_A")
        assert comp_a["value"] == 1_000_000.0  # original, not the 1.1M amendment from snap2
        comp_b = next(r for r in rows if r["company_id"] == "COMP_B")
        assert comp_b["value"] == 5_000_000.0  # original, not the 5.2M amendment from snap2

    def test_snapshot_2_has_5_rows(self, iceberg_env):
        rows = read_with_duckdb(iceberg_env["table"], snapshot_id=iceberg_env["snap2"])
        assert len(rows) == 5

    def test_snapshot_2_contains_amendments(self, iceberg_env):
        rows = read_with_duckdb(iceberg_env["table"], snapshot_id=iceberg_env["snap2"])
        comp_a_values = sorted(r["value"] for r in rows if r["company_id"] == "COMP_A")
        assert comp_a_values == [1_000_000.0, 1_100_000.0]

    def test_snapshot_3_equals_current(self, iceberg_env):
        snap3_rows = read_with_duckdb(iceberg_env["table"], snapshot_id=iceberg_env["snap3"])
        current_rows = read_with_duckdb(iceberg_env["table"])
        assert len(snap3_rows) == len(current_rows) == 6


# --- Snapshot metadata ---


class TestSnapshotMetadata:
    """All snapshots have IDs, timestamps, and correct parent references."""

    def test_three_snapshots_exist(self, iceberg_env):
        snaps = get_snapshots(iceberg_env["table"])
        assert len(snaps) == 3

    def test_all_snapshots_have_ids_and_timestamps(self, iceberg_env):
        for snap in get_snapshots(iceberg_env["table"]):
            assert snap["snapshot_id"] is not None
            assert snap["timestamp_ms"] is not None
            assert snap["timestamp_ms"] > 0

    def test_parent_chain_is_correct(self, iceberg_env):
        snaps = get_snapshots(iceberg_env["table"])
        assert snaps[0]["parent_snapshot_id"] is None  # first snapshot has no parent
        assert snaps[1]["parent_snapshot_id"] == snaps[0]["snapshot_id"]
        assert snaps[2]["parent_snapshot_id"] == snaps[1]["snapshot_id"]

    def test_all_operations_are_append(self, iceberg_env):
        for snap in get_snapshots(iceberg_env["table"]):
            assert snap["operation"] == "append"


# --- Schema consistency ---


class TestSchemaConsistency:
    """Iceberg schema matches expected field names and types."""

    def test_field_names(self, iceberg_env):
        fields = [f.name for f in iceberg_env["table"].schema().fields]
        assert fields == ["company_id", "metric_name", "value", "reporting_period", "filed_date"]

    def test_field_count(self, iceberg_env):
        assert len(iceberg_env["table"].schema().fields) == 5


# --- Edge cases ---


class TestEdgeCases:
    """Edge cases: empty table, idempotency, non-existent snapshot."""

    def test_empty_table_query(self, tmp_path):
        catalog = get_catalog(tmp_path / "wh", tmp_path / "cat.db")
        table = get_or_create_table(catalog, "test_db", "empty_table", TEST_SCHEMA)
        rows = read_with_duckdb(table)
        assert rows == []

    def test_table_already_exists_returns_existing(self, iceberg_env):
        table2 = get_or_create_table(
            iceberg_env["catalog"], "test_db", "financial_facts_test", TEST_SCHEMA
        )
        rows = read_with_duckdb(table2)
        assert len(rows) == 6  # same data, not a new empty table

    def test_idempotent_setup(self, iceberg_env):
        """Running setup twice doesn't corrupt data."""
        snap_count_before = len(get_snapshots(iceberg_env["table"]))
        # Re-get catalog and table
        catalog2 = get_catalog(iceberg_env["warehouse"], iceberg_env["catalog_db"])
        table2 = get_or_create_table(catalog2, "test_db", "financial_facts_test", TEST_SCHEMA)
        snap_count_after = len(get_snapshots(table2))
        assert snap_count_after == snap_count_before

    def test_nonexistent_snapshot_raises_error(self, iceberg_env):
        with pytest.raises(ValueError, match="Snapshot not found"):
            read_with_duckdb(iceberg_env["table"], snapshot_id=9999999999)
