"""Common format functions for domain projects to reuse.

These are convenience functions, not base class methods. Domain projects
import and use them in their FormatRule.format_fn. The framework doesn't
assume which formatters any domain needs.

Usage:
    from brightsmith.mcp.format_utils import format_large_number, format_percentage

    FormatRule(
        match=lambda col, val, row: row.get("unit") == "USD",
        format_fn=lambda val: format_large_number(val, prefix="$"),
    )
"""

from __future__ import annotations

import math
from datetime import date


def format_large_number(value: float | int, prefix: str = "", suffix: str = "") -> str:
    """Format large numbers with magnitude suffixes.

    1_234_567_890 -> '1.2B'
    1_234_567 -> '1.2M'
    1_234 -> '1,234'
    Negative values get a leading minus sign.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    num = float(value)
    negative = num < 0
    num = abs(num)

    if num >= 1_000_000_000:
        formatted = f"{num / 1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        formatted = f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        formatted = f"{num:,.0f}"
    else:
        formatted = f"{num:,.2f}" if num != int(num) else f"{int(num)}"

    sign = "-" if negative else ""
    return f"{sign}{prefix}{formatted}{suffix}"


def format_percentage(value: float, decimals: int = 1) -> str:
    """Format a decimal fraction as a percentage.

    0.253 -> '25.3%'
    -0.124 -> '-12.4%'
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    pct = value * 100
    return f"{pct:.{decimals}f}%"


def format_decimal(value: float | int, decimals: int = 2) -> str:
    """Format a number with thousands separators and fixed decimals.

    1234.5678 -> '1,234.57'
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{float(value):,.{decimals}f}"


def format_multiplier(value: float, decimals: int = 1) -> str:
    """Format a ratio as a multiplier.

    2.345 -> '2.3x'
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{value:.{decimals}f}x"


def format_date_range(start: date, end: date) -> str:
    """Format a date range as a human-readable string.

    (2024-01-01, 2024-12-31) -> 'Jan 2024 - Dec 2024'
    """
    if start is None or end is None:
        return ""
    return f"{start.strftime('%b %Y')} - {end.strftime('%b %Y')}"


def format_yoy_change(value: float, decimals: int = 1) -> str:
    """Format a year-over-year change with explicit sign.

    0.078 -> '+7.8%'
    -0.123 -> '-12.3%'
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    pct = value * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.{decimals}f}%"
