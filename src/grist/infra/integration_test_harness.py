"""Integration test harness — validates pipeline output against golden datasets.

Golden datasets contain independently verified reference values from
authoritative sources. The harness queries live Iceberg tables and compares
actual output to expected values.

Usage:
    from grist.infra.integration_test_harness import PipelineTestHarness

    harness = PipelineTestHarness(catalog)
    golden = harness.load_golden_dataset("governance/golden-datasets/my-spec-golden.json")
    result = harness.validate(golden)
    assert result.all_match, result.summary()
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class GoldenRecord:
    """A single known-correct reference value."""

    entity: str
    metric: str
    period: str
    expected_value: float
    tolerance: float
    tolerance_type: str  # "relative" or "absolute"
    source: str
    table: str
    notes: str = ""


@dataclass
class Mismatch:
    """A golden record that didn't match pipeline output."""

    record: GoldenRecord
    actual_value: float | None
    difference: float | None
    reason: str


@dataclass
class ValidationResult:
    """Result of validating golden records against pipeline output."""

    matches: list[GoldenRecord] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)
    missing_records: list[GoldenRecord] = field(default_factory=list)
    extra_info: dict = field(default_factory=dict)

    @property
    def all_match(self) -> bool:
        return len(self.mismatches) == 0 and len(self.missing_records) == 0

    @property
    def total(self) -> int:
        return len(self.matches) + len(self.mismatches) + len(self.missing_records)

    def summary(self) -> str:
        lines = [
            f"Golden Dataset Validation: {len(self.matches)}/{self.total} match",
        ]
        if self.mismatches:
            lines.append(f"  Mismatches ({len(self.mismatches)}):")
            for m in self.mismatches:
                lines.append(
                    f"    {m.record.entity}/{m.record.metric}/{m.record.period}: "
                    f"expected={m.record.expected_value}, actual={m.actual_value}, "
                    f"reason={m.reason}"
                )
        if self.missing_records:
            lines.append(f"  Missing ({len(self.missing_records)}):")
            for r in self.missing_records:
                lines.append(f"    {r.entity}/{r.metric}/{r.period}")
        return "\n".join(lines)


class PipelineTestHarness:
    """Validates pipeline output against golden datasets.

    Args:
        catalog: PyIceberg catalog for loading table metadata.
    """

    def __init__(self, catalog):
        self.catalog = catalog

    def load_golden_dataset(self, path: str | Path) -> list[GoldenRecord]:
        """Load golden records from a JSON file.

        Args:
            path: Path to the golden dataset JSON file.

        Returns:
            List of GoldenRecord instances.
        """
        data = json.loads(Path(path).read_text())
        table = data.get("table", "")
        records = []
        for r in data.get("records", []):
            records.append(
                GoldenRecord(
                    entity=r["entity"],
                    metric=r["metric"],
                    period=r["period"],
                    expected_value=float(r["expected_value"]),
                    tolerance=float(r.get("tolerance", 0.01)),
                    tolerance_type=r.get("tolerance_type", "relative"),
                    source=r.get("source", ""),
                    table=table,
                    notes=r.get("notes", ""),
                )
            )
        return records

    def validate(
        self,
        golden_records: list[GoldenRecord],
        entity_col: str = "entity",
        metric_col: str = "metric",
        period_col: str = "period",
        value_col: str = "value",
    ) -> ValidationResult:
        """Validate golden records against Iceberg table data.

        Args:
            golden_records: List of reference values to check.
            entity_col: Column name for entity identifier.
            metric_col: Column name for metric identifier.
            period_col: Column name for period identifier.
            value_col: Column name for the value to compare.

        Returns:
            ValidationResult with matches, mismatches, and missing records.
        """
        result = ValidationResult()

        # Group records by table
        by_table: dict[str, list[GoldenRecord]] = {}
        for r in golden_records:
            by_table.setdefault(r.table, []).append(r)

        for table_id, records in by_table.items():
            # Load table data via DuckDB
            ns, tbl = table_id.split(".")
            try:
                iceberg_table = self.catalog.load_table(table_id)
                arrow_data = iceberg_table.scan().to_arrow()
            except Exception:
                # Table doesn't exist — all records are missing
                result.missing_records.extend(records)
                continue

            con = duckdb.connect()
            con.register("tbl", arrow_data)

            for golden in records:
                query = (
                    f"SELECT {value_col} FROM tbl "
                    f"WHERE {entity_col} = ? AND {metric_col} = ? AND {period_col} = ?"
                )
                rows = con.execute(query, [golden.entity, golden.metric, golden.period]).fetchall()

                if not rows:
                    result.missing_records.append(golden)
                    continue

                actual = float(rows[0][0])

                if self._within_tolerance(actual, golden.expected_value, golden.tolerance, golden.tolerance_type):
                    result.matches.append(golden)
                else:
                    diff = actual - golden.expected_value
                    result.mismatches.append(
                        Mismatch(
                            record=golden,
                            actual_value=actual,
                            difference=diff,
                            reason=f"outside {golden.tolerance_type} tolerance of {golden.tolerance}",
                        )
                    )

            con.close()

        return result

    @staticmethod
    def _within_tolerance(
        actual: float,
        expected: float,
        tolerance: float,
        tolerance_type: str,
    ) -> bool:
        if tolerance_type == "absolute":
            return abs(actual - expected) <= tolerance
        else:
            # relative
            if expected == 0:
                return actual == 0
            return abs((actual - expected) / expected) <= tolerance
