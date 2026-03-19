"""Tests for collision resolution module."""

import json

from brightsmith.silver.concept_normalization.collision import CollisionResolver


def test_primary_concept_wins_over_secondary(tmp_path):
    """When a collision rule exists, primary_sources[0] should win."""
    rules = {
        "version": "1.0",
        "rules": {
            "Revenue": {
                "primary_sources": ["Revenues", "SalesRevenueNet", "RevenueFromContract"],
                "primary_unit": "USD",
                "resolution_strategy": "prefer_primary_order",
                "rationale": "Revenues is the most common GAAP tag",
                "approved_by": "human:jeff",
                "approved_at": "2026-03-19T00:00:00Z",
            },
        },
    }
    (tmp_path / "collision-rules.json").write_text(json.dumps(rules))

    resolver = CollisionResolver(tmp_path / "collision-rules.json")
    winner = resolver.resolve("Revenue", ["SalesRevenueNet", "Revenues", "RevenueFromContract"])
    assert winner == "Revenues"


def test_fallback_to_secondary_when_primary_absent(tmp_path):
    """If primary_sources[0] isn't in the list, fall back to next preferred."""
    rules = {
        "version": "1.0",
        "rules": {
            "Revenue": {
                "primary_sources": ["Revenues", "SalesRevenueNet"],
                "resolution_strategy": "prefer_primary_order",
                "rationale": "test",
            },
        },
    }
    (tmp_path / "collision-rules.json").write_text(json.dumps(rules))

    resolver = CollisionResolver(tmp_path / "collision-rules.json")
    winner = resolver.resolve("Revenue", ["SalesRevenueNet", "RevenueFromContract"])
    assert winner == "SalesRevenueNet"


def test_collision_rules_cover_all_multi_source_concepts(tmp_path):
    """get_uncovered_concepts should identify concepts without rules."""
    rules = {
        "version": "1.0",
        "rules": {
            "Revenue": {
                "primary_sources": ["Revenues"],
                "resolution_strategy": "prefer_primary_order",
                "rationale": "test",
            },
        },
    }
    (tmp_path / "collision-rules.json").write_text(json.dumps(rules))

    resolver = CollisionResolver(tmp_path / "collision-rules.json")
    multi_source = {
        "Revenue": ["Revenues", "SalesRevenueNet"],
        "NetIncome": ["NetIncomeLoss", "ProfitLoss"],
    }
    uncovered = resolver.get_uncovered_concepts(multi_source)
    assert uncovered == ["NetIncome"]


def test_resolution_produces_unique_grain(tmp_path):
    """Single source resolves to itself."""
    resolver = CollisionResolver()  # no rules file
    winner = resolver.resolve("Revenue", ["Revenues"])
    assert winner == "Revenues"


def test_no_rules_file_first_encountered_wins():
    """Without collision rules, first source in list wins."""
    resolver = CollisionResolver()
    winner = resolver.resolve("Revenue", ["SalesRevenueNet", "Revenues"])
    assert winner == "SalesRevenueNet"


def test_empty_sources_returns_none():
    """Empty source list returns None."""
    resolver = CollisionResolver()
    winner = resolver.resolve("Revenue", [])
    assert winner is None


def test_unapproved_rules_detected(tmp_path):
    """get_unapproved_rules returns rules without approved_by."""
    rules = {
        "version": "1.0",
        "rules": {
            "Revenue": {
                "primary_sources": ["Revenues"],
                "resolution_strategy": "prefer_primary_order",
                "rationale": "test",
                "approved_by": None,
                "approved_at": None,
            },
        },
    }
    (tmp_path / "collision-rules.json").write_text(json.dumps(rules))

    resolver = CollisionResolver(tmp_path / "collision-rules.json")
    unapproved = resolver.get_unapproved_rules()
    assert len(unapproved) == 1
    assert unapproved[0].canonical_concept == "Revenue"
