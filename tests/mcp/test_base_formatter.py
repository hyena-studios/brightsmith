"""Tests for BaseFormatter — domain-agnostic value formatting."""

from __future__ import annotations

from brightsmith.mcp.base_formatter import BaseFormatter, FormatRule


class TestFormatter(BaseFormatter):
    """Test formatter with a few rules."""

    def get_format_rules(self) -> list[FormatRule]:
        return [
            FormatRule(
                match=lambda col, val, row: col == "revenue",
                format_fn=lambda val: f"${val:,.0f}",
            ),
            FormatRule(
                match=lambda col, val, row: col.endswith("_pct"),
                format_fn=lambda val: f"{val * 100:.1f}%",
            ),
            FormatRule(
                match=lambda col, val, row: isinstance(val, float),
                format_fn=lambda val: f"{val:.2f}",
            ),
        ]


class TestNoRules:
    def test_empty_formatter_passthrough(self):
        """Empty formatter returns rows unchanged."""
        f = BaseFormatter()
        row = {"a": 1, "b": "hello"}
        result = f.format_row(row)
        assert result == {"a": 1, "b": "hello"}

    def test_empty_formatter_no_formatted_keys(self):
        """No _formatted keys added when no rules match."""
        f = BaseFormatter()
        row = {"x": 42}
        result = f.format_row(row)
        assert "x_formatted" not in result


class TestSingleRuleMatch:
    def test_matching_rule_adds_formatted_key(self):
        f = TestFormatter()
        row = {"revenue": 1234567}
        result = f.format_row(row)
        assert result["revenue"] == 1234567
        assert result["revenue_formatted"] == "$1,234,567"

    def test_pct_rule_matches(self):
        f = TestFormatter()
        row = {"margin_pct": 0.253}
        result = f.format_row(row)
        assert result["margin_pct_formatted"] == "25.3%"


class TestFirstMatchWins:
    def test_revenue_uses_first_rule_not_float_fallback(self):
        """revenue column matches first rule (currency), not third (float)."""
        f = TestFormatter()
        row = {"revenue": 1000.0}
        result = f.format_row(row)
        assert result["revenue_formatted"] == "$1,000"

    def test_unknown_float_uses_fallback(self):
        """Float value in non-matching column uses the float fallback rule."""
        f = TestFormatter()
        row = {"ratio": 2.345}
        result = f.format_row(row)
        assert result["ratio_formatted"] == "2.35"


class TestNoMatch:
    def test_non_matching_value_unchanged(self):
        f = TestFormatter()
        row = {"name": "Acme Corp"}
        result = f.format_row(row)
        assert "name_formatted" not in result
        assert result["name"] == "Acme Corp"


class TestNoneValues:
    def test_none_values_skipped(self):
        f = TestFormatter()
        row = {"revenue": None}
        result = f.format_row(row)
        assert "revenue_formatted" not in result

    def test_none_in_format_value(self):
        f = TestFormatter()
        assert f.format_value("revenue", None, {}) is None


class TestBatchFormatting:
    def test_format_rows_batch(self):
        f = TestFormatter()
        rows = [
            {"revenue": 100, "name": "A"},
            {"revenue": 200, "name": "B"},
        ]
        result = f.format_rows(rows)
        assert len(result) == 2
        assert result[0]["revenue_formatted"] == "$100"
        assert result[1]["revenue_formatted"] == "$200"
        assert "name_formatted" not in result[0]


class TestOriginalPreserved:
    def test_raw_value_always_kept(self):
        f = TestFormatter()
        row = {"revenue": 999}
        result = f.format_row(row)
        assert result["revenue"] == 999
        assert "revenue_formatted" in result


class TestPrivateKeysSkipped:
    def test_underscore_keys_not_formatted(self):
        """Keys starting with _ are internal metadata, not formatted."""
        f = TestFormatter()
        row = {"_anomaly_flags": [], "revenue": 100}
        result = f.format_row(row)
        assert "_anomaly_flags_formatted" not in result
        assert "revenue_formatted" in result


class TestRuleException:
    def test_rule_exception_skipped(self):
        """If a rule's match or format_fn raises, skip to next rule."""

        class BrokenFormatter(BaseFormatter):
            def get_format_rules(self):
                return [
                    FormatRule(
                        match=lambda col, val, row: 1 / 0,  # raises
                        format_fn=lambda val: "never",
                    ),
                    FormatRule(
                        match=lambda col, val, row: True,
                        format_fn=lambda val: "fallback",
                    ),
                ]

        f = BrokenFormatter()
        result = f.format_value("x", 42, {})
        assert result == "fallback"
