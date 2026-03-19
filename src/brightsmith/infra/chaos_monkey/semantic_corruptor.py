"""Semantic corruption strategies.

Corruptions that are structurally valid but semantically wrong — column
swaps, entity mixing, temporal shifts. These pass type checks but produce
incorrect business results.
"""

from __future__ import annotations

import random
from datetime import timedelta

from brightsmith.infra.chaos_monkey.manifest import CorruptionRecord


class SemanticCorruptor:
    """Corruptions that are structurally valid but semantically wrong."""

    def swap_columns(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
        col_a: str,
        col_b: str,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Swap values between two related columns.

        E.g., swap revenue and net_income — both are doubles, structurally
        valid, but semantically wrong.
        """
        if not records:
            return records, []
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), min(n, len(records))))
        corruptions = []
        for i in indices:
            orig_a = records[i].get(col_a)
            orig_b = records[i].get(col_b)
            records[i][col_a], records[i][col_b] = orig_b, orig_a
            corruptions.append(CorruptionRecord(
                row_index=i,
                column=f"{col_a}<->{col_b}",
                original_value=f"{col_a}={orig_a}, {col_b}={orig_b}",
                corrupted_value=f"{col_a}={orig_b}, {col_b}={orig_a}",
                strategy="column_swap",
                dimension="Consistency",
            ))
        return records, corruptions

    def mix_entities(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
        entity_field: str,
        value_fields: list[str],
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Assign one entity's values to another entity.

        Apple gets Microsoft's revenue — structurally valid, semantically wrong.
        """
        entities = list(set(r.get(entity_field) for r in records if r.get(entity_field)))
        if len(entities) < 2:
            return records, []
        source, target = rng.sample(entities, 2)
        source_rows = [r for r in records if r.get(entity_field) == source]
        if not source_rows:
            return records, []

        n = max(1, int(len(records) * rate))
        corruptions = []
        count = 0
        for i, row in enumerate(records):
            if row.get(entity_field) == target and count < n:
                donor = rng.choice(source_rows)
                for vf in value_fields:
                    if vf in donor:
                        row[vf] = donor[vf]
                corruptions.append(CorruptionRecord(
                    row_index=i,
                    column=",".join(value_fields),
                    original_value=f"entity={target}",
                    corrupted_value=f"values from entity={source}",
                    strategy="entity_mix",
                    dimension="Consistency",
                ))
                count += 1
        return records, corruptions

    def shift_temporal(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
        date_fields: list[str],
        shift_days: int = 365,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Shift dates by a fixed amount. Makes temporal joins fail."""
        if not records:
            return records, []
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), min(n, len(records))))
        corruptions = []
        for i in indices:
            for df in date_fields:
                val = records[i].get(df)
                if val is not None and hasattr(val, "year"):
                    original = str(val)
                    records[i][df] = val + timedelta(days=shift_days)
                    corruptions.append(CorruptionRecord(
                        row_index=i,
                        column=df,
                        original_value=original,
                        corrupted_value=str(records[i][df]),
                        strategy="temporal_shift",
                        dimension="Consistency",
                    ))
        return records, corruptions
