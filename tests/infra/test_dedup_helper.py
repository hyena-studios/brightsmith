"""Tests for filter_existing_records() — DuckDB anti-join dedup helper.

Validates that the helper correctly filters out records already present
in an Iceberg table using DuckDB anti-joins instead of Python sets.
"""

from __future__ import annotations


import pytest
from pyiceberg.schema import Schema
from pyiceberg.types import DoubleType, NestedField, StringType

from brightsmith.infra.iceberg_setup import (
    append_data,
    get_or_create_table,
    filter_existing_records,
    get_catalog,
)

SCHEMA = Schema(
    NestedField(1, "record_id", StringType(), required=False),
    NestedField(2, "company", StringType(), required=False),
    NestedField(3, "value", DoubleType(), required=False),
)

EXISTING_RECORDS = [
    {"record_id": "REC-001", "company": "AAPL", "value": 100.0},
    {"record_id": "REC-002", "company": "MSFT", "value": 200.0},
    {"record_id": "REC-003", "company": "GOOG", "value": 300.0},
]


@pytest.fixture
def dedup_env(tmp_path):
    """Create an Iceberg table with 3 existing records for dedup testing."""
    warehouse = tmp_path / "warehouse"
    catalog_db = tmp_path / "catalog.db"
    catalog = get_catalog(warehouse, catalog_db)
    table = get_or_create_table(catalog, "test_ns", "dedup_test", SCHEMA)
    append_data(table, EXISTING_RECORDS)
    return table


class TestFilterExistingRecords:
    """DuckDB anti-join dedup produces correct results."""

    def test_all_new_records_pass_through(self, dedup_env):
        """Records with IDs not in the table are all returned."""
        new_records = [
            {"record_id": "REC-100", "company": "AMZN", "value": 400.0},
            {"record_id": "REC-101", "company": "META", "value": 500.0},
        ]
        result, skipped = filter_existing_records(dedup_env, new_records)
        assert len(result) == 2
        assert skipped == 0
        assert {r["record_id"] for r in result} == {"REC-100", "REC-101"}

    def test_all_duplicate_records_filtered(self, dedup_env):
        """Records whose IDs already exist are all filtered out."""
        dupes = [
            {"record_id": "REC-001", "company": "AAPL", "value": 999.0},
            {"record_id": "REC-002", "company": "MSFT", "value": 999.0},
        ]
        result, skipped = filter_existing_records(dedup_env, dupes)
        assert len(result) == 0
        assert skipped == 2

    def test_mixed_new_and_duplicate(self, dedup_env):
        """Only new records survive when mixed with duplicates."""
        mixed = [
            {"record_id": "REC-001", "company": "AAPL", "value": 100.0},  # dupe
            {"record_id": "REC-NEW", "company": "TSLA", "value": 600.0},  # new
            {"record_id": "REC-003", "company": "GOOG", "value": 300.0},  # dupe
        ]
        result, skipped = filter_existing_records(dedup_env, mixed)
        assert len(result) == 1
        assert skipped == 2
        assert result[0]["record_id"] == "REC-NEW"

    def test_empty_input_returns_empty(self, dedup_env):
        """Empty input list returns empty output with zero skipped."""
        result, skipped = filter_existing_records(dedup_env, [])
        assert result == []
        assert skipped == 0

    def test_empty_table_passes_all(self, tmp_path):
        """When the Iceberg table is empty, all records pass through."""
        catalog = get_catalog(tmp_path / "wh", tmp_path / "cat.db")
        table = get_or_create_table(catalog, "test_ns", "empty_dedup", SCHEMA)
        # Append zero rows so the table has data files (empty scan works)
        new_records = [
            {"record_id": "REC-A", "company": "X", "value": 1.0},
            {"record_id": "REC-B", "company": "Y", "value": 2.0},
        ]
        result, skipped = filter_existing_records(table, new_records)
        assert len(result) == 2
        assert skipped == 0

    def test_custom_id_field(self, tmp_path):
        """Works with a custom ID field name (not record_id)."""
        custom_schema = Schema(
            NestedField(1, "fact_id", StringType(), required=False),
            NestedField(2, "metric", StringType(), required=False),
        )
        catalog = get_catalog(tmp_path / "wh", tmp_path / "cat.db")
        table = get_or_create_table(catalog, "test_ns", "custom_id", custom_schema)
        append_data(table, [
            {"fact_id": "F-001", "metric": "revenue"},
            {"fact_id": "F-002", "metric": "assets"},
        ])
        new_records = [
            {"fact_id": "F-001", "metric": "revenue"},  # dupe
            {"fact_id": "F-003", "metric": "income"},    # new
        ]
        result, skipped = filter_existing_records(table, new_records, id_field="fact_id")
        assert len(result) == 1
        assert skipped == 1
        assert result[0]["fact_id"] == "F-003"

    def test_preserves_all_columns(self, dedup_env):
        """Returned records retain all their original columns and values."""
        new_records = [
            {"record_id": "REC-X", "company": "NFLX", "value": 777.0},
        ]
        result, skipped = filter_existing_records(dedup_env, new_records)
        assert len(result) == 1
        rec = result[0]
        assert rec["record_id"] == "REC-X"
        assert rec["company"] == "NFLX"
        assert rec["value"] == 777.0

    def test_skipped_count_accurate(self, dedup_env):
        """skipped_count = input_count - output_count."""
        records = [
            {"record_id": "REC-001", "company": "A", "value": 1.0},  # dupe
            {"record_id": "REC-002", "company": "B", "value": 2.0},  # dupe
            {"record_id": "REC-003", "company": "C", "value": 3.0},  # dupe
            {"record_id": "REC-NEW1", "company": "D", "value": 4.0},
            {"record_id": "REC-NEW2", "company": "E", "value": 5.0},
        ]
        result, skipped = filter_existing_records(dedup_env, records)
        assert len(result) == 2
        assert skipped == 3
        assert skipped == len(records) - len(result)
