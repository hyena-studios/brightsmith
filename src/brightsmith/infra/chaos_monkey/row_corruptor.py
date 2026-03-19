"""Row-level corruption strategies.

Corruptions that affect whole rows or row relationships — duplicates,
orphan keys, entity removal, period removal. These test cross-row DQ
rules that per-cell corruption misses entirely.
"""

from __future__ import annotations

import random

from brightsmith.infra.chaos_monkey.manifest import CorruptionRecord


class RowCorruptor:
    """Corruptions that affect whole rows or row relationships."""

    def duplicate_rows(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Insert exact duplicate rows. Tests uniqueness rules."""
        if not records:
            return records, []
        n = max(1, int(len(records) * rate))
        indices = rng.sample(range(len(records)), min(n, len(records)))
        duplicates = [dict(records[i]) for i in indices]
        corruptions = [
            CorruptionRecord(
                row_index=len(records) + i,
                column="*",
                original_value="(new row)",
                corrupted_value="(exact duplicate)",
                strategy="exact_duplicate",
                dimension="Uniqueness",
            )
            for i in range(len(duplicates))
        ]
        return records + duplicates, corruptions

    def near_duplicate_rows(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
        grain_fields: list[str],
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Insert rows that match on grain but differ in a non-grain field.

        Tests whether DQ rules distinguish grain uniqueness from full-row uniqueness.
        """
        if not records:
            return records, []
        n = max(1, int(len(records) * rate * 0.5))
        indices = rng.sample(range(len(records)), min(n, len(records)))
        near_dupes = []
        corruptions = []
        for idx, i in enumerate(indices):
            row = dict(records[i])
            non_grain = [k for k in row if k not in grain_fields]
            if non_grain:
                col = rng.choice(non_grain)
                row[col] = None
            near_dupes.append(row)
            corruptions.append(CorruptionRecord(
                row_index=len(records) + idx,
                column="*",
                original_value="(new row)",
                corrupted_value="(near duplicate, non-grain field nulled)",
                strategy="near_duplicate",
                dimension="Uniqueness",
            ))
        return records + near_dupes, corruptions

    def orphan_foreign_keys(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
        fk_field: str,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Set FK fields to values that don't exist in the parent table.

        Tests referential integrity rules.
        """
        if not records:
            return records, []
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), min(n, len(records))))
        corruptions = []
        for i in indices:
            original = str(records[i].get(fk_field, ""))
            records[i][fk_field] = f"ORPHAN_{rng.randint(100000, 999999)}"
            corruptions.append(CorruptionRecord(
                row_index=i,
                column=fk_field,
                original_value=original,
                corrupted_value=str(records[i][fk_field]),
                strategy="orphan_fk",
                dimension="Referential Integrity",
            ))
        return records, corruptions

    def remove_entity(
        self,
        records: list[dict],
        rng: random.Random,
        entity_field: str,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Remove all rows for one entity. Tests coverage/volume rules."""
        entities = list(set(r.get(entity_field) for r in records if r.get(entity_field)))
        if not entities:
            return records, []
        victim = rng.choice(entities)
        filtered = [r for r in records if r.get(entity_field) != victim]
        removed = len(records) - len(filtered)
        return filtered, [CorruptionRecord(
            row_index=-1,
            column=entity_field,
            original_value=str(victim),
            corrupted_value=f"(removed {removed} rows)",
            strategy="entity_removal",
            dimension="Coverage",
        )]

    def remove_time_period(
        self,
        records: list[dict],
        rng: random.Random,
        period_field: str,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Remove all rows for one time period. Tests temporal coverage."""
        periods = list(set(r.get(period_field) for r in records if r.get(period_field)))
        if not periods:
            return records, []
        victim = rng.choice(periods)
        filtered = [r for r in records if r.get(period_field) != victim]
        removed = len(records) - len(filtered)
        return filtered, [CorruptionRecord(
            row_index=-1,
            column=period_field,
            original_value=str(victim),
            corrupted_value=f"(removed {removed} rows)",
            strategy="period_removal",
            dimension="Coverage",
        )]
