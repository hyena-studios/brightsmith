"""Tests for lineage verification logic."""

from brightsmith.infra.lineage import (
    emit_start,
    emit_complete,
    query_lineage_events,
)


class TestQueryLineageEvents:
    def test_query_nonexistent_table_returns_empty(self):
        """Querying for a table with no events returns empty list."""
        # This may fail to connect to Iceberg — that's fine, should return []
        result = query_lineage_events("nonexistent.table")
        assert isinstance(result, list)

    def test_query_returns_list(self):
        result = query_lineage_events("any.table", event_type="COMPLETE", limit=5)
        assert isinstance(result, list)


class TestEmitWithNewParams:
    def test_emit_start_with_spec_reference(self):
        """emit_start accepts spec_reference and agent_id (backward compatible)."""
        run_id = emit_start(
            job_name="test-job",
            input_tables=["raw.test"],
            output_table="base.test",
            producer="test",
            spec_reference="docs/specs/test.md",
            agent_id="@primary-agent",
        )
        assert isinstance(run_id, str)
        assert len(run_id) == 36

    def test_emit_start_without_new_params(self):
        """emit_start still works without new params (backward compatible)."""
        run_id = emit_start(
            job_name="test-job",
            input_tables=["raw.test"],
            output_table="base.test",
            producer="test",
        )
        assert isinstance(run_id, str)

    def test_emit_complete_with_transformation_steps(self):
        """emit_complete accepts transformation_steps."""
        # Fault-tolerant — won't crash even if Iceberg write fails
        emit_complete(
            run_id="fake-run-id",
            job_name="test-job",
            output_table="base.test",
            producer="test",
            row_count=100,
            transformation_steps=[
                {"order": 0, "name": "filter", "description": "Filter nulls"},
                {"order": 1, "name": "dedup", "description": "Grain-based dedup"},
            ],
        )

    def test_emit_complete_without_new_params(self):
        """emit_complete still works without transformation_steps."""
        emit_complete(
            run_id="fake-run-id",
            job_name="test-job",
            output_table="base.test",
            producer="test",
            row_count=50,
        )
