"""Domain-agnostic query-time anomaly detection.

Runs rules against tool results and attaches human-readable flags.
These are interpretation aids, not DQ rules — they help LLMs caveat
their responses, never block the pipeline.

Usage:
    from brightsmith.mcp.base_anomaly_checker import BaseAnomalyChecker, AnomalyRule

    class MyAnomalyChecker(BaseAnomalyChecker):
        def get_anomaly_rules(self) -> list[AnomalyRule]:
            return [
                AnomalyRule(
                    rule_id="ANOM-001",
                    description="Extreme YoY change",
                    check=lambda row: abs(row.get("yoy_pct", 0)) > 2.0,
                    flag="Extreme YoY change (>200%)",
                    severity="warning",
                ),
            ]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class AnomalyFlag:
    """A flag attached to a data point by an anomaly rule."""

    rule_id: str
    severity: str
    """One of: info, warning, caveat, error."""
    message: str


@dataclass
class AnomalyRule:
    """A query-time anomaly detection rule."""

    rule_id: str
    description: str
    check: Callable[[dict], bool]
    """Predicate: (row) -> is_anomalous."""
    flag: str
    """Human-readable flag message."""
    severity: str = "warning"
    """One of: info, warning, caveat, error."""


class BaseAnomalyChecker:
    """Domain-agnostic query-time anomaly detection.

    Domain projects subclass and override ``get_anomaly_rules()`` to register
    anomaly rules. The framework runs all rules against each row and attaches
    matching flags.
    """

    def get_anomaly_rules(self) -> list[AnomalyRule]:
        """Override in domain project. Return anomaly rules."""
        return []

    def check_row(self, row: dict) -> list[AnomalyFlag]:
        """Run all rules against a single row. Returns matching flags."""
        flags = []
        for rule in self.get_anomaly_rules():
            try:
                if rule.check(row):
                    flags.append(AnomalyFlag(
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        message=rule.flag,
                    ))
            except Exception:
                continue
        return flags

    def check_rows(self, rows: list[dict]) -> list[dict]:
        """Run all rules against rows.

        Returns new dicts with ``_anomaly_flags`` key attached to each row.
        Rows with no anomalies get an empty list.
        """
        result = []
        for row in rows:
            new_row = dict(row)
            flags = self.check_row(row)
            new_row["_anomaly_flags"] = [
                {"rule_id": f.rule_id, "severity": f.severity, "message": f.message}
                for f in flags
            ]
            result.append(new_row)
        return result
