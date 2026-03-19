"""Distribution corruption strategies.

Corruptions that change statistical properties without obviously corrupting
individual values — value spikes, sign flips, uniform dates. These test
distribution-based DQ rules that per-cell checks miss.
"""

from __future__ import annotations

import random
from datetime import date

from grist.infra.chaos_monkey.manifest import CorruptionRecord


class DistributionCorruptor:
    """Corruptions that change statistical properties."""

    def spike_values(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
        value_field: str,
        spike_value: float = 999999.0,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Set many values to the same number. Kills variance.

        Tests distribution-based DQ rules (variance, standard deviation).
        """
        if not records:
            return records, []
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), min(n, len(records))))
        corruptions = []
        for i in indices:
            original = records[i].get(value_field)
            records[i][value_field] = spike_value
            corruptions.append(CorruptionRecord(
                row_index=i,
                column=value_field,
                original_value=str(original),
                corrupted_value=str(spike_value),
                strategy="value_spike",
                dimension="Distribution",
            ))
        return records, corruptions

    def flip_signs(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
        value_field: str,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Negate values. Revenue becomes negative revenue.

        Tests domain-specific validity rules (sign constraints).
        """
        if not records:
            return records, []
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), min(n, len(records))))
        corruptions = []
        for i in indices:
            v = records[i].get(value_field)
            if v is not None and isinstance(v, (int, float)):
                original = str(v)
                records[i][value_field] = -v
                corruptions.append(CorruptionRecord(
                    row_index=i,
                    column=value_field,
                    original_value=original,
                    corrupted_value=str(-v),
                    strategy="sign_flip",
                    dimension="Distribution",
                ))
        return records, corruptions

    def uniform_dates(
        self,
        records: list[dict],
        rate: float,
        rng: random.Random,
        date_field: str,
        target_date: date | None = None,
    ) -> tuple[list[dict], list[CorruptionRecord]]:
        """Set all dates to the same day. Kills temporal distribution.

        Tests temporal coverage and distribution rules.
        """
        if not records:
            return records, []
        target = target_date or date(2020, 1, 1)
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), min(n, len(records))))
        corruptions = []
        for i in indices:
            original = records[i].get(date_field)
            records[i][date_field] = target
            corruptions.append(CorruptionRecord(
                row_index=i,
                column=date_field,
                original_value=str(original),
                corrupted_value=str(target),
                strategy="uniform_dates",
                dimension="Distribution",
            ))
        return records, corruptions
