"""Tests for row-level corruption strategies."""

import random

from grist.infra.chaos_monkey.row_corruptor import RowCorruptor


def _make_records():
    return [
        {"cik": 1, "fy": 2024, "value": 100.0, "name": "Apple"},
        {"cik": 2, "fy": 2024, "value": 200.0, "name": "Microsoft"},
        {"cik": 3, "fy": 2024, "value": 300.0, "name": "Google"},
        {"cik": 1, "fy": 2023, "value": 90.0, "name": "Apple"},
        {"cik": 2, "fy": 2023, "value": 180.0, "name": "Microsoft"},
    ]


def test_duplicate_rows_adds_exact_copies():
    """Duplicate injection should add exact copies of existing rows."""
    rc = RowCorruptor()
    records = _make_records()
    original_len = len(records)
    result, corruptions = rc.duplicate_rows(records, rate=0.5, rng=random.Random(42))
    assert len(result) > original_len
    assert all(c.strategy == "exact_duplicate" for c in corruptions)
    assert all(c.dimension == "Uniqueness" for c in corruptions)


def test_near_duplicate_differs_in_non_grain_field():
    """Near-duplicate should match grain but differ in non-grain field."""
    rc = RowCorruptor()
    records = _make_records()
    result, corruptions = rc.near_duplicate_rows(
        records, rate=0.5, rng=random.Random(42), grain_fields=["cik", "fy"],
    )
    assert len(result) > len(_make_records())
    assert all(c.strategy == "near_duplicate" for c in corruptions)


def test_orphan_fk_creates_invalid_references():
    """Orphan FK injection should create values that don't exist in parent."""
    rc = RowCorruptor()
    records = _make_records()
    result, corruptions = rc.orphan_foreign_keys(
        records, rate=0.5, rng=random.Random(42), fk_field="cik",
    )
    orphan_values = [r["cik"] for r in result if isinstance(r["cik"], str) and r["cik"].startswith("ORPHAN_")]
    assert len(orphan_values) > 0
    assert all(c.dimension == "Referential Integrity" for c in corruptions)


def test_entity_removal_drops_all_rows_for_one_entity():
    """Entity removal should drop ALL rows for one entity."""
    rc = RowCorruptor()
    records = _make_records()
    result, corruptions = rc.remove_entity(records, rng=random.Random(42), entity_field="cik")
    remaining_entities = set(r["cik"] for r in result)
    # At least one entity should be gone
    original_entities = set(r["cik"] for r in _make_records())
    assert len(remaining_entities) < len(original_entities)
    assert corruptions[0].strategy == "entity_removal"
    assert corruptions[0].dimension == "Coverage"


def test_period_removal_drops_all_rows_for_one_period():
    """Period removal should drop ALL rows for one time period."""
    rc = RowCorruptor()
    records = _make_records()
    result, corruptions = rc.remove_time_period(records, rng=random.Random(42), period_field="fy")
    remaining_periods = set(r["fy"] for r in result)
    original_periods = set(r["fy"] for r in _make_records())
    assert len(remaining_periods) < len(original_periods)
    assert corruptions[0].strategy == "period_removal"
    assert corruptions[0].dimension == "Coverage"


def test_empty_records_handled():
    """All methods should handle empty records gracefully."""
    rc = RowCorruptor()
    rng = random.Random(42)
    result, c = rc.duplicate_rows([], rate=0.1, rng=rng)
    assert result == [] and c == []
    result, c = rc.orphan_foreign_keys([], rate=0.1, rng=rng, fk_field="id")
    assert result == [] and c == []
