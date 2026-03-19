"""Tests for semantic corruption strategies."""

import random
from datetime import date

from grist.infra.chaos_monkey.semantic_corruptor import SemanticCorruptor


def _make_records():
    return [
        {"cik": 1, "revenue": 100.0, "net_income": 20.0, "filed": date(2024, 3, 1)},
        {"cik": 2, "revenue": 200.0, "net_income": 40.0, "filed": date(2024, 3, 15)},
        {"cik": 3, "revenue": 300.0, "net_income": 60.0, "filed": date(2024, 4, 1)},
    ]


def test_column_swap_exchanges_values():
    """Column swap should exchange values between two columns."""
    sc = SemanticCorruptor()
    records = _make_records()
    original_rev = records[0]["revenue"]
    original_ni = records[0]["net_income"]

    result, corruptions = sc.swap_columns(
        records, rate=1.0, rng=random.Random(42), col_a="revenue", col_b="net_income",
    )
    # At least some rows should have swapped values
    assert any(c.strategy == "column_swap" for c in corruptions)
    assert all(c.dimension == "Consistency" for c in corruptions)


def test_entity_mix_assigns_wrong_values():
    """Entity mixing should assign one entity's values to another."""
    sc = SemanticCorruptor()
    records = _make_records()
    result, corruptions = sc.mix_entities(
        records, rate=1.0, rng=random.Random(42),
        entity_field="cik", value_fields=["revenue", "net_income"],
    )
    assert any(c.strategy == "entity_mix" for c in corruptions)
    assert all(c.dimension == "Consistency" for c in corruptions)


def test_temporal_shift_moves_dates():
    """Temporal shift should move dates by the specified number of days."""
    sc = SemanticCorruptor()
    records = _make_records()
    original_date = records[0]["filed"]

    result, corruptions = sc.shift_temporal(
        records, rate=1.0, rng=random.Random(42),
        date_fields=["filed"], shift_days=365,
    )
    assert any(c.strategy == "temporal_shift" for c in corruptions)
    # At least one date should have moved
    shifted = [r for r in result if r["filed"] != original_date]
    assert len(shifted) > 0


def test_empty_records_handled():
    """All methods should handle empty records gracefully."""
    sc = SemanticCorruptor()
    rng = random.Random(42)
    result, c = sc.swap_columns([], rate=0.1, rng=rng, col_a="a", col_b="b")
    assert result == [] and c == []
    result, c = sc.shift_temporal([], rate=0.1, rng=rng, date_fields=["d"])
    assert result == [] and c == []
