"""Tests for the DQ execution engine.

Validates threshold evaluation, rule loading, SQL execution, table reference
parsing, and the rule lifecycle (proposed -> approved -> active).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import duckdb
import pyarrow as pa
import pytest

from brightsmith.infra.dq_runner import (
    DQValidationError,
    _extract_table_refs,
    _rewrite_sql,
    approve_rules,
    evaluate_threshold,
    execute_sql_rule,
    load_rules,
    run_rules,
    validate_after_write,
)


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------


class TestEvaluateThreshold:
    """evaluate_threshold parses expressions and compares correctly."""

    def test_result_equals_zero_pass(self):
        passed, _ = evaluate_threshold(0, "result = 0")
        assert passed is True

    def test_result_equals_zero_fail(self):
        passed, _ = evaluate_threshold(5, "result = 0")
        assert passed is False

    def test_result_count_equals_zero_pass(self):
        passed, _ = evaluate_threshold(0, "result_count = 0")
        assert passed is True

    def test_result_count_equals_zero_fail(self):
        passed, _ = evaluate_threshold(3, "result_count = 0")
        assert passed is False

    def test_result_gte_pass(self):
        passed, _ = evaluate_threshold(30.0, "result >= 25.0")
        assert passed is True

    def test_result_gte_fail(self):
        passed, _ = evaluate_threshold(20.0, "result >= 25.0")
        assert passed is False

    def test_result_gte_boundary(self):
        passed, _ = evaluate_threshold(25.0, "result >= 25.0")
        assert passed is True

    def test_none_raw_result_treated_as_zero(self):
        passed, _ = evaluate_threshold(None, "result = 0")
        assert passed is True

    def test_threshold_with_suffix_stripped(self):
        """Thresholds like '100% -- zero violations' should parse the comparison from context."""
        # These don't have a parseable comparison -- they're implementation-only rules
        passed, detail = evaluate_threshold(0, "100% — zero violations")
        assert passed is False  # unparseable
        assert "unparseable" in detail

    def test_detail_includes_actual_value(self):
        _, detail = evaluate_threshold(5, "result = 0")
        assert "actual=5" in detail

    def test_double_equals(self):
        passed, _ = evaluate_threshold(0, "result == 0")
        assert passed is True

    def test_not_equals(self):
        passed, _ = evaluate_threshold(5, "result != 0")
        assert passed is True

    def test_less_than(self):
        passed, _ = evaluate_threshold(3, "result < 5")
        assert passed is True

    def test_greater_than(self):
        passed, _ = evaluate_threshold(10, "result > 5")
        assert passed is True


# ---------------------------------------------------------------------------
# Table reference extraction
# ---------------------------------------------------------------------------


class TestExtractTableRefs:
    """_extract_table_refs finds namespace.table patterns in SQL."""

    def test_single_table(self):
        refs = _extract_table_refs("SELECT * FROM silver.financial_facts")
        assert ("silver", "financial_facts") in refs

    def test_cross_namespace_join(self):
        sql = "SELECT * FROM bronze.xbrl_company_facts r JOIN silver.entity_mappings m ON r.cik = m.cik"
        refs = _extract_table_refs(sql)
        assert ("bronze", "xbrl_company_facts") in refs
        assert ("silver", "entity_mappings") in refs

    def test_skips_alias_column_refs(self):
        sql = "SELECT r.cik, m.status FROM bronze.xbrl_company_facts r JOIN silver.entity_mappings m ON r.cik = m.cik"
        refs = _extract_table_refs(sql)
        # r.cik and m.status should be skipped (not known namespaces)
        assert ("r", "cik") not in refs
        assert ("m", "status") not in refs
        # But real table refs should be found
        assert ("bronze", "xbrl_company_facts") in refs
        assert ("silver", "entity_mappings") in refs

    def test_deduplicates(self):
        sql = "SELECT * FROM silver.financial_facts f WHERE f.cik IN (SELECT cik FROM silver.financial_facts)"
        refs = _extract_table_refs(sql)
        assert refs.count(("silver", "financial_facts")) == 1

    def test_no_table_refs(self):
        refs = _extract_table_refs("SELECT 1")
        assert refs == []


# ---------------------------------------------------------------------------
# SQL rewriting
# ---------------------------------------------------------------------------


class TestRewriteSql:
    """_rewrite_sql replaces namespace.table with view names."""

    def test_single_replacement(self):
        sql = "SELECT * FROM silver.financial_facts"
        result = _rewrite_sql(sql, [("silver", "financial_facts")])
        assert result == "SELECT * FROM silver_financial_facts"

    def test_multiple_replacements(self):
        sql = "FROM bronze.xbrl_company_facts r JOIN silver.entity_mappings m"
        result = _rewrite_sql(sql, [("bronze", "xbrl_company_facts"), ("silver", "entity_mappings")])
        assert "bronze_xbrl_company_facts" in result
        assert "silver_entity_mappings" in result


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


class TestLoadRules:
    """load_rules reads JSON files and augments rule dicts."""

    def test_load_from_tmp_dir(self, tmp_path):
        """Load rules from a temporary directory."""
        rules_dir = tmp_path / "dq-rules"
        rules_dir.mkdir()
        data = {
            "spec": "test-spec",
            "tables": ["test.table"],
            "rules": [
                {
                    "rule_id": "TMP-001",
                    "category": "Validity",
                    "priority": "P0",
                    "description": "Test rule",
                    "sql": "SELECT 0",
                    "threshold": "result = 0",
                    "status": "active",
                },
                {
                    "rule_id": "TMP-002",
                    "category": "Completeness",
                    "priority": "P1",
                    "description": "Another test rule",
                    "sql": "SELECT 0",
                    "threshold": "result = 0",
                    "status": "proposed",
                },
            ],
        }
        (rules_dir / "test-spec.json").write_text(json.dumps(data, indent=2))

        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", rules_dir):
            rules = load_rules()
            assert len(rules) == 2
            for rule in rules:
                assert "spec" in rule
                assert rule["spec"] == "test-spec"
                assert "status" in rule
                assert rule["status"] in ("proposed", "approved", "active")

    def test_filter_by_spec(self, tmp_path):
        """Filter rules by spec name."""
        rules_dir = tmp_path / "dq-rules"
        rules_dir.mkdir()
        data1 = {
            "spec": "spec-a",
            "tables": ["test.a"],
            "rules": [{"rule_id": "A-001", "category": "Validity", "priority": "P0",
                        "description": "Rule A", "sql": "SELECT 0", "threshold": "result = 0", "status": "active"}],
        }
        data2 = {
            "spec": "spec-b",
            "tables": ["test.b"],
            "rules": [{"rule_id": "B-001", "category": "Validity", "priority": "P0",
                        "description": "Rule B", "sql": "SELECT 0", "threshold": "result = 0", "status": "active"}],
        }
        (rules_dir / "spec-a.json").write_text(json.dumps(data1, indent=2))
        (rules_dir / "spec-b.json").write_text(json.dumps(data2, indent=2))

        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", rules_dir):
            rules = load_rules(spec="spec-a")
            assert len(rules) == 1
            assert all(r["spec"] == "spec-a" for r in rules)

    def test_filter_nonexistent_spec(self):
        rules = load_rules(spec="nonexistent-spec")
        assert rules == []


# ---------------------------------------------------------------------------
# SQL execution against in-memory DuckDB
# ---------------------------------------------------------------------------


class TestExecuteSqlRule:
    """execute_sql_rule runs SQL and evaluates thresholds."""

    @pytest.fixture
    def con_with_data(self):
        """DuckDB connection with a test view registered."""
        con = duckdb.connect()
        con.execute("""
            CREATE TABLE silver_financial_facts AS
            SELECT * FROM (VALUES
                ('FACT-001', 'CIK001', 'revenue', 1000.0, 1, false, NULL),
                ('FACT-002', 'CIK001', 'assets', 5000.0, 2, false, NULL),
                ('FACT-003', 'CIK002', 'revenue', 2000.0, 1, true, 'ACC-999')
            ) AS t(fact_id, cik, concept, value, calendar_quarter, is_superseded, superseded_by)
        """)
        yield con
        con.close()

    def test_passing_rule(self, con_with_data):
        rule = {
            "rule_id": "TEST-001",
            "sql": "SELECT COUNT(*) FROM silver_financial_facts WHERE calendar_quarter < 1 OR calendar_quarter > 4",
            "threshold": "result = 0",
            "spec": "test",
        }
        result = execute_sql_rule(rule, con_with_data)
        assert result["passed"] is True
        assert result["raw_value"] == 0
        assert result["error"] is None

    def test_failing_rule(self, con_with_data):
        rule = {
            "rule_id": "TEST-002",
            "sql": "SELECT COUNT(*) FROM silver_financial_facts WHERE is_superseded = true AND superseded_by IS NULL",
            "threshold": "result = 0",
            "spec": "test",
        }
        result = execute_sql_rule(rule, con_with_data)
        # Our test data has superseded_by set for the superseded row, so this should pass
        assert result["passed"] is True

    def test_result_count_threshold(self, con_with_data):
        rule = {
            "rule_id": "TEST-003",
            "sql": "SELECT fact_id, COUNT(*) FROM silver_financial_facts GROUP BY fact_id HAVING COUNT(*) > 1",
            "threshold": "result_count = 0",
            "spec": "test",
        }
        result = execute_sql_rule(rule, con_with_data)
        assert result["passed"] is True  # no duplicates
        assert result["raw_value"] == 0

    def test_sql_error_captured(self, con_with_data):
        rule = {
            "rule_id": "TEST-ERR",
            "sql": "SELECT * FROM nonexistent_table",
            "threshold": "result = 0",
            "spec": "test",
        }
        result = execute_sql_rule(rule, con_with_data)
        assert result["passed"] is False
        assert result["error"] is not None
        assert "nonexistent_table" in result["error"]

    def test_execution_time_recorded(self, con_with_data):
        rule = {
            "rule_id": "TEST-TIME",
            "sql": "SELECT COUNT(*) FROM silver_financial_facts",
            "threshold": "result >= 0",
            "spec": "test",
        }
        result = execute_sql_rule(rule, con_with_data)
        assert result["execution_time_ms"] >= 0

    def test_result_has_all_fields(self, con_with_data):
        rule = {
            "rule_id": "TEST-FIELDS",
            "sql": "SELECT 0",
            "threshold": "result = 0",
            "spec": "test",
        }
        result = execute_sql_rule(rule, con_with_data)
        expected_keys = {"rule_id", "spec", "category", "passed", "raw_value", "threshold", "detail", "violations", "execution_time_ms", "error", "executed_at"}
        assert expected_keys == set(result.keys())


# ---------------------------------------------------------------------------
# Rule approval
# ---------------------------------------------------------------------------


class TestApproveRules:
    """approve_rules transitions proposed rules to approved."""

    @pytest.fixture
    def temp_rules_dir(self, tmp_path):
        """Create a temp DQ rules dir with a proposed rule."""
        rules_dir = tmp_path / "dq-rules"
        rules_dir.mkdir()
        data = {
            "spec": "test-spec",
            "tables": ["test.table"],
            "rules": [
                {
                    "rule_id": "TEST-PROP-001",
                    "category": "Validity",
                    "priority": "P0",
                    "description": "Test proposed rule",
                    "sql": "SELECT 0",
                    "threshold": "result = 0",
                    "status": "proposed",
                    "proposed_by": "@dq-engineer",
                    "proposed_at": "2026-03-14T00:00:00Z",
                },
                {
                    "rule_id": "TEST-ACT-001",
                    "category": "Validity",
                    "priority": "P0",
                    "description": "Already active rule",
                    "sql": "SELECT 0",
                    "threshold": "result = 0",
                    "status": "active",
                },
            ],
        }
        (rules_dir / "test-spec.json").write_text(json.dumps(data, indent=2))
        return rules_dir

    def test_approve_proposed_rule(self, temp_rules_dir):
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", temp_rules_dir):
            results = approve_rules(["TEST-PROP-001"])
            assert results[0]["status"] == "approved"

            # Verify file was updated
            data = json.loads((temp_rules_dir / "test-spec.json").read_text())
            rule = next(r for r in data["rules"] if r["rule_id"] == "TEST-PROP-001")
            assert rule["status"] == "approved"
            assert rule["approved_by"] == "human"
            assert "approved_at" in rule

    def test_approve_already_active_no_change(self, temp_rules_dir):
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", temp_rules_dir):
            results = approve_rules(["TEST-ACT-001"])
            assert "not proposed" in results[0].get("message", "")

    def test_approve_nonexistent_rule(self, temp_rules_dir):
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", temp_rules_dir):
            results = approve_rules(["NOPE-001"])
            assert results[0]["status"] == "not_found"


# ---------------------------------------------------------------------------
# Integration: run_rules end-to-end with temp Iceberg tables
# ---------------------------------------------------------------------------


class TestRunRulesIntegration:
    """run_rules executes the full chain: JSON -> Iceberg -> DuckDB -> results."""

    @pytest.fixture
    def iceberg_env(self, tmp_path):
        """Create a temp Iceberg catalog with test tables and DQ rules."""
        from pyiceberg.schema import Schema
        from pyiceberg.types import DateType, DoubleType, IntegerType, NestedField, StringType

        from brightsmith.infra.iceberg_setup import append_data, get_or_create_table, get_catalog

        warehouse = tmp_path / "warehouse"
        catalog_db = tmp_path / "catalog.db"
        catalog = get_catalog(warehouse, catalog_db)

        # Create silver.test_facts table
        schema = Schema(
            NestedField(1, "fact_id", StringType(), required=True),
            NestedField(2, "cik", IntegerType(), required=True),
            NestedField(3, "val", DoubleType(), required=True),
            NestedField(4, "filed_date", DateType(), required=True),
        )
        table = get_or_create_table(catalog, "silver", "test_facts", schema)

        import datetime
        append_data(table, [
            {"fact_id": "F1", "cik": 1, "val": 100.0, "filed_date": datetime.date(2024, 3, 15)},
            {"fact_id": "F2", "cik": 1, "val": 200.0, "filed_date": datetime.date(2024, 6, 15)},
            {"fact_id": "F3", "cik": 2, "val": 300.0, "filed_date": datetime.date(2024, 9, 15)},
            {"fact_id": "F1", "cik": 1, "val": 100.0, "filed_date": datetime.date(2024, 3, 15)},  # duplicate
        ])

        # Write DQ rules JSON
        rules_dir = tmp_path / "dq-rules"
        rules_dir.mkdir()
        results_dir = tmp_path / "dq-results"
        results_dir.mkdir()

        rules = {
            "spec": "test-integration",
            "tables": ["silver.test_facts"],
            "rules": [
                {
                    "rule_id": "INT-001",
                    "category": "Validity",
                    "priority": "P0",
                    "description": "No future filed_dates",
                    "sql": "SELECT COUNT(*) FROM silver.test_facts WHERE filed_date > '2030-01-01'",
                    "threshold": "result = 0",
                    "status": "active",
                },
                {
                    "rule_id": "INT-002",
                    "category": "Uniqueness",
                    "priority": "P0",
                    "description": "No duplicate fact_ids",
                    "sql": "SELECT fact_id, COUNT(*) FROM silver.test_facts GROUP BY fact_id HAVING COUNT(*) > 1",
                    "threshold": "result_count = 0",
                    "status": "active",
                },
                {
                    "rule_id": "INT-003",
                    "category": "Completeness",
                    "priority": "P1",
                    "description": "All vals positive",
                    "sql": "SELECT COUNT(*) FROM silver.test_facts WHERE val <= 0",
                    "threshold": "result = 0",
                    "status": "active",
                },
                {
                    "rule_id": "INT-SKIP",
                    "category": "Validity",
                    "priority": "P0",
                    "description": "Proposed rule -- should not execute",
                    "sql": "SELECT 1/0",
                    "threshold": "result = 0",
                    "status": "proposed",
                },
            ],
        }
        (rules_dir / "test-integration.json").write_text(json.dumps(rules, indent=2))

        return {
            "catalog": catalog,
            "rules_dir": rules_dir,
            "results_dir": results_dir,
        }

    def test_run_rules_returns_all_active_results(self, iceberg_env):
        """run_rules executes active/approved rules and returns results."""
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", iceberg_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", iceberg_env["results_dir"]):
            result = run_rules(spec="test-integration", catalog=iceberg_env["catalog"])

        assert result["rules_total"] == 3  # INT-SKIP is proposed, excluded
        assert result["run_id"] is not None
        assert result["executed_at"] is not None

    def test_run_rules_detects_duplicate_fact_ids(self, iceberg_env):
        """INT-002 (uniqueness) should fail -- we inserted a duplicate F1."""
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", iceberg_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", iceberg_env["results_dir"]):
            result = run_rules(spec="test-integration", catalog=iceberg_env["catalog"])

        int002 = next(r for r in result["results"] if r["rule_id"] == "INT-002")
        assert int002["passed"] is False
        assert int002["raw_value"] == 1  # one duplicate fact_id

    def test_run_rules_passes_valid_rules(self, iceberg_env):
        """INT-001 (no future dates) and INT-003 (positive vals) should pass."""
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", iceberg_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", iceberg_env["results_dir"]):
            result = run_rules(spec="test-integration", catalog=iceberg_env["catalog"])

        int001 = next(r for r in result["results"] if r["rule_id"] == "INT-001")
        assert int001["passed"] is True

        int003 = next(r for r in result["results"] if r["rule_id"] == "INT-003")
        assert int003["passed"] is True

    def test_run_rules_p0_gate_fails_on_duplicate(self, iceberg_env):
        """P0 gate should fail because INT-002 (P0 uniqueness) failed."""
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", iceberg_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", iceberg_env["results_dir"]):
            result = run_rules(spec="test-integration", catalog=iceberg_env["catalog"])

        assert result["p0_passed"] is False

    def test_run_rules_skips_proposed(self, iceberg_env):
        """Proposed rules (INT-SKIP) should not be executed."""
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", iceberg_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", iceberg_env["results_dir"]):
            result = run_rules(spec="test-integration", catalog=iceberg_env["catalog"])

        rule_ids = [r["rule_id"] for r in result["results"]]
        assert "INT-SKIP" not in rule_ids

    def test_run_rules_saves_results_file(self, iceberg_env):
        """Results should be written to DQ_RESULTS_DIR."""
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", iceberg_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", iceberg_env["results_dir"]):
            run_rules(spec="test-integration", catalog=iceberg_env["catalog"])

        files = list(iceberg_env["results_dir"].glob("*.json"))
        assert len(files) == 1
        saved = json.loads(files[0].read_text())
        assert saved["rules_total"] == 3

    def test_run_rules_priority_filter(self, iceberg_env):
        """Filtering by priority should only run matching rules."""
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", iceberg_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", iceberg_env["results_dir"]):
            result = run_rules(spec="test-integration", priority="P1", catalog=iceberg_env["catalog"])

        assert result["rules_total"] == 1
        assert result["results"][0]["rule_id"] == "INT-003"


# ---------------------------------------------------------------------------
# Integration: validate_after_write
# ---------------------------------------------------------------------------


class TestValidateAfterWrite:
    """validate_after_write raises DQValidationError on real P0 failures."""

    @pytest.fixture
    def passing_env(self, tmp_path):
        """Iceberg env where all rules pass."""
        from pyiceberg.schema import Schema
        from pyiceberg.types import DoubleType, IntegerType, NestedField, StringType

        from brightsmith.infra.iceberg_setup import append_data, get_or_create_table, get_catalog

        catalog = get_catalog(tmp_path / "wh", tmp_path / "cat.db")
        schema = Schema(
            NestedField(1, "id", StringType(), required=True),
            NestedField(2, "val", DoubleType(), required=True),
        )
        table = get_or_create_table(catalog, "silver", "clean_table", schema)
        append_data(table, [{"id": "A", "val": 1.0}, {"id": "B", "val": 2.0}])

        rules_dir = tmp_path / "dq-rules"
        rules_dir.mkdir()
        results_dir = tmp_path / "dq-results"
        results_dir.mkdir()

        rules = {
            "spec": "test-pass",
            "tables": ["silver.clean_table"],
            "rules": [{
                "rule_id": "PASS-001",
                "category": "Validity",
                "priority": "P0",
                "description": "All vals positive",
                "sql": "SELECT COUNT(*) FROM silver.clean_table WHERE val <= 0",
                "threshold": "result = 0",
                "status": "active",
            }],
        }
        (rules_dir / "test-pass.json").write_text(json.dumps(rules, indent=2))

        return {"catalog": catalog, "rules_dir": rules_dir, "results_dir": results_dir}

    @pytest.fixture
    def failing_env(self, tmp_path):
        """Iceberg env where a P0 rule fails."""
        from pyiceberg.schema import Schema
        from pyiceberg.types import DoubleType, NestedField, StringType

        from brightsmith.infra.iceberg_setup import append_data, get_or_create_table, get_catalog

        catalog = get_catalog(tmp_path / "wh", tmp_path / "cat.db")
        schema = Schema(
            NestedField(1, "id", StringType(), required=True),
            NestedField(2, "val", DoubleType(), required=True),
        )
        table = get_or_create_table(catalog, "silver", "dirty_table", schema)
        append_data(table, [
            {"id": "A", "val": 1.0},
            {"id": "A", "val": 2.0},  # duplicate id
        ])

        rules_dir = tmp_path / "dq-rules"
        rules_dir.mkdir()
        results_dir = tmp_path / "dq-results"
        results_dir.mkdir()

        rules = {
            "spec": "test-fail",
            "tables": ["silver.dirty_table"],
            "rules": [{
                "rule_id": "FAIL-001",
                "category": "Uniqueness",
                "priority": "P0",
                "description": "No duplicate ids",
                "sql": "SELECT id, COUNT(*) FROM silver.dirty_table GROUP BY id HAVING COUNT(*) > 1",
                "threshold": "result_count = 0",
                "status": "active",
            }],
        }
        (rules_dir / "test-fail.json").write_text(json.dumps(rules, indent=2))

        return {"catalog": catalog, "rules_dir": rules_dir, "results_dir": results_dir}

    def test_validate_returns_result_on_pass(self, passing_env):
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", passing_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", passing_env["results_dir"]):
            result = validate_after_write("test-pass", catalog=passing_env["catalog"])

        assert result["rules_passed"] == 1
        assert result["p0_passed"] is True

    def test_validate_raises_on_p0_failure(self, failing_env):
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", failing_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", failing_env["results_dir"]):
            with pytest.raises(DQValidationError) as exc_info:
                validate_after_write("test-fail", catalog=failing_env["catalog"])

        assert "FAIL-001" in str(exc_info.value)
        assert len(exc_info.value.failures) == 1

    def test_validate_error_includes_run_result(self, failing_env):
        with patch("brightsmith.infra.dq_runner.DQ_RULES_DIR", failing_env["rules_dir"]), \
             patch("brightsmith.infra.dq_runner.DQ_RESULTS_DIR", failing_env["results_dir"]):
            with pytest.raises(DQValidationError) as exc_info:
                validate_after_write("test-fail", catalog=failing_env["catalog"])

        assert exc_info.value.run_result["rules_total"] == 1
        assert exc_info.value.run_result["p0_passed"] is False
