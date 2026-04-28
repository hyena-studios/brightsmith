"""Tests for BaseAnomalyChecker — query-time anomaly detection."""

from __future__ import annotations

from brightsmith.mcp.base_anomaly_checker import (
    AnomalyRule,
    BaseAnomalyChecker,
)


class TestChecker(BaseAnomalyChecker):
    """Test checker with a few rules."""

    def get_anomaly_rules(self) -> list[AnomalyRule]:
        return [
            AnomalyRule(
                rule_id="ANOM-001",
                description="Extreme YoY change",
                check=lambda row: abs(row.get("yoy_pct", 0)) > 2.0,
                flag="Extreme YoY change (>200%)",
                severity="warning",
            ),
            AnomalyRule(
                rule_id="ANOM-002",
                description="Negative value",
                check=lambda row: (row.get("val") or 0) < 0,
                flag="Negative value detected",
                severity="caveat",
            ),
            AnomalyRule(
                rule_id="ANOM-003",
                description="Missing metric",
                check=lambda row: row.get("metric") is None,
                flag="Metric field is missing",
                severity="info",
            ),
        ]


class TestNoRules:
    def test_empty_checker_no_flags(self):
        checker = BaseAnomalyChecker()
        flags = checker.check_row({"val": 100})
        assert flags == []

    def test_empty_checker_check_rows(self):
        checker = BaseAnomalyChecker()
        rows = [{"val": 1}, {"val": 2}]
        result = checker.check_rows(rows)
        assert all(r["_anomaly_flags"] == [] for r in result)


class TestMatchingRule:
    def test_anomalous_row_gets_flag(self):
        checker = TestChecker()
        flags = checker.check_row({"yoy_pct": 3.5, "val": 100, "metric": "revenue"})
        assert len(flags) == 1
        assert flags[0].rule_id == "ANOM-001"
        assert flags[0].severity == "warning"
        assert "YoY" in flags[0].message


class TestNonMatching:
    def test_clean_row_no_flags(self):
        checker = TestChecker()
        flags = checker.check_row({"yoy_pct": 0.05, "val": 100, "metric": "revenue"})
        assert flags == []


class TestMultipleFlags:
    def test_row_triggers_multiple_rules(self):
        checker = TestChecker()
        flags = checker.check_row({"yoy_pct": 5.0, "val": -100, "metric": "equity"})
        assert len(flags) == 2
        rule_ids = {f.rule_id for f in flags}
        assert "ANOM-001" in rule_ids
        assert "ANOM-002" in rule_ids


class TestCheckRows:
    def test_batch_attaches_flags(self):
        checker = TestChecker()
        rows = [
            {"yoy_pct": 0.1, "val": 100, "metric": "revenue"},
            {"yoy_pct": 5.0, "val": -50, "metric": "equity"},
        ]
        result = checker.check_rows(rows)
        assert len(result) == 2
        assert result[0]["_anomaly_flags"] == []
        assert len(result[1]["_anomaly_flags"]) == 2

    def test_batch_preserves_original_data(self):
        checker = TestChecker()
        rows = [{"val": 42, "metric": "revenue"}]
        result = checker.check_rows(rows)
        assert result[0]["val"] == 42
        assert result[0]["metric"] == "revenue"


class TestSeverityLevels:
    def test_info_severity(self):
        checker = TestChecker()
        flags = checker.check_row({"yoy_pct": 0.0, "val": 100})
        assert len(flags) == 1
        assert flags[0].severity == "info"
        assert flags[0].rule_id == "ANOM-003"

    def test_caveat_severity(self):
        checker = TestChecker()
        flags = checker.check_row({"yoy_pct": 0.0, "val": -10, "metric": "equity"})
        assert any(f.severity == "caveat" for f in flags)


class TestFlagSerialization:
    def test_check_rows_flag_format(self):
        """Flags in check_rows output are dicts with rule_id, severity, message."""
        checker = TestChecker()
        rows = [{"yoy_pct": 5.0, "val": 100, "metric": "revenue"}]
        result = checker.check_rows(rows)
        flag = result[0]["_anomaly_flags"][0]
        assert isinstance(flag, dict)
        assert "rule_id" in flag
        assert "severity" in flag
        assert "message" in flag


class TestRuleException:
    def test_exception_in_check_skipped(self):
        """If a rule raises, skip it and continue."""

        class BrokenChecker(BaseAnomalyChecker):
            def get_anomaly_rules(self):
                return [
                    AnomalyRule(
                        rule_id="BAD",
                        description="Broken",
                        check=lambda row: 1 / 0,
                        flag="never",
                    ),
                    AnomalyRule(
                        rule_id="GOOD",
                        description="Works",
                        check=lambda row: True,
                        flag="Always flags",
                        severity="info",
                    ),
                ]

        checker = BrokenChecker()
        flags = checker.check_row({"val": 1})
        assert len(flags) == 1
        assert flags[0].rule_id == "GOOD"
