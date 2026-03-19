"""Tests for distribution corruption strategies."""

import random
from datetime import date

from grist.infra.chaos_monkey.distribution_corruptor import DistributionCorruptor


def _make_records():
    return [
        {"value": 100.0, "filed": date(2024, 1, 15)},
        {"value": 200.0, "filed": date(2024, 2, 15)},
        {"value": 300.0, "filed": date(2024, 3, 15)},
        {"value": 400.0, "filed": date(2024, 4, 15)},
        {"value": 500.0, "filed": date(2024, 5, 15)},
    ]


def test_value_spike_sets_uniform_values():
    """Value spiking should set many values to the same number."""
    dc = DistributionCorruptor()
    records = _make_records()
    result, corruptions = dc.spike_values(
        records, rate=0.8, rng=random.Random(42), value_field="value", spike_value=999.0,
    )
    spiked_count = sum(1 for r in result if r["value"] == 999.0)
    assert spiked_count > 0
    assert all(c.strategy == "value_spike" for c in corruptions)
    assert all(c.dimension == "Distribution" for c in corruptions)


def test_sign_flip_negates_values():
    """Sign flipping should negate numeric values."""
    dc = DistributionCorruptor()
    records = _make_records()
    result, corruptions = dc.flip_signs(
        records, rate=0.8, rng=random.Random(42), value_field="value",
    )
    negative_count = sum(1 for r in result if r["value"] < 0)
    assert negative_count > 0
    assert all(c.strategy == "sign_flip" for c in corruptions)
    assert all(c.dimension == "Distribution" for c in corruptions)


def test_uniform_dates_kills_temporal_variance():
    """Uniform dates should set all dates to the same day."""
    dc = DistributionCorruptor()
    records = _make_records()
    result, corruptions = dc.uniform_dates(
        records, rate=0.8, rng=random.Random(42), date_field="filed",
    )
    uniform_count = sum(1 for r in result if r["filed"] == date(2020, 1, 1))
    assert uniform_count > 0
    assert all(c.strategy == "uniform_dates" for c in corruptions)
    assert all(c.dimension == "Distribution" for c in corruptions)


def test_empty_records_handled():
    """All methods should handle empty records gracefully."""
    dc = DistributionCorruptor()
    rng = random.Random(42)
    result, c = dc.spike_values([], rate=0.1, rng=rng, value_field="v")
    assert result == [] and c == []
    result, c = dc.flip_signs([], rate=0.1, rng=rng, value_field="v")
    assert result == [] and c == []
    result, c = dc.uniform_dates([], rate=0.1, rng=rng, date_field="d")
    assert result == [] and c == []


def test_sign_flip_skips_none_values():
    """Sign flip should skip None values without crashing."""
    dc = DistributionCorruptor()
    records = [{"value": None}, {"value": 100.0}]
    result, corruptions = dc.flip_signs(
        records, rate=1.0, rng=random.Random(42), value_field="value",
    )
    # Should only flip the non-None value
    assert len(corruptions) <= 1
