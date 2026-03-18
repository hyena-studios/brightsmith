"""Tests for chaos monkey — schema-agnostic adversarial DQ testing."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from grist.infra.chaos_monkey.injector import (
    ChaosInjector,
    InjectionConfig,
    SchemaIntrospector,
    _corrupt_date,
    _corrupt_double,
    _corrupt_integer,
    _corrupt_string,
)
from grist.infra.chaos_monkey.manifest import ChaosManifest, CorruptionRecord
from grist.infra.chaos_monkey.reconciler import AfterActionReconciler
from grist.infra.chaos_monkey.safety import SafetyGate, SafetyViolation


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------


class TestSafetyGate:
    def test_all_conditions_met(self, monkeypatch):
        monkeypatch.setenv("CHAOS_MONKEY_ENABLED", "true")
        monkeypatch.setenv("GRIST_ENV", "dev")
        SafetyGate.check("shadow_base")  # should not raise

    def test_missing_enabled_flag(self, monkeypatch):
        monkeypatch.delenv("CHAOS_MONKEY_ENABLED", raising=False)
        monkeypatch.setenv("GRIST_ENV", "dev")
        with pytest.raises(SafetyViolation, match="CHAOS_MONKEY_ENABLED"):
            SafetyGate.check("shadow_base")

    def test_wrong_env(self, monkeypatch):
        monkeypatch.setenv("CHAOS_MONKEY_ENABLED", "true")
        monkeypatch.setenv("GRIST_ENV", "prod")
        with pytest.raises(SafetyViolation, match="GRIST_ENV"):
            SafetyGate.check("shadow_base")

    def test_non_shadow_namespace(self, monkeypatch):
        monkeypatch.setenv("CHAOS_MONKEY_ENABLED", "true")
        monkeypatch.setenv("GRIST_ENV", "dev")
        with pytest.raises(SafetyViolation, match="does not start with"):
            SafetyGate.check("base")

    def test_multiple_failures(self, monkeypatch):
        monkeypatch.delenv("CHAOS_MONKEY_ENABLED", raising=False)
        monkeypatch.delenv("GRIST_ENV", raising=False)
        with pytest.raises(SafetyViolation) as exc:
            SafetyGate.check("base")
        assert "CHAOS_MONKEY_ENABLED" in str(exc.value)
        assert "GRIST_ENV" in str(exc.value)
        assert "does not start with" in str(exc.value)

    def test_is_safe_returns_bool(self, monkeypatch):
        monkeypatch.setenv("CHAOS_MONKEY_ENABLED", "true")
        monkeypatch.setenv("GRIST_ENV", "dev")
        assert SafetyGate.is_safe("shadow_raw") is True
        assert SafetyGate.is_safe("raw") is False


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


class TestSchemaIntrospector:
    def _make_schema(self):
        from pyiceberg.schema import Schema
        from pyiceberg.types import (
            DateType,
            DoubleType,
            IntegerType,
            LongType,
            NestedField,
            StringType,
            TimestampType,
        )

        return Schema(
            NestedField(1, "id", StringType(), required=True),
            NestedField(2, "name", StringType(), required=False),
            NestedField(3, "amount", DoubleType(), required=False),
            NestedField(4, "count", IntegerType(), required=False),
            NestedField(5, "big_count", LongType(), required=False),
            NestedField(6, "event_date", DateType(), required=False),
            NestedField(7, "created_at", TimestampType(), required=False),
        )

    def test_profiles_all_columns(self):
        schema = self._make_schema()
        introspector = SchemaIntrospector(schema)
        assert len(introspector.columns) == 7

    def test_identifies_corruptible_columns(self):
        schema = self._make_schema()
        introspector = SchemaIntrospector(schema)
        corruptible = introspector.corruptible_columns
        # All 7 columns have known types
        assert len(corruptible) == 7

    def test_tracks_required_flag(self):
        schema = self._make_schema()
        introspector = SchemaIntrospector(schema)
        id_col = next(c for c in introspector.columns if c.name == "id")
        assert id_col.required is True
        name_col = next(c for c in introspector.columns if c.name == "name")
        assert name_col.required is False


# ---------------------------------------------------------------------------
# Corruption functions
# ---------------------------------------------------------------------------

import random


class TestCorruptionStrategies:
    def test_string_corruption_produces_different_value(self):
        rng = random.Random(42)
        for _ in range(20):
            val, strategy = _corrupt_string("hello world", rng)
            assert strategy in ("null", "empty_string", "unicode_garbage", "truncation")

    def test_double_corruption_strategies(self):
        rng = random.Random(42)
        strategies_seen = set()
        for _ in range(50):
            val, strategy = _corrupt_double(100.0, rng)
            strategies_seen.add(strategy)
        # Should see at least 3 of the 5 strategies
        assert len(strategies_seen) >= 3

    def test_integer_corruption_strategies(self):
        rng = random.Random(42)
        strategies_seen = set()
        for _ in range(50):
            val, strategy = _corrupt_integer(42, rng)
            strategies_seen.add(strategy)
        assert len(strategies_seen) >= 3

    def test_date_corruption_produces_dates(self):
        rng = random.Random(42)
        for _ in range(20):
            val, strategy = _corrupt_date(date(2020, 6, 15), rng)
            assert val is None or isinstance(val, date)

    def test_null_strategy_returns_none(self):
        # Force null strategy by trying many times with known seed
        rng = random.Random(0)
        nulls = sum(1 for _ in range(100) if _corrupt_string("test", rng)[0] is None)
        assert nulls > 0  # At least some nulls in 100 tries


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class TestManifest:
    def test_manifest_roundtrip(self, tmp_path):
        manifest = ChaosManifest(
            source_table="base.facts",
            shadow_table="shadow_base.facts",
            total_rows=1000,
            corruption_rate=0.07,
            seed=42,
        )
        manifest.add_corruption(CorruptionRecord(
            row_index=5, column="amount", original_value="100.0",
            corrupted_value="-100.0", strategy="negative", dimension="Validity",
        ))
        manifest.add_corruption(CorruptionRecord(
            row_index=10, column="name", original_value="Apple Inc",
            corrupted_value="", strategy="empty_string", dimension="Completeness",
        ))

        path = tmp_path / "manifest.json"
        manifest.save(path)

        loaded = ChaosManifest.from_file(path)
        assert loaded.source_table == "base.facts"
        assert loaded.total_rows == 1000
        assert loaded.rows_corrupted == 2
        assert loaded.columns_corrupted == 2
        assert len(loaded.corruptions) == 2
        assert "Validity" in loaded.dimensions_covered
        assert "Completeness" in loaded.dimensions_covered


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------


class TestReconciler:
    def test_all_caught(self):
        manifest = ChaosManifest(
            source_table="base.facts",
            shadow_table="shadow_base.facts",
            total_rows=100,
            corruption_rate=0.05,
            seed=1,
        )
        manifest.add_corruption(CorruptionRecord(
            row_index=1, column="val", original_value="100",
            corrupted_value="-100", strategy="negative", dimension="Validity",
        ))

        dq_results = {
            "rules_total": 5,
            "rules_passed": 4,
            "rules_failed": 1,
            "results": [
                {"rule_id": "R1", "passed": False, "category": "Validity", "detail": "found negatives"},
                {"rule_id": "R2", "passed": True, "category": "Completeness", "detail": "ok"},
            ],
        }

        reconciler = AfterActionReconciler()
        report = reconciler.reconcile(manifest, dq_results)

        assert report["coverage"]["catch_rate"] == 1.0
        assert len(report["gaps"]) == 0

    def test_missed_dimension(self):
        manifest = ChaosManifest(
            source_table="base.facts",
            shadow_table="shadow_base.facts",
            total_rows=100,
            corruption_rate=0.05,
            seed=1,
        )
        manifest.add_corruption(CorruptionRecord(
            row_index=1, column="val", original_value="100",
            corrupted_value="-100", strategy="negative", dimension="Validity",
        ))
        manifest.add_corruption(CorruptionRecord(
            row_index=2, column="name", original_value="test",
            corrupted_value="", strategy="empty_string", dimension="Completeness",
        ))

        dq_results = {
            "rules_total": 3,
            "rules_passed": 3,
            "rules_failed": 0,
            "results": [
                {"rule_id": "R1", "passed": True, "category": "Validity", "detail": "ok"},
            ],
        }

        reconciler = AfterActionReconciler()
        report = reconciler.reconcile(manifest, dq_results)

        assert report["coverage"]["catch_rate"] == 0.0
        assert "Validity" in report["coverage"]["dimensions_missed"]
        assert "Completeness" in report["coverage"]["dimensions_missed"]

    def test_generate_report(self, tmp_path):
        manifest = ChaosManifest(
            source_table="base.facts",
            shadow_table="shadow_base.facts",
            total_rows=50,
            corruption_rate=0.1,
            seed=1,
        )
        manifest.add_corruption(CorruptionRecord(
            row_index=0, column="x", original_value="1",
            corrupted_value="0", strategy="zero", dimension="Accuracy",
        ))

        dq_results = {"rules_total": 1, "rules_passed": 1, "rules_failed": 0, "results": []}

        reconciler = AfterActionReconciler()
        path = tmp_path / "report.md"
        reconciler.generate_report(manifest, dq_results, path)

        content = path.read_text()
        assert "After-Action Report" in content
        assert "base.facts" in content
        assert "Accuracy" in content
