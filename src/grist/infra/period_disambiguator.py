"""Temporal period classification for date-span analysis.

Any domain with temporal data (financial filings, healthcare encounters, IoT
time-series) needs to distinguish annual vs quarterly vs monthly vs point-in-time
records. This utility provides that classification using date-span analysis
rather than ad-hoc label parsing.

Usage:
    from grist.infra.period_disambiguator import PeriodDisambiguator, PeriodThresholds

    pd = PeriodDisambiguator()  # default thresholds
    result = pd.classify(date(2009, 9, 27), date(2010, 9, 25))
    # -> PeriodClassification(period_type='annual', duration_days=363, confidence=0.97)

    # Domain projects override thresholds:
    pd = PeriodDisambiguator(PeriodThresholds(annual_min=350))
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


PeriodType = Literal["annual", "quarterly", "monthly", "point_in_time", "unknown"]


@dataclass(frozen=True)
class PeriodThresholds:
    """Configurable thresholds for period classification.

    Domain projects override these defaults when fiscal calendars or
    reporting conventions differ from standard ranges.
    """

    annual_min: int = 300
    """Minimum days for annual classification (default 300 to handle ~52-week fiscal years)."""

    annual_max: int = 400
    """Maximum days for annual classification (handles 53-week fiscal years + leap)."""

    quarterly_min: int = 60
    """Minimum days for quarterly classification."""

    quarterly_max: int = 120
    """Maximum days for quarterly classification."""

    monthly_min: int = 25
    """Minimum days for monthly classification."""

    monthly_max: int = 35
    """Maximum days for monthly classification."""


@dataclass(frozen=True)
class PeriodClassification:
    """Result of classifying a date range."""

    period_type: PeriodType
    duration_days: int
    confidence: float
    """0.0-1.0 confidence in the classification."""


class PeriodDisambiguator:
    """Classifies date ranges into period types using span analysis.

    The core insight: a 365-day span is annual regardless of what the source
    system labels it. This avoids the class of bugs where label-based period
    identification picks the wrong row from multiple overlapping periods.
    """

    def __init__(self, thresholds: PeriodThresholds | None = None):
        self.thresholds = thresholds or PeriodThresholds()

    def classify(self, start_date: date, end_date: date) -> PeriodClassification:
        """Classify a single date range by its span.

        Args:
            start_date: Period start (inclusive).
            end_date: Period end (inclusive).

        Returns:
            PeriodClassification with type, duration, and confidence.
        """
        if start_date is None or end_date is None:
            return PeriodClassification(
                period_type="unknown", duration_days=0, confidence=0.0
            )

        duration = (end_date - start_date).days
        t = self.thresholds

        if duration == 0:
            return PeriodClassification(
                period_type="point_in_time", duration_days=0, confidence=1.0
            )

        if t.annual_min <= duration <= t.annual_max:
            # Confidence peaks at 365, falls off toward edges
            center = (t.annual_min + t.annual_max) / 2
            spread = (t.annual_max - t.annual_min) / 2
            conf = max(0.5, 1.0 - abs(duration - center) / spread * 0.5)
            return PeriodClassification(
                period_type="annual", duration_days=duration, confidence=round(conf, 2)
            )

        if t.quarterly_min <= duration <= t.quarterly_max:
            center = (t.quarterly_min + t.quarterly_max) / 2
            spread = (t.quarterly_max - t.quarterly_min) / 2
            conf = max(0.5, 1.0 - abs(duration - center) / spread * 0.5)
            return PeriodClassification(
                period_type="quarterly",
                duration_days=duration,
                confidence=round(conf, 2),
            )

        if t.monthly_min <= duration <= t.monthly_max:
            center = (t.monthly_min + t.monthly_max) / 2
            spread = (t.monthly_max - t.monthly_min) / 2
            conf = max(0.5, 1.0 - abs(duration - center) / spread * 0.5)
            return PeriodClassification(
                period_type="monthly",
                duration_days=duration,
                confidence=round(conf, 2),
            )

        return PeriodClassification(
            period_type="unknown", duration_days=duration, confidence=0.0
        )

    def classify_batch(
        self,
        records: list[dict],
        start_col: str,
        end_col: str,
    ) -> list[PeriodClassification]:
        """Classify a batch of records by their date spans.

        Args:
            records: List of dicts containing start and end date fields.
            start_col: Name of the start date column.
            end_col: Name of the end date column.

        Returns:
            List of PeriodClassification, one per record (same order).
        """
        return [
            self.classify(r.get(start_col), r.get(end_col)) for r in records
        ]

    def select_primary(
        self,
        facts: list[dict],
        entity_col: str,
        metric_col: str,
        period_col: str,
        start_col: str,
        end_col: str,
        target_type: PeriodType = "annual",
    ) -> list[dict]:
        """Select one fact per entity-metric-period matching the target period type.

        Given multiple facts for the same grain (e.g., 11 rows for Apple FY2010
        revenue with spans from 91d to 365d), selects only the row whose date
        span matches the target period type.

        Args:
            facts: List of fact dicts.
            entity_col: Column identifying the entity (e.g., 'cik').
            metric_col: Column identifying the metric (e.g., 'concept').
            period_col: Column identifying the period label (e.g., 'fiscal_year').
            start_col: Column with period start date.
            end_col: Column with period end date.
            target_type: Which period type to keep (default 'annual').

        Returns:
            Filtered list containing only facts matching the target period type.
            When multiple facts for the same grain match, the one with highest
            confidence is kept.
        """
        # Classify all facts
        classifications = self.classify_batch(facts, start_col, end_col)

        # Filter to target type
        candidates: dict[tuple, list[tuple[dict, PeriodClassification]]] = {}
        for fact, cls in zip(facts, classifications):
            if cls.period_type != target_type:
                continue
            key = (fact.get(entity_col), fact.get(metric_col), fact.get(period_col))
            candidates.setdefault(key, []).append((fact, cls))

        # For each grain, pick highest confidence
        result = []
        for group in candidates.values():
            best = max(group, key=lambda x: x[1].confidence)
            result.append(best[0])

        return result

    def as_duckdb_sql(
        self,
        table: str,
        start_col: str,
        end_col: str,
        output_col: str = "period_type",
    ) -> str:
        """Generate a DuckDB SQL CASE expression for period classification.

        Returns a SQL fragment that can be embedded in SELECT or WHERE clauses.

        Args:
            table: Table name or alias for qualifying columns.
            start_col: Start date column name.
            end_col: End date column name.
            output_col: Name for the computed column.

        Returns:
            SQL CASE expression string.
        """
        t = self.thresholds
        return (
            f"CASE\n"
            f"  WHEN {table}.{end_col} - {table}.{start_col} = 0 THEN 'point_in_time'\n"
            f"  WHEN {table}.{end_col} - {table}.{start_col} "
            f"BETWEEN {t.annual_min} AND {t.annual_max} THEN 'annual'\n"
            f"  WHEN {table}.{end_col} - {table}.{start_col} "
            f"BETWEEN {t.quarterly_min} AND {t.quarterly_max} THEN 'quarterly'\n"
            f"  WHEN {table}.{end_col} - {table}.{start_col} "
            f"BETWEEN {t.monthly_min} AND {t.monthly_max} THEN 'monthly'\n"
            f"  ELSE 'unknown'\n"
            f"END AS {output_col}"
        )
