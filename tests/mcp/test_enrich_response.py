"""Tests for the enrich_response() pipeline in BaseMCPServer."""

from __future__ import annotations

import pytest

from brightsmith.mcp.base_anomaly_checker import AnomalyRule, BaseAnomalyChecker
from brightsmith.mcp.base_formatter import BaseFormatter, FormatRule
from brightsmith.mcp.base_mcp_server import BaseMCPServer


class SimpleFormatter(BaseFormatter):
    def get_format_rules(self):
        return [
            FormatRule(
                match=lambda col, val, row: col == "val",
                format_fn=lambda val: f"${val:,.0f}",
            ),
        ]


class SimpleChecker(BaseAnomalyChecker):
    def get_anomaly_rules(self):
        return [
            AnomalyRule(
                rule_id="TEST-001",
                description="Negative value",
                check=lambda row: (row.get("val") or 0) < 0,
                flag="Value is negative",
                severity="warning",
            ),
        ]


@pytest.fixture
def base_server(tmp_path):
    """Server with no intelligence layer."""
    return BaseMCPServer(
        warehouse_path=tmp_path / "warehouse",
        catalog_path=tmp_path / "catalog.db",
    )


@pytest.fixture
def formatter_server(tmp_path):
    """Server with formatter only."""
    return BaseMCPServer(
        warehouse_path=tmp_path / "warehouse",
        catalog_path=tmp_path / "catalog.db",
        formatter=SimpleFormatter(),
    )


@pytest.fixture
def anomaly_server(tmp_path):
    """Server with anomaly checker only."""
    return BaseMCPServer(
        warehouse_path=tmp_path / "warehouse",
        catalog_path=tmp_path / "catalog.db",
        anomaly_checker=SimpleChecker(),
    )


@pytest.fixture
def full_server(tmp_path):
    """Server with all intelligence layer components."""
    return BaseMCPServer(
        warehouse_path=tmp_path / "warehouse",
        catalog_path=tmp_path / "catalog.db",
        formatter=SimpleFormatter(),
        anomaly_checker=SimpleChecker(),
    )


class TestEnrichNoComponents:
    def test_governance_only(self, base_server):
        """Without intelligence layer, enrich_response is just attach_governance."""
        result = {"data": [{"val": 100}], "row_count": 1}
        enriched = base_server.enrich_response(result, "consumable.test")
        assert "governance" in enriched
        assert enriched["governance"]["table"] == "consumable.test"
        assert enriched["data"] == [{"val": 100}]

    def test_backward_compatible(self, base_server):
        """attach_governance still works standalone."""
        result = {"data": [{"val": 100}]}
        enriched = base_server.attach_governance(result, "consumable.test")
        assert "governance" in enriched


class TestEnrichFormatterOnly:
    def test_format_plus_governance(self, formatter_server):
        result = {"data": [{"val": 1234567}]}
        enriched = formatter_server.enrich_response(result, "consumable.test")
        assert enriched["data"][0]["val"] == 1234567
        assert enriched["data"][0]["val_formatted"] == "$1,234,567"
        assert "governance" in enriched

    def test_no_anomaly_flags(self, formatter_server):
        """Formatter-only server doesn't add anomaly flags."""
        result = {"data": [{"val": -100}]}
        enriched = formatter_server.enrich_response(result, "consumable.test")
        assert "_anomaly_flags" not in enriched["data"][0]


class TestEnrichAnomalyOnly:
    def test_flag_plus_governance(self, anomaly_server):
        result = {"data": [{"val": -100}]}
        enriched = anomaly_server.enrich_response(result, "consumable.test")
        assert len(enriched["data"][0]["_anomaly_flags"]) == 1
        assert enriched["data"][0]["_anomaly_flags"][0]["rule_id"] == "TEST-001"
        assert "governance" in enriched

    def test_no_formatted_keys(self, anomaly_server):
        """Anomaly-only server doesn't add formatted keys."""
        result = {"data": [{"val": 100}]}
        enriched = anomaly_server.enrich_response(result, "consumable.test")
        assert "val_formatted" not in enriched["data"][0]


class TestEnrichFullPipeline:
    def test_format_then_flag_then_governance(self, full_server):
        result = {"data": [{"val": -5000}]}
        enriched = full_server.enrich_response(result, "consumable.test")
        row = enriched["data"][0]
        # Formatted
        assert row["val_formatted"] == "$-5,000"
        # Flagged
        assert len(row["_anomaly_flags"]) == 1
        # Governance
        assert "governance" in enriched

    def test_clean_row_full_pipeline(self, full_server):
        result = {"data": [{"val": 1000}]}
        enriched = full_server.enrich_response(result, "consumable.test")
        row = enriched["data"][0]
        assert row["val_formatted"] == "$1,000"
        assert row["_anomaly_flags"] == []
        assert "governance" in enriched


class TestEnrichPreservesData:
    def test_original_values_never_lost(self, full_server):
        result = {"data": [{"val": 42, "name": "Acme"}], "row_count": 1}
        enriched = full_server.enrich_response(result, "consumable.test")
        assert enriched["data"][0]["val"] == 42
        assert enriched["data"][0]["name"] == "Acme"
        assert enriched["row_count"] == 1


class TestEnrichNonDataResults:
    def test_no_data_key(self, full_server):
        """Results without 'data' list are just governance-wrapped."""
        result = {"tables": ["a", "b"]}
        enriched = full_server.enrich_response(result, "consumable.test")
        assert "governance" in enriched
        assert enriched["tables"] == ["a", "b"]

    def test_data_not_list(self, full_server):
        """If 'data' is not a list, skip formatting/flagging."""
        result = {"data": "raw string"}
        enriched = full_server.enrich_response(result, "consumable.test")
        assert enriched["data"] == "raw string"
        assert "governance" in enriched
