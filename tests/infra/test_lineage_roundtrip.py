"""Round-trip characterization tests for lineage (M0.1).

The existing lineage tests cover emit-shape and no-data CLI paths only. These
drive the full emit -> Iceberg -> read/query/CLI path against a real tmp
governance warehouse, pinning current behavior of the read helpers, the
downstream-consumer query, and the CLI report commands that were the least
covered lines in the module.
"""

from __future__ import annotations

import pytest

import brightsmith.config as config
from brightsmith.infra.lineage import (
    _read_all_events,
    cmd_generate_docs,
    cmd_history,
    cmd_status,
    cmd_verify,
    emit_complete,
    emit_fail,
    emit_start,
    query_downstream_consumers,
    query_lineage_events,
)


@pytest.fixture
def gov_warehouse(tmp_path, monkeypatch):
    """Point the governance warehouse, catalog, and project root at tmp."""
    monkeypatch.setattr(config, "GOVERNANCE_WAREHOUSE", tmp_path / "gov-wh")
    monkeypatch.setattr(config, "CATALOG_PATH", tmp_path / "catalog.db")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _emit_pair(job="promote:base.facts", out="base.facts", inp=None, spec="my-spec"):
    run_id = emit_start(
        job_name=job,
        input_tables=inp if inp is not None else ["bronze.raw"],
        output_table=out,
        producer="test",
        spec_reference=spec,
    )
    emit_complete(
        run_id=run_id,
        job_name=job,
        output_table=out,
        producer="test",
        snapshot_id=12345,
        row_count=10,
        dq_passed=5,
        dq_total=5,
        dq_p0_passed=True,
    )
    return run_id


def test_emit_then_query_lineage_events(gov_warehouse):
    """A COMPLETE event is written and read back with its fields intact."""
    _emit_pair()
    events = query_lineage_events("base.facts", event_type="COMPLETE")
    assert len(events) == 1
    ev = events[0]
    assert ev["output_table"] == "base.facts"
    assert ev["row_count"] == 10
    assert ev["event_type"] == "COMPLETE"


def test_read_all_events_returns_both(gov_warehouse):
    """_read_all_events returns every emitted event (START + COMPLETE)."""
    _emit_pair()
    events = _read_all_events()
    types = sorted(e["event_type"] for e in events)
    assert types == ["COMPLETE", "START"]


def test_query_downstream_consumers(gov_warehouse):
    """A job that reads bronze.raw is discoverable as a downstream consumer."""
    _emit_pair(inp=["bronze.raw"])
    consumers = query_downstream_consumers("bronze.raw")
    assert len(consumers) == 1
    assert consumers[0]["event_type"] == "START"
    assert "bronze.raw" in consumers[0]["input_tables"]


def test_query_downstream_consumers_no_match(gov_warehouse):
    """A table nothing consumes yields no downstream consumers (empty, no error)."""
    _emit_pair(inp=["bronze.raw"])
    assert query_downstream_consumers("bronze.nonexistent") == []


def test_emit_fail_is_queryable(gov_warehouse):
    """A FAIL event round-trips through the store."""
    run_id = emit_start(
        job_name="promote:base.broken", input_tables=[], output_table="base.broken", producer="test",
    )
    emit_fail(
        run_id=run_id, job_name="promote:base.broken", output_table="base.broken",
        producer="test", error_message="boom", duration_ms=5,
    )
    fails = [e for e in _read_all_events() if e["event_type"] == "FAIL"]
    assert len(fails) == 1
    assert fails[0]["error_message"] == "boom"


def test_cmd_status_prints_job(gov_warehouse, capsys):
    _emit_pair()
    cmd_status()
    out = capsys.readouterr().out
    assert "promote:base.facts" in out


def test_cmd_history_prints_events(gov_warehouse, capsys):
    _emit_pair()
    cmd_history("promote:base.facts")
    out = capsys.readouterr().out
    assert "promote:base.facts" in out
    assert "COMPLETE" in out


def test_cmd_generate_docs_writes_openlineage_json(gov_warehouse, capsys):
    """generate-docs writes one OpenLineage .json per job with a COMPLETE event."""
    _emit_pair(job="promote:base.facts")
    cmd_generate_docs()
    lineage_dir = gov_warehouse / "governance" / "lineage"
    assert lineage_dir.exists()
    docs = list(lineage_dir.glob("*.json"))
    assert docs, "expected at least one generated OpenLineage doc"
    assert (lineage_dir / "promote-base-facts.json") in docs


def test_cmd_verify_returns_zero_for_complete_lineage(gov_warehouse, capsys):
    """A spec with a COMPLETE event carrying row_count verifies OK.

    cmd_verify matches events by ``job_name LIKE %spec%`` (or spec_reference),
    so a realistic job name embeds the spec — e.g. ``promote:my-spec``.
    """
    _emit_pair(job="promote:my-spec", spec="my-spec")
    rc = cmd_verify("my-spec")
    out = capsys.readouterr().out
    assert rc == 0
    assert "my-spec" in out


def test_cmd_verify_fails_when_no_events(gov_warehouse, capsys):
    """cmd_verify returns 1 when no lineage events exist for the spec at all."""
    # Emit an unrelated job so the table exists but has nothing for 'ghost-spec'.
    _emit_pair(job="promote:other", out="base.other", spec="other-spec")
    rc = cmd_verify("ghost-spec")
    assert rc == 1
