"""Tests for format_utils — common format functions."""

from __future__ import annotations

import math
from datetime import date

from brightsmith.mcp.format_utils import (
    format_date_range,
    format_decimal,
    format_large_number,
    format_multiplier,
    format_percentage,
    format_yoy_change,
)


class TestFormatLargeNumber:
    def test_billions(self):
        assert format_large_number(1_234_567_890) == "1.2B"

    def test_billions_with_prefix(self):
        assert format_large_number(394_328_000_000, prefix="$") == "$394.3B"

    def test_millions(self):
        assert format_large_number(1_234_567) == "1.2M"

    def test_millions_with_prefix(self):
        assert format_large_number(97_000_000, prefix="$") == "$97.0M"

    def test_thousands(self):
        assert format_large_number(1_234) == "1,234"

    def test_small_integer(self):
        assert format_large_number(42) == "42"

    def test_small_decimal(self):
        assert format_large_number(3.14) == "3.14"

    def test_negative_billions(self):
        assert format_large_number(-1_234_567_890) == "-1.2B"

    def test_negative_millions(self):
        assert format_large_number(-1_234_567) == "-1.2M"

    def test_zero(self):
        assert format_large_number(0) == "0"

    def test_none(self):
        assert format_large_number(None) == ""

    def test_nan(self):
        assert format_large_number(float("nan")) == ""

    def test_suffix(self):
        assert format_large_number(1_000_000, suffix=" USD") == "1.0M USD"


class TestFormatPercentage:
    def test_positive(self):
        assert format_percentage(0.253) == "25.3%"

    def test_negative(self):
        assert format_percentage(-0.124) == "-12.4%"

    def test_zero(self):
        assert format_percentage(0.0) == "0.0%"

    def test_custom_decimals(self):
        assert format_percentage(0.12345, decimals=2) == "12.35%"

    def test_none(self):
        assert format_percentage(None) == ""

    def test_nan(self):
        assert format_percentage(float("nan")) == ""


class TestFormatDecimal:
    def test_with_thousands(self):
        assert format_decimal(1234.5678) == "1,234.57"

    def test_integer(self):
        assert format_decimal(1000) == "1,000.00"

    def test_custom_decimals(self):
        assert format_decimal(1234.5, decimals=1) == "1,234.5"

    def test_none(self):
        assert format_decimal(None) == ""


class TestFormatMultiplier:
    def test_normal(self):
        assert format_multiplier(2.345) == "2.3x"

    def test_negative(self):
        assert format_multiplier(-15.1) == "-15.1x"

    def test_zero(self):
        assert format_multiplier(0.0) == "0.0x"

    def test_none(self):
        assert format_multiplier(None) == ""


class TestFormatDateRange:
    def test_same_year(self):
        assert format_date_range(date(2024, 1, 1), date(2024, 12, 31)) == "Jan 2024 - Dec 2024"

    def test_cross_year(self):
        assert format_date_range(date(2023, 10, 1), date(2024, 9, 30)) == "Oct 2023 - Sep 2024"

    def test_none_start(self):
        assert format_date_range(None, date(2024, 12, 31)) == ""

    def test_none_end(self):
        assert format_date_range(date(2024, 1, 1), None) == ""


class TestFormatYoyChange:
    def test_positive(self):
        assert format_yoy_change(0.078) == "+7.8%"

    def test_negative(self):
        assert format_yoy_change(-0.123) == "-12.3%"

    def test_zero(self):
        assert format_yoy_change(0.0) == "0.0%"

    def test_none(self):
        assert format_yoy_change(None) == ""

    def test_nan(self):
        assert format_yoy_change(float("nan")) == ""

    def test_custom_decimals(self):
        assert format_yoy_change(0.12345, decimals=2) == "+12.35%"
