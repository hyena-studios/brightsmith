"""Tests for the integration test harness — validates golden dataset comparison."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from brightsmith.infra.integration_test_harness import (
    GoldenRecord,
    PipelineTestHarness,
    ValidationResult,
)


@pytest.fixture
def golden_file(tmp_path):
    """Create a golden dataset JSON file."""
    data = {
        "spec": "test-spec",
        "table": "consumable.metrics",
        "source_description": "Test reference data",
        "records": [
            {
                "entity": "AAPL",
                "metric": "revenue",
                "period": "FY2010",
                "expected_value": 65225,
                "tolerance": 0.01,
                "tolerance_type": "relative",
                "source": "Apple 10-K",
            },
            {
                "entity": "MSFT",
                "metric": "revenue",
                "period": "FY2010",
                "expected_value": 62484,
                "tolerance": 100,
                "tolerance_type": "absolute",
                "source": "Microsoft 10-K",
            },
            {
                "entity": "GOOG",
                "metric": "revenue",
                "period": "FY2010",
                "expected_value": 29321,
                "tolerance": 0.01,
                "tolerance_type": "relative",
                "source": "Alphabet 10-K",
            },
        ],
    }
    path = tmp_path / "test-golden.json"
    path.write_text(json.dumps(data))
    return path


class TestLoadGoldenDataset:
    def test_loads_records(self, golden_file):
        harness = PipelineTestHarness(catalog=MagicMock())
        records = harness.load_golden_dataset(golden_file)
        assert len(records) == 3
        assert records[0].entity == "AAPL"
        assert records[0].expected_value == 65225.0
        assert records[0].tolerance_type == "relative"
        assert records[0].table == "consumable.metrics"

    def test_default_tolerance(self, tmp_path):
        data = {
            "table": "t.t",
            "records": [
                {"entity": "X", "metric": "m", "period": "p", "expected_value": 1},
            ],
        }
        path = tmp_path / "g.json"
        path.write_text(json.dumps(data))
        harness = PipelineTestHarness(catalog=MagicMock())
        records = harness.load_golden_dataset(path)
        assert records[0].tolerance == 0.01


class TestValidationResult:
    def test_all_match_when_empty(self):
        result = ValidationResult()
        assert result.all_match is True

    def test_not_match_with_mismatch(self):
        from brightsmith.infra.integration_test_harness import Mismatch
        r = GoldenRecord("A", "m", "p", 100, 0.01, "relative", "src", "t.t")
        result = ValidationResult(mismatches=[Mismatch(r, 200.0, 100.0, "too different")])
        assert result.all_match is False

    def test_summary_includes_mismatches(self):
        from brightsmith.infra.integration_test_harness import Mismatch
        r = GoldenRecord("AAPL", "revenue", "FY2010", 65225, 0.01, "relative", "10-K", "t.t")
        result = ValidationResult(
            matches=[],
            mismatches=[Mismatch(r, 20300.0, -44925.0, "outside tolerance")],
        )
        summary = result.summary()
        assert "AAPL" in summary
        assert "0/1" in summary


class TestWithinTolerance:
    def test_relative_within(self):
        assert PipelineTestHarness._within_tolerance(65200.0, 65225.0, 0.01, "relative") is True

    def test_relative_outside(self):
        assert PipelineTestHarness._within_tolerance(20300.0, 65225.0, 0.01, "relative") is False

    def test_absolute_within(self):
        assert PipelineTestHarness._within_tolerance(62500.0, 62484.0, 100, "absolute") is True

    def test_absolute_outside(self):
        assert PipelineTestHarness._within_tolerance(63000.0, 62484.0, 100, "absolute") is False

    def test_zero_expected_relative(self):
        assert PipelineTestHarness._within_tolerance(0.0, 0.0, 0.01, "relative") is True
        assert PipelineTestHarness._within_tolerance(1.0, 0.0, 0.01, "relative") is False
