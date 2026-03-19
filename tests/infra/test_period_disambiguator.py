"""Tests for PeriodDisambiguator — the root cause fix for period misclassification."""

from datetime import date

import pytest

from brightsmith.infra.period_disambiguator import (
    PeriodClassification,
    PeriodDisambiguator,
    PeriodThresholds,
)


@pytest.fixture
def pd():
    return PeriodDisambiguator()


# ---------------------------------------------------------------------------
# classify() — single date range
# ---------------------------------------------------------------------------


class TestClassify:
    def test_annual_365_days(self, pd):
        result = pd.classify(date(2009, 9, 27), date(2010, 9, 27))
        assert result.period_type == "annual"
        assert result.duration_days == 365
        assert result.confidence >= 0.8

    def test_annual_364_days_fiscal_year(self, pd):
        """Apple's fiscal year is ~364 days (52 weeks)."""
        result = pd.classify(date(2009, 9, 27), date(2010, 9, 25))
        assert result.period_type == "annual"
        assert result.duration_days == 363
        assert result.confidence >= 0.8

    def test_quarterly_91_days(self, pd):
        result = pd.classify(date(2010, 1, 1), date(2010, 4, 2))
        assert result.period_type == "quarterly"
        assert result.duration_days == 91
        assert result.confidence >= 0.8

    def test_quarterly_90_days(self, pd):
        result = pd.classify(date(2010, 7, 1), date(2010, 9, 29))
        assert result.period_type == "quarterly"
        assert result.duration_days == 90

    def test_monthly_30_days(self, pd):
        result = pd.classify(date(2010, 3, 1), date(2010, 3, 31))
        assert result.period_type == "monthly"
        assert result.duration_days == 30

    def test_point_in_time_zero_days(self, pd):
        result = pd.classify(date(2010, 9, 25), date(2010, 9, 25))
        assert result.period_type == "point_in_time"
        assert result.duration_days == 0
        assert result.confidence == 1.0

    def test_unknown_200_days(self, pd):
        """200 days doesn't fit any standard period."""
        result = pd.classify(date(2010, 1, 1), date(2010, 7, 20))
        assert result.period_type == "unknown"
        assert result.confidence == 0.0

    def test_none_start_date(self, pd):
        result = pd.classify(None, date(2010, 9, 25))
        assert result.period_type == "unknown"
        assert result.confidence == 0.0

    def test_none_end_date(self, pd):
        result = pd.classify(date(2010, 9, 25), None)
        assert result.period_type == "unknown"

    def test_edge_360_days(self, pd):
        """360-day span (some fiscal calendars) should still classify as annual."""
        result = pd.classify(date(2010, 1, 1), date(2010, 12, 27))
        assert result.period_type == "annual"
        assert result.duration_days == 360

    def test_leap_year_366_days(self, pd):
        result = pd.classify(date(2012, 1, 1), date(2013, 1, 1))
        assert result.period_type == "annual"
        assert result.duration_days == 366


# ---------------------------------------------------------------------------
# classify_batch()
# ---------------------------------------------------------------------------


class TestClassifyBatch:
    def test_mixed_periods(self, pd):
        records = [
            {"start": date(2010, 1, 1), "end": date(2010, 12, 31)},  # annual
            {"start": date(2010, 1, 1), "end": date(2010, 4, 1)},  # quarterly
            {"start": date(2010, 6, 15), "end": date(2010, 6, 15)},  # point_in_time
        ]
        results = pd.classify_batch(records, "start", "end")
        assert len(results) == 3
        assert results[0].period_type == "annual"
        assert results[1].period_type == "quarterly"
        assert results[2].period_type == "point_in_time"

    def test_empty_batch(self, pd):
        assert pd.classify_batch([], "start", "end") == []


# ---------------------------------------------------------------------------
# select_primary() — the Apple FY2010 scenario
# ---------------------------------------------------------------------------


class TestSelectPrimary:
    def test_apple_fy2010_scenario(self, pd):
        """The real-world scenario: 11 rows for Apple revenue FY2010.

        Only 1 has a 365-day span (the annual value). The rest are quarterly
        (91d), YTD (182d, 273d), and point-in-time (0d) periods. select_primary
        should return only the annual row.
        """
        facts = [
            # Annual (the one we want)
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2009, 9, 27), "end": date(2010, 9, 25), "value": 65225},
            # Q1
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2009, 9, 27), "end": date(2009, 12, 26), "value": 15683},
            # Q2
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2009, 12, 27), "end": date(2010, 3, 27), "value": 15680},
            # Q3
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2010, 3, 28), "end": date(2010, 6, 26), "value": 15700},
            # Q4 (derived, would be ~18162)
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2010, 6, 27), "end": date(2010, 9, 25), "value": 18162},
            # 6-month YTD
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2009, 9, 27), "end": date(2010, 3, 27), "value": 31363},
            # 9-month YTD
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2009, 9, 27), "end": date(2010, 6, 26), "value": 47063},
            # Point-in-time balance
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2010, 9, 25), "end": date(2010, 9, 25), "value": 65225},
            # Another quarterly with slightly different span
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2010, 1, 1), "end": date(2010, 3, 31), "value": 15680},
            # Short fragment
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2010, 7, 1), "end": date(2010, 7, 15), "value": 2000},
            # 45-day oddball
            {"cik": "320193", "concept": "Revenue", "fy": "2010",
             "start": date(2010, 8, 1), "end": date(2010, 9, 15), "value": 8000},
        ]

        result = pd.select_primary(
            facts,
            entity_col="cik",
            metric_col="concept",
            period_col="fy",
            start_col="start",
            end_col="end",
            target_type="annual",
        )

        assert len(result) == 1
        assert result[0]["value"] == 65225
        assert result[0]["start"] == date(2009, 9, 27)

    def test_select_quarterly(self, pd):
        """Select quarterly values instead of annual."""
        facts = [
            {"entity": "A", "metric": "Rev", "period": "Q1",
             "start": date(2010, 1, 1), "end": date(2010, 4, 1), "value": 100},
            {"entity": "A", "metric": "Rev", "period": "Q1",
             "start": date(2010, 1, 1), "end": date(2010, 12, 31), "value": 400},
        ]
        result = pd.select_primary(
            facts, "entity", "metric", "period", "start", "end",
            target_type="quarterly",
        )
        assert len(result) == 1
        assert result[0]["value"] == 100

    def test_multiple_entities(self, pd):
        """Each entity gets its own annual value."""
        facts = [
            {"entity": "AAPL", "metric": "Rev", "period": "2010",
             "start": date(2009, 10, 1), "end": date(2010, 9, 30), "value": 65225},
            {"entity": "AAPL", "metric": "Rev", "period": "2010",
             "start": date(2010, 7, 1), "end": date(2010, 9, 30), "value": 18162},
            {"entity": "MSFT", "metric": "Rev", "period": "2010",
             "start": date(2009, 7, 1), "end": date(2010, 6, 30), "value": 62484},
            {"entity": "MSFT", "metric": "Rev", "period": "2010",
             "start": date(2010, 4, 1), "end": date(2010, 6, 30), "value": 16039},
        ]
        result = pd.select_primary(
            facts, "entity", "metric", "period", "start", "end",
            target_type="annual",
        )
        assert len(result) == 2
        values = {r["entity"]: r["value"] for r in result}
        assert values["AAPL"] == 65225
        assert values["MSFT"] == 62484

    def test_no_matching_type(self, pd):
        """If no facts match the target type, return empty."""
        facts = [
            {"entity": "A", "metric": "Rev", "period": "Q1",
             "start": date(2010, 1, 1), "end": date(2010, 4, 1), "value": 100},
        ]
        result = pd.select_primary(
            facts, "entity", "metric", "period", "start", "end",
            target_type="annual",
        )
        assert result == []


# ---------------------------------------------------------------------------
# Custom thresholds
# ---------------------------------------------------------------------------


class TestCustomThresholds:
    def test_tight_annual_range(self):
        pd = PeriodDisambiguator(PeriodThresholds(annual_min=360, annual_max=370))
        # 355 days — now falls outside the tighter range
        result = pd.classify(date(2010, 1, 1), date(2010, 12, 22))
        assert result.period_type == "unknown"
        # 365 days — within range
        result = pd.classify(date(2010, 1, 1), date(2010, 12, 31))
        assert result.period_type == "annual"

    def test_wide_quarterly_range(self):
        pd = PeriodDisambiguator(PeriodThresholds(quarterly_min=50, quarterly_max=130))
        result = pd.classify(date(2010, 1, 1), date(2010, 2, 25))
        assert result.period_type == "quarterly"
        assert result.duration_days == 55


# ---------------------------------------------------------------------------
# as_duckdb_sql()
# ---------------------------------------------------------------------------


class TestAsDuckDBSQL:
    def test_generates_valid_case_expression(self, pd):
        sql = pd.as_duckdb_sql("f", "start_date", "end_date")
        assert "CASE" in sql
        assert "WHEN f.end_date - f.start_date = 0 THEN 'point_in_time'" in sql
        assert "BETWEEN 300 AND 400 THEN 'annual'" in sql
        assert "BETWEEN 60 AND 120 THEN 'quarterly'" in sql
        assert "BETWEEN 25 AND 35 THEN 'monthly'" in sql
        assert "ELSE 'unknown'" in sql
        assert "AS period_type" in sql

    def test_custom_output_col(self, pd):
        sql = pd.as_duckdb_sql("t", "s", "e", output_col="my_period")
        assert "AS my_period" in sql

    def test_custom_thresholds_in_sql(self):
        pd = PeriodDisambiguator(PeriodThresholds(annual_min=350, annual_max=380))
        sql = pd.as_duckdb_sql("t", "s", "e")
        assert "BETWEEN 350 AND 380 THEN 'annual'" in sql
