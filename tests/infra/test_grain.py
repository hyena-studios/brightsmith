"""Tests for deterministic grain hashing."""

import pytest

from brightsmith.infra.grain import compute_grain_id


def test_same_input_produces_same_hash():
    """Deterministic: same grain values → same hash every time."""
    row = {"cik": 320193, "fy": 2024, "fp": "FY"}
    fields = ["cik", "fy", "fp"]
    h1 = compute_grain_id(row, fields)
    h2 = compute_grain_id(row, fields)
    assert h1 == h2
    assert len(h1) == 16


def test_different_input_produces_different_hash():
    """Distinct grains should produce distinct hashes."""
    row_a = {"cik": 320193, "fy": 2024, "fp": "FY"}
    row_b = {"cik": 320193, "fy": 2023, "fp": "FY"}
    fields = ["cik", "fy", "fp"]
    assert compute_grain_id(row_a, fields) != compute_grain_id(row_b, fields)


def test_present_but_none_value_hashes_deterministically():
    """A grain field present with a None value is allowed and hashes as "None".

    (WP-2.4: distinguishes "key present, value None" — allowed — from
    "key missing" — a hard error, see test_missing_grain_field_raises.)
    """
    row = {"cik": 320193, "fy": None, "fp": "FY"}
    fields = ["cik", "fy", "fp"]
    h1 = compute_grain_id(row, fields)
    h2 = compute_grain_id(row, fields)
    assert h1 == h2
    assert len(h1) == 16
    # It hashes the literal string "None" for the missing value, so it differs
    # from a row whose fy is the integer 0 or any other value.
    assert h1 != compute_grain_id({"cik": 320193, "fy": 0, "fp": "FY"}, fields)


def test_missing_grain_field_raises():
    """A grain field KEY absent from the row is a hard error naming the field.

    (WP-2.4: previously this silently became "" and collapsed distinct rows into
    one hash — data loss via dedup.)
    """
    row = {"cik": 320193, "fp": "FY"}  # "fy" key entirely absent
    fields = ["cik", "fy", "fp"]
    with pytest.raises(ValueError, match="fy"):
        compute_grain_id(row, fields)


def test_delimiter_escaping_prevents_collision():
    """('a|b', 'c') must not collide with ('a', 'b|c'). (WP-2.4)"""
    fields = ["x", "y"]
    h1 = compute_grain_id({"x": "a|b", "y": "c"}, fields)
    h2 = compute_grain_id({"x": "a", "y": "b|c"}, fields)
    assert h1 != h2


def test_prefix_included_in_id():
    """Prefix should appear at the start of the ID."""
    row = {"cik": 320193, "fy": 2024}
    result = compute_grain_id(row, ["cik", "fy"], prefix="CF")
    assert result.startswith("CF-")
    assert len(result) == 19  # "CF-" + 16 hex chars


def test_no_prefix():
    """Without prefix, just the 16-char hash is returned."""
    row = {"cik": 320193, "fy": 2024}
    result = compute_grain_id(row, ["cik", "fy"])
    assert "-" not in result
    assert len(result) == 16


def test_field_order_matters():
    """(a, b) should produce a different hash than (b, a)."""
    row = {"a": "1", "b": "2"}
    h1 = compute_grain_id(row, ["a", "b"])
    h2 = compute_grain_id(row, ["b", "a"])
    assert h1 != h2


def test_empty_grain_fields():
    """Empty grain fields list should produce a consistent hash."""
    row = {"cik": 320193}
    result = compute_grain_id(row, [])
    assert isinstance(result, str)
    assert len(result) == 16


def test_special_characters_in_values():
    """Special characters should hash without error."""
    row = {"name": "O'Brien & Co.", "id": "123|456"}
    result = compute_grain_id(row, ["name", "id"])
    assert isinstance(result, str)
    assert len(result) == 16
