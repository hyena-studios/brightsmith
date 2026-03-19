"""Tests for golden dataset tooling."""

import json

from brightsmith.infra.golden_dataset import (
    VerificationResult,
    list_golden_datasets,
    load_golden_dataset,
)


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
