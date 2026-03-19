"""Tests for deterministic grain hashing."""

from grist.infra.grain import compute_grain_id


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


def test_null_fields_handled():
    """None and missing fields should produce a consistent hash, not crash."""
    row_a = {"cik": 320193, "fy": None, "fp": "FY"}
    row_b = {"cik": 320193, "fp": "FY"}  # fy missing entirely
    fields = ["cik", "fy", "fp"]
    h_a = compute_grain_id(row_a, fields)
    h_b = compute_grain_id(row_b, fields)
    assert isinstance(h_a, str)
    assert isinstance(h_b, str)
    # Both use str(None) and str("") respectively — different but both valid
    assert len(h_a) == 16
    assert len(h_b) == 16


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
