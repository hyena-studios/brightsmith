"""Chaos monkey manifest — records every corruption for reconciliation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CorruptionRecord:
    """A single corruption event."""

    row_index: int
    column: str
    original_value: str
    corrupted_value: str
    strategy: str
    dimension: str


@dataclass
class ChaosManifest:
    """Complete manifest of a chaos monkey injection run."""

    source_table: str
    shadow_table: str
    total_rows: int
    corruption_rate: float
    seed: int | None
    corruptions: list[CorruptionRecord] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_corruption(self, record: CorruptionRecord) -> None:
        self.corruptions.append(record)

    @property
    def rows_corrupted(self) -> int:
        return len(set(c.row_index for c in self.corruptions))

    @property
    def columns_corrupted(self) -> int:
        return len(set(c.column for c in self.corruptions))

    @property
    def dimensions_covered(self) -> set[str]:
        return set(c.dimension for c in self.corruptions)

    def to_dict(self) -> dict:
        return {
            "source_table": self.source_table,
            "shadow_table": self.shadow_table,
            "total_rows": self.total_rows,
            "corruption_rate": self.corruption_rate,
            "seed": self.seed,
            "created_at": self.created_at,
            "summary": {
                "rows_corrupted": self.rows_corrupted,
                "columns_corrupted": self.columns_corrupted,
                "total_corruptions": len(self.corruptions),
                "dimensions_covered": sorted(self.dimensions_covered),
            },
            "corruptions": [
                {
                    "row_index": c.row_index,
                    "column": c.column,
                    "original_value": c.original_value,
                    "corrupted_value": c.corrupted_value,
                    "strategy": c.strategy,
                    "dimension": c.dimension,
                }
                for c in self.corruptions
            ],
        }

    def save(self, path: Path) -> Path:
        """Save manifest as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        return path

    @classmethod
    def from_file(cls, path: Path) -> ChaosManifest:
        """Load manifest from JSON file."""
        data = json.loads(path.read_text())
        manifest = cls(
            source_table=data["source_table"],
            shadow_table=data["shadow_table"],
            total_rows=data["total_rows"],
            corruption_rate=data["corruption_rate"],
            seed=data["seed"],
            created_at=data.get("created_at", ""),
        )
        for c in data.get("corruptions", []):
            manifest.add_corruption(CorruptionRecord(**c))
        return manifest
