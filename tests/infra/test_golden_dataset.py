"""Tests for golden dataset tooling."""

import json

from pyiceberg.schema import Schema
from pyiceberg.types import DoubleType, NestedField, StringType

import brightsmith.config as config
from brightsmith.infra.golden_dataset import (
    VerificationResult,
    list_golden_datasets,
    load_golden_dataset,
    verify_golden_dataset,
)
from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table


def test_load_golden_dataset_exists(tmp_path):
    """Should load a golden dataset file that exists."""
    dataset = {
        "spec": "test-spec",
        "table": "consumable.metrics",
        "values": [
            {"description": "Apple revenue FY2010", "filters": {"entity": "AAPL"}, "column": "value", "expected_value": 65225}
        ],
    }
    (tmp_path / "test-spec-golden.json").write_text(json.dumps(dataset))

    result = load_golden_dataset("test-spec", golden_dir=tmp_path)
    assert result is not None
    assert result["spec"] == "test-spec"
    assert len(result["values"]) == 1


def test_load_golden_dataset_missing(tmp_path):
    """Should return None when golden dataset doesn't exist."""
    result = load_golden_dataset("nonexistent", golden_dir=tmp_path)
    assert result is None


def test_list_golden_datasets(tmp_path):
    """Should list all golden datasets with metadata."""
    for name in ["spec-a", "spec-b"]:
        dataset = {"spec": name, "table": "consumable.t", "values": [{"x": 1}, {"x": 2}]}
        (tmp_path / f"{name}-golden.json").write_text(json.dumps(dataset))

    datasets = list_golden_datasets(golden_dir=tmp_path)
    assert len(datasets) == 2
    assert datasets[0]["spec"] == "spec-a"
    assert datasets[0]["value_count"] == 2


def test_list_golden_datasets_empty(tmp_path):
    """Empty directory should return empty list."""
    datasets = list_golden_datasets(golden_dir=tmp_path)
    assert datasets == []


def test_missing_golden_dataset_detected(tmp_path):
    """Load should return None for specs without golden datasets."""
    result = load_golden_dataset("missing-spec", golden_dir=tmp_path)
    assert result is None


def test_verification_result_fields():
    """VerificationResult should store all expected fields."""
    r = VerificationResult(
        description="Test value",
        expected=100.0,
        actual=99.5,
        diff_pct=0.5,
        status="MATCH",
        filters={"entity": "AAPL"},
        column="value",
    )
    assert r.status == "MATCH"
    assert r.diff_pct == 0.5
    assert r.filters == {"entity": "AAPL"}


# --- verify_golden_dataset against a real tmp warehouse (M2.2) -------------

_METRICS_SCHEMA = Schema(
    NestedField(field_id=1, name="entity", field_type=StringType(), required=True),
    NestedField(field_id=2, name="value", field_type=DoubleType(), required=False),
)


def _setup_warehouse(tmp_path, monkeypatch):
    """Point config at a fresh tmp warehouse holding one known row (AAPL=100)."""
    warehouse = tmp_path / "warehouse"
    catalog_path = tmp_path / "catalog.db"
    monkeypatch.setattr(config, "WAREHOUSE_PATH", warehouse)
    monkeypatch.setattr(config, "CATALOG_PATH", catalog_path)

    catalog = get_catalog(warehouse, catalog_path)
    table = get_or_create_table(catalog, "consumable", "metrics", _METRICS_SCHEMA)
    append_data(table, [{"entity": "AAPL", "value": 100.0}])


def _write_golden(golden_dir, values):
    dataset = {"spec": "gd-spec", "table": "consumable.metrics", "values": values}
    (golden_dir / "gd-spec-golden.json").write_text(json.dumps(dataset))


def test_verify_match_close_mismatch_missing(tmp_path, monkeypatch):
    """Exercise every status bucket against the known AAPL=100 row."""
    _setup_warehouse(tmp_path, monkeypatch)
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    _write_golden(golden_dir, [
        {"description": "exact", "filters": {"entity": "AAPL"}, "column": "value", "expected_value": 100.0},
        {"description": "close", "filters": {"entity": "AAPL"}, "column": "value", "expected_value": 103.0},
        {"description": "off", "filters": {"entity": "AAPL"}, "column": "value", "expected_value": 150.0},
        {"description": "absent", "filters": {"entity": "ZZZZ"}, "column": "value", "expected_value": 100.0},
    ])

    results = verify_golden_dataset("gd-spec", golden_dir=golden_dir)
    by_desc = {r.description: r.status for r in results}
    assert by_desc == {
        "exact": "MATCH",       # 0% diff, within default 1% tolerance
        "close": "CLOSE",       # 2.9% diff: outside 1% tol but <= 5%
        "off": "MISMATCH",      # 33% diff
        "absent": "MISSING",    # no row matches the filter
    }


def test_verify_tolerance_override_promotes_close_to_match(tmp_path, monkeypatch):
    """A 5% tolerance override turns the 2.9%-off value from CLOSE into MATCH."""
    _setup_warehouse(tmp_path, monkeypatch)
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    _write_golden(golden_dir, [
        {"description": "close", "filters": {"entity": "AAPL"}, "column": "value", "expected_value": 103.0},
    ])

    results = verify_golden_dataset("gd-spec", golden_dir=golden_dir, tolerance_override=0.05)
    assert results[0].status == "MATCH"


def test_verify_missing_table_yields_all_missing(tmp_path, monkeypatch):
    """When the table can't be loaded, every value is MISSING (fails safe)."""
    # Point config at an empty warehouse; do not create the table.
    monkeypatch.setattr(config, "WAREHOUSE_PATH", tmp_path / "empty-wh")
    monkeypatch.setattr(config, "CATALOG_PATH", tmp_path / "empty.db")
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    _write_golden(golden_dir, [
        {"description": "v", "filters": {"entity": "AAPL"}, "column": "value", "expected_value": 100.0},
    ])

    results = verify_golden_dataset("gd-spec", golden_dir=golden_dir)
    assert len(results) == 1
    assert results[0].status == "MISSING"
