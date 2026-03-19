"""Schema-agnostic corruption injector.

SchemaIntrospector reads a PyIceberg table schema and maps each column's
type to appropriate corruption strategies. ChaosInjector copies a table
to a shadow namespace and applies corruptions at a configurable rate.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.types import (
    BooleanType,
    DateType,
    DoubleType,
    FloatType,
    IntegerType,
    LongType,
    StringType,
    TimestampType,
    TimestamptzType,
)

from grist.infra.chaos_monkey.manifest import ChaosManifest, CorruptionRecord
from grist.infra.chaos_monkey.safety import SHADOW_PREFIX, SafetyGate
from grist.infra.iceberg_setup import append_data, get_or_create_table


# ---------------------------------------------------------------------------
# Type → corruption strategy mapping
# ---------------------------------------------------------------------------

def _corrupt_string(value: Any, rng: random.Random) -> tuple[Any, str]:
    strategy = rng.choice(["null", "empty", "unicode_garbage", "truncation"])
    if strategy == "null":
        return None, "null"
    elif strategy == "empty":
        return "", "empty_string"
    elif strategy == "unicode_garbage":
        return "".join(chr(rng.randint(0x4E00, 0x9FFF)) for _ in range(5)), "unicode_garbage"
    else:
        if isinstance(value, str) and len(value) > 2:
            return value[: len(value) // 2], "truncation"
        return "", "truncation_to_empty"


def _corrupt_double(value: Any, rng: random.Random) -> tuple[Any, str]:
    strategy = rng.choice(["null", "negative", "nan", "extreme", "zero"])
    if strategy == "null":
        return None, "null"
    elif strategy == "negative":
        v = float(value) if value is not None else 1.0
        return -abs(v), "negative"
    elif strategy == "nan":
        return float("nan"), "nan"
    elif strategy == "extreme":
        return 9.999e15, "extreme_value"
    else:
        return 0.0, "zero"


def _corrupt_integer(value: Any, rng: random.Random) -> tuple[Any, str]:
    strategy = rng.choice(["null", "negative", "overflow", "zero"])
    if strategy == "null":
        return None, "null"
    elif strategy == "negative":
        v = int(value) if value is not None else 1
        return -abs(v), "negative"
    elif strategy == "overflow":
        return 2**31 - 1, "overflow"
    else:
        return 0, "zero"


def _corrupt_date(value: Any, rng: random.Random) -> tuple[Any, str]:
    strategy = rng.choice(["null", "future", "epoch", "far_past"])
    if strategy == "null":
        return None, "null"
    elif strategy == "future":
        return date(2099, 12, 31), "future_date"
    elif strategy == "epoch":
        return date(1970, 1, 1), "epoch"
    else:
        return date(1900, 1, 1), "far_past"


def _corrupt_timestamp(value: Any, rng: random.Random) -> tuple[Any, str]:
    strategy = rng.choice(["null", "future", "epoch"])
    if strategy == "null":
        return None, "null"
    elif strategy == "future":
        return datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc), "future_timestamp"
    else:
        return datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc), "epoch_timestamp"


def _corrupt_boolean(value: Any, rng: random.Random) -> tuple[Any, str]:
    return None, "null"


# Map PyIceberg types to corruption functions
_CORRUPTION_MAP: dict[type, callable] = {
    StringType: _corrupt_string,
    DoubleType: _corrupt_double,
    FloatType: _corrupt_double,
    IntegerType: _corrupt_integer,
    LongType: _corrupt_integer,
    DateType: _corrupt_date,
    TimestampType: _corrupt_timestamp,
    TimestamptzType: _corrupt_timestamp,
    BooleanType: _corrupt_boolean,
}


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------

@dataclass
class ColumnProfile:
    """Profile of a single column for corruption targeting."""

    name: str
    iceberg_type: type
    required: bool
    corruption_fn: callable | None


class SchemaIntrospector:
    """Reads a PyIceberg schema and maps columns to corruption strategies."""

    def __init__(self, schema: Schema):
        self.schema = schema
        self.columns = self._profile_columns()

    def _profile_columns(self) -> list[ColumnProfile]:
        profiles = []
        for f in self.schema.fields:
            type_class = type(f.field_type)
            profiles.append(
                ColumnProfile(
                    name=f.name,
                    iceberg_type=type_class,
                    required=f.required,
                    corruption_fn=_CORRUPTION_MAP.get(type_class),
                )
            )
        return profiles

    @property
    def corruptible_columns(self) -> list[ColumnProfile]:
        """Columns that have a known corruption strategy."""
        return [c for c in self.columns if c.corruption_fn is not None]


# ---------------------------------------------------------------------------
# Injector
# ---------------------------------------------------------------------------

@dataclass
class InjectionConfig:
    """Configuration for a chaos monkey run."""

    rate: float = 0.07
    """Fraction of rows to corrupt (0.0-1.0)."""

    seed: int | None = None
    """Random seed for reproducibility."""

    max_corruptions_per_row: int = 3
    """Maximum number of columns to corrupt in a single row."""


class ChaosInjector:
    """Copies an Iceberg table to a shadow namespace and injects corruptions."""

    def __init__(self, catalog, config: InjectionConfig | None = None):
        self.catalog = catalog
        self.config = config or InjectionConfig()
        self.rng = random.Random(self.config.seed)

    def inject(
        self,
        source_namespace: str,
        source_table: str,
        records: list[dict],
    ) -> tuple[list[dict], ChaosManifest]:
        """Inject corruptions into a copy of the data.

        Args:
            source_namespace: Original table namespace (e.g., 'base').
            source_table: Original table name (e.g., 'financial_facts').
            records: Original records read from the source table.

        Returns:
            (corrupted_records, manifest) — the corrupted data and a manifest
            documenting every corruption.
        """
        shadow_ns = f"{SHADOW_PREFIX}{source_namespace}"
        SafetyGate.check(shadow_ns)

        # Load source schema for introspection
        source = self.catalog.load_table(f"{source_namespace}.{source_table}")
        introspector = SchemaIntrospector(source.schema())
        corruptible = introspector.corruptible_columns

        if not corruptible:
            raise ValueError(f"No corruptible columns found in {source_namespace}.{source_table}")

        # Determine which rows to corrupt
        n_corrupt = max(1, int(len(records) * self.config.rate))
        corrupt_indices = set(self.rng.sample(range(len(records)), min(n_corrupt, len(records))))

        manifest = ChaosManifest(
            source_table=f"{source_namespace}.{source_table}",
            shadow_table=f"{shadow_ns}.{source_table}",
            total_rows=len(records),
            corruption_rate=self.config.rate,
            seed=self.config.seed,
        )

        # Apply corruptions
        corrupted = []
        for i, record in enumerate(records):
            row = dict(record)  # copy
            if i in corrupt_indices:
                n_cols = self.rng.randint(1, min(self.config.max_corruptions_per_row, len(corruptible)))
                targets = self.rng.sample(corruptible, n_cols)
                for col in targets:
                    original = row.get(col.name)
                    corrupted_val, strategy = col.corruption_fn(original, self.rng)
                    # Skip null injection on required fields to avoid schema violations
                    if col.required and corrupted_val is None:
                        continue
                    manifest.add_corruption(CorruptionRecord(
                        row_index=i,
                        column=col.name,
                        original_value=str(original),
                        corrupted_value=str(corrupted_val),
                        strategy=strategy,
                        dimension=_strategy_to_dimension(strategy),
                    ))
                    row[col.name] = corrupted_val
            corrupted.append(row)

        # Write to shadow table
        shadow_table = get_or_create_table(
            self.catalog, shadow_ns, source_table, source.schema()
        )
        if corrupted:
            append_data(shadow_table, corrupted)

        return corrupted, manifest


def _strategy_to_dimension(strategy: str) -> str:
    """Map a corruption strategy to its DQ dimension.

    Covers all 10 dimensions:
    1. Completeness, 2. Validity, 3. Accuracy, 4. Reasonableness, 5. Freshness,
    6. Uniqueness, 7. Referential Integrity, 8. Coverage, 9. Consistency,
    10. Distribution.
    """
    mapping = {
        # Original 5 dimensions (cell-level)
        "null": "Completeness",
        "empty_string": "Completeness",
        "unicode_garbage": "Validity",
        "truncation": "Accuracy",
        "truncation_to_empty": "Completeness",
        "negative": "Validity",
        "nan": "Validity",
        "extreme_value": "Reasonableness",
        "zero": "Accuracy",
        "overflow": "Reasonableness",
        "future_date": "Freshness",
        "epoch": "Freshness",
        "far_past": "Freshness",
        "future_timestamp": "Freshness",
        "epoch_timestamp": "Freshness",
        # New 5 dimensions (cross-row, semantic, distribution)
        "exact_duplicate": "Uniqueness",
        "near_duplicate": "Uniqueness",
        "orphan_fk": "Referential Integrity",
        "entity_removal": "Coverage",
        "period_removal": "Coverage",
        "column_swap": "Consistency",
        "entity_mix": "Consistency",
        "temporal_shift": "Consistency",
        "value_spike": "Distribution",
        "sign_flip": "Distribution",
        "uniform_dates": "Distribution",
    }
    return mapping.get(strategy, "Validity")
