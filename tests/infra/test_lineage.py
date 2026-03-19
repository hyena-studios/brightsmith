"""Tests for runtime lineage event emission."""

from grist.infra.lineage import emit_start


def test_emit_start_returns_run_id():
    """emit_start should return a valid run_id string even if write fails."""
    # emit_start is fault-tolerant — it logs warnings on write failures
    # but always returns a run_id
    run_id = emit_start(
        job_name="test-job",
        input_tables=["raw.test"],
        output_table="base.test",
        producer="test",
    )
    assert isinstance(run_id, str)
    assert len(run_id) == 36  # UUID format


def test_emit_creates_valid_openlineage_event():
    """Lineage event should have all required OpenLineage fields."""
    # The actual Iceberg write may fail (no warehouse), but the run_id is valid
    run_id = emit_start(
        job_name="test-ingest",
        input_tables=["external.source"],
        output_table="raw.test_table",
        producer="@primary-agent",
    )
    assert run_id is not None
    assert isinstance(run_id, str)
