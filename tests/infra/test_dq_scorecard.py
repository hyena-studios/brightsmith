"""Tests for the DQ scorecard generator.

Validates scorecard generation from execution results including
pass/fail formatting, category summaries, and gating status.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from brightsmith.infra.dq_scorecard import generate_scorecard


@pytest.fixture
def mock_rules_dir(tmp_path):
    """Create a temp DQ rules dir with rule metadata."""
    rules_dir = tmp_path / "dq-rules"
    rules_dir.mkdir()
    data = {
        "spec": "test-spec",
        "tables": ["test.table"],
        "rules": [
            {
                "rule_id": "TEST-001",
                "category": "Validity",
                "priority": "P0",
                "description": "No nulls",
                "sql": "SELECT 0",
                "threshold": "result = 0",
                "status": "active",
            },
            {
                "rule_id": "TEST-002",
                "category": "Completeness",
                "priority": "P1",
                "description": "Coverage check",
                "sql": "SELECT 0",
                "threshold": "result >= 25.0",
                "status": "active",
            },
            {
                "rule_id": "TEST-003",
                "category": "Validity",
                "priority": "P0",
                "description": "Range check",
                "sql": "SELECT 0",
                "threshold": "result = 0",
                "status": "active",
            },
        ],
    }
    (rules_dir / "test-spec.json").write_text(json.dumps(data, indent=2))
    return rules_dir


@pytest.fixture
def all_pass_results():
    """Run results where all rules pass."""
    return {
        "run_id": "abc123",
        "spec": "test-spec",
        "executed_at": "2026-03-14T12:00:00Z",
        "rules_total": 3,
        "rules_passed": 3,
        "rules_failed": 0,
        "p0_passed": True,
        "results": [
            {"rule_id": "TEST-001", "spec": "test-spec", "passed": True, "raw_value": 0, "threshold": "result = 0", "detail": "actual=0, threshold=result = 0", "violations": 0, "execution_time_ms": 5, "error": None, "executed_at": "2026-03-14T12:00:00Z"},
            {"rule_id": "TEST-002", "spec": "test-spec", "passed": True, "raw_value": 30.0, "threshold": "result >= 25.0", "detail": "actual=30.0, threshold=result >= 25.0", "violations": 0, "execution_time_ms": 10, "error": None, "executed_at": "2026-03-14T12:00:00Z"},
            {"rule_id": "TEST-003", "spec": "test-spec", "passed": True, "raw_value": 0, "threshold": "result = 0", "detail": "actual=0, threshold=result = 0", "violations": 0, "execution_time_ms": 3, "error": None, "executed_at": "2026-03-14T12:00:00Z"},
        ],
    }


@pytest.fixture
def mixed_results():
    """Run results with a P0 failure and P1 failure."""
    return {
        "run_id": "def456",
        "spec": "test-spec",
        "executed_at": "2026-03-14T12:00:00Z",
        "rules_total": 3,
        "rules_passed": 1,
        "rules_failed": 2,
        "p0_passed": False,
        "results": [
            {"rule_id": "TEST-001", "spec": "test-spec", "passed": False, "raw_value": 5, "threshold": "result = 0", "detail": "actual=5, threshold=result = 0", "violations": 5, "execution_time_ms": 5, "error": None, "executed_at": "2026-03-14T12:00:00Z"},
            {"rule_id": "TEST-002", "spec": "test-spec", "passed": False, "raw_value": 20.0, "threshold": "result >= 25.0", "detail": "actual=20.0, threshold=result >= 25.0", "violations": None, "execution_time_ms": 10, "error": None, "executed_at": "2026-03-14T12:00:00Z"},
            {"rule_id": "TEST-003", "spec": "test-spec", "passed": True, "raw_value": 0, "threshold": "result = 0", "detail": "actual=0, threshold=result = 0", "violations": 0, "execution_time_ms": 3, "error": None, "executed_at": "2026-03-14T12:00:00Z"},
        ],
    }


class TestScorecardGeneration:
    """generate_scorecard produces correct markdown from results."""

    def test_creates_scorecard_file(self, tmp_path, mock_rules_dir, all_pass_results):
        scorecards_dir = tmp_path / "scorecards"
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(all_pass_results, "test-spec")
            assert path.exists()
            assert path.name == "test-spec-scorecard.md"

    def test_header_shows_production_validation(self, tmp_path, mock_rules_dir, all_pass_results):
        scorecards_dir = tmp_path / "scorecards"
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(all_pass_results, "test-spec")
            content = path.read_text()
            assert "Production Data Validation" in content
            assert "Test-Based" not in content

    def test_all_pass_shows_100_percent(self, tmp_path, mock_rules_dir, all_pass_results):
        scorecards_dir = tmp_path / "scorecards"
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(all_pass_results, "test-spec")
            content = path.read_text()
            assert "3/3 rules passing (100%)" in content

    def test_all_pass_p0_gate_pass(self, tmp_path, mock_rules_dir, all_pass_results):
        scorecards_dir = tmp_path / "scorecards"
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(all_pass_results, "test-spec")
            content = path.read_text()
            assert "P0 Gate: PASS" in content

    def test_failures_listed(self, tmp_path, mock_rules_dir, mixed_results):
        scorecards_dir = tmp_path / "scorecards"
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(mixed_results, "test-spec")
            content = path.read_text()
            assert "Failures Requiring Action" in content
            assert "TEST-001" in content
            assert "TEST-002" in content

    def test_p0_gate_fail_on_p0_failure(self, tmp_path, mock_rules_dir, mixed_results):
        scorecards_dir = tmp_path / "scorecards"
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(mixed_results, "test-spec")
            content = path.read_text()
            assert "P0 Gate: FAIL" in content

    def test_category_summary_present(self, tmp_path, mock_rules_dir, all_pass_results):
        scorecards_dir = tmp_path / "scorecards"
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(all_pass_results, "test-spec")
            content = path.read_text()
            assert "Summary by Category" in content
            assert "Validity" in content
            assert "Completeness" in content

    def test_run_id_in_scorecard(self, tmp_path, mock_rules_dir, all_pass_results):
        scorecards_dir = tmp_path / "scorecards"
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(all_pass_results, "test-spec")
            content = path.read_text()
            assert "abc123" in content


class TestScorecardWithErrors:
    """Scorecard handles error results correctly."""

    def test_error_result_shows_error(self, tmp_path, mock_rules_dir):
        scorecards_dir = tmp_path / "scorecards"
        results = {
            "run_id": "err001",
            "spec": "test-spec",
            "executed_at": "2026-03-14T12:00:00Z",
            "rules_total": 1,
            "rules_passed": 0,
            "rules_failed": 1,
            "p0_passed": False,
            "results": [
                {"rule_id": "TEST-001", "spec": "test-spec", "passed": False, "raw_value": None, "threshold": "result = 0", "detail": None, "violations": None, "execution_time_ms": 1, "error": "Table not found: base.financial_facts", "executed_at": "2026-03-14T12:00:00Z"},
            ],
        }
        with patch("brightsmith.infra.dq_scorecard.DQ_RULES_DIR", mock_rules_dir), \
             patch("brightsmith.infra.dq_scorecard.DQ_SCORECARDS_DIR", scorecards_dir):
            path = generate_scorecard(results, "test-spec")
            content = path.read_text()
            assert "ERROR" in content
            assert "Table not found" in content
