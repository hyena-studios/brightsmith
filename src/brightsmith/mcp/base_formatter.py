"""Domain-agnostic value formatter with type dispatch.

The framework provides the dispatch mechanism. Domain projects register
format rules that match values by column name, value type, or row context,
and provide format functions that convert raw values to human-readable strings.

Usage:
    from brightsmith.mcp.base_formatter import BaseFormatter, FormatRule

    class MyFormatter(BaseFormatter):
        def get_format_rules(self) -> list[FormatRule]:
            return [
                FormatRule(
                    match=lambda col, val, row: row.get("unit") == "USD",
                    format_fn=format_currency,
                ),
            ]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class FormatRule:
    """A formatting rule: match predicate + format function.

    Rules are evaluated in order. First matching rule wins.
    """

    match: Callable[[str, Any, dict], bool]
    """Predicate: (column_name, value, full_row) -> should_format."""

    format_fn: Callable[[Any], str]
    """Formatter: (value) -> formatted_string."""


class BaseFormatter:
    """Domain-agnostic value formatter with type dispatch.

    Domain projects subclass and override ``get_format_rules()`` to register
    format rules. The framework handles dispatch — first matching rule wins.
    """

    def get_format_rules(self) -> list[FormatRule]:
        """Override in domain project. Return format rules in priority order."""
        return []

    def format_value(self, column: str, value: Any, row: dict) -> str | None:
        """Format a single value. Returns None if no rule matches."""
        if value is None:
            return None
        for rule in self.get_format_rules():
            try:
                if rule.match(column, value, row):
                    return rule.format_fn(value)
            except Exception:
                continue
        return None

    def format_row(self, row: dict) -> dict:
        """Format all values in a row using registered rules.

        Returns a new dict. For each formatted value, adds a
        ``{column}_formatted`` key with the string representation.
        The original value is preserved under the original key.
        """
        result = dict(row)
        for column, value in row.items():
            if column.startswith("_"):
                continue
            formatted = self.format_value(column, value, row)
            if formatted is not None:
                result[f"{column}_formatted"] = formatted
        return result

    def format_rows(self, rows: list[dict]) -> list[dict]:
        """Format all rows. Convenience batch method."""
        return [self.format_row(row) for row in rows]
