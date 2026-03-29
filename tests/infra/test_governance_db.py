"""Tests for governance admin database."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from brightsmith.infra.grain import compute_grain_id
from brightsmith.infra.iceberg_setup import get_catalog, get_or_create_table, read_with_duckdb
from brightsmith.infra.promote import promote


# ---------------------------------------------------------------------------
# Helpers — set up isolated governance DB for each test
# ---------------------------------------------------------------------------


def _configure_test_env(tmp_dir: Path):
    """Point config at a temp directory for isolated testing."""
    import brightsmith.config as cfg

    cfg.PROJECT_ROOT = tmp_dir
    cfg.GOVERNANCE_WAREHOUSE = tmp_dir / "data" / "governance" / "iceberg_warehouse"
    cfg.CATALOG_PATH = tmp_dir / "data" / "catalog" / "catalog.db"
    cfg.DQ_RULES_DIR = tmp_dir / "governance" / "dq-rules"
    cfg.DQ_RESULTS_DIR = tmp_dir / "governance" / "dq-results"
    cfg.PIPELINE_STATE_DIR = tmp_dir / "governance" / "pipeline-state"
    cfg.GOLDEN_DATASETS_DIR = tmp_dir / "governance" / "golden-datasets"
    cfg.DQ_SCORECARDS_DIR = tmp_dir / "governance" / "dq-scorecards"


@pytest.fixture
def gov_env(tmp_path):
    """Fixture that sets up an isolated governance environment."""
    import brightsmith.config as cfg

    # Save originals
    orig = {
        "PROJECT_ROOT": cfg.PROJECT_ROOT,
        "GOVERNANCE_WAREHOUSE": cfg.GOVERNANCE_WAREHOUSE,
        "CATALOG_PATH": cfg.CATALOG_PATH,
        "DQ_RULES_DIR": cfg.DQ_RULES_DIR,
        "DQ_RESULTS_DIR": cfg.DQ_RESULTS_DIR,
        "PIPELINE_STATE_DIR": cfg.PIPELINE_STATE_DIR,
        "GOLDEN_DATASETS_DIR": cfg.GOLDEN_DATASETS_DIR,
        "DQ_SCORECARDS_DIR": cfg.DQ_SCORECARDS_DIR,
    }

    _configure_test_env(tmp_path)
    yield tmp_path

    # Restore originals
    for key, val in orig.items():
        setattr(cfg, key, val)


# ---------------------------------------------------------------------------
# Schema + table creation tests
# ---------------------------------------------------------------------------


def test_governance_tables_created(gov_env):
    """All 7 governance tables should be created lazily."""
    from brightsmith.infra.governance_db import _get_governance_table, _TABLE_CONFIGS

    for table_name in _TABLE_CONFIGS:
        table = _get_governance_table(table_name)
        assert table is not None
        # Verify schema field count matches definition
        schema, _ = _TABLE_CONFIGS[table_name]
        assert len(table.schema().fields) == len(schema.fields)


# ---------------------------------------------------------------------------
# Write + query tests
# ---------------------------------------------------------------------------


def test_write_spec_registry(gov_env):
    """Write a spec registry row and query it back."""
    from brightsmith.infra.governance_db import get_current_specs, write_spec_registry

    write_spec_registry(
        spec_name="test-spec",
        zone="raw",
        status="IN_PROGRESS",
        output_tables=["raw.test_table"],
        updated_by="@test",
        dq_score_pct=95.0,
        dq_rules_total=20,
        dq_rules_passing=19,
        dq_rules_failing=1,
        dq_p0_passed=True,
    )

    specs = get_current_specs()
    assert len(specs) == 1
    assert specs[0]["spec_name"] == "test-spec"
    assert specs[0]["zone"] == "raw"
    assert specs[0]["status"] == "IN_PROGRESS"
    assert specs[0]["dq_rules_total"] == 20


def test_spec_registry_latest_row_wins(gov_env):
    """Multiple writes for same spec should return latest state."""
    from brightsmith.infra.governance_db import get_current_specs, write_spec_registry

    write_spec_registry(
        spec_name="evolving-spec", zone="raw", status="IN_PROGRESS",
        output_tables=[], updated_by="@agent1",
    )
    write_spec_registry(
        spec_name="evolving-spec", zone="raw", status="COMPLETE",
        output_tables=["raw.table1"], updated_by="@staff-engineer",
        dq_score_pct=100.0,
    )

    specs = get_current_specs()
    assert len(specs) == 1
    assert specs[0]["status"] == "COMPLETE"
    assert specs[0]["updated_by"] == "@staff-engineer"


def test_write_dq_run(gov_env):
    """Write a DQ run and query it back."""
    from brightsmith.infra.governance_db import get_dq_runs, get_latest_dq_run, write_dq_run

    now = datetime.now(timezone.utc)
    write_dq_run(
        run_id="run-001", spec_name="test-spec", table_name="raw.test",
        executed_at=now, rules_total=10, rules_passed=9, rules_failed=1,
        rules_errored=0, score_pct=90.0, p0_passed=True,
        p0_total=3, p0_failed=0, duration_ms=1500,
    )

    runs = get_dq_runs("test-spec")
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-001"
    assert runs[0]["score_pct"] == pytest.approx(90.0)

    latest = get_latest_dq_run("test-spec")
    assert latest is not None
    assert latest["run_id"] == "run-001"


def test_write_dq_rule_results(gov_env):
    """Write individual rule results and query them back."""
    from brightsmith.infra.governance_db import get_dq_rule_results, write_dq_rule_results

    results = [
        {
            "rule_id": "RAW-001",
            "category": "Completeness",
            "priority": "P0",
            "description": "CIK not null",
            "passed": True,
            "raw_value": "0",
            "threshold": "result = 0",
            "executed_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "rule_id": "RAW-002",
            "category": "Validity",
            "priority": "P1",
            "description": "FY format valid",
            "passed": False,
            "violations": 3,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        },
    ]
    write_dq_rule_results("run-001", "test-spec", results)

    rows = get_dq_rule_results("run-001")
    assert len(rows) == 2
    rule_ids = {r["rule_id"] for r in rows}
    assert rule_ids == {"RAW-001", "RAW-002"}


def test_write_pipeline_event(gov_env):
    """Write pipeline events and query them back."""
    from brightsmith.infra.governance_db import get_pipeline_events, write_pipeline_event

    write_pipeline_event(
        spec_name="test-spec", step_name="governance-reviewer-pre",
        event_type="COMPLETED", agent_id="@governance-reviewer",
        output_path="governance/reviews/test.md",
    )
    write_pipeline_event(
        spec_name="test-spec", step_name="primary-agent",
        event_type="COMPLETED", agent_id="@primary-agent",
    )

    events = get_pipeline_events("test-spec")
    assert len(events) == 2
    step_names = [e["step_name"] for e in events]
    assert "governance-reviewer-pre" in step_names


def test_write_agent_activity(gov_env):
    """Write agent activities and query them back."""
    from brightsmith.infra.governance_db import get_agent_activity, write_agent_activity

    write_agent_activity(
        spec_name="test-spec", agent_id="@dq-engineer",
        activity_type="finding", severity="info",
        summary="3.2% of CIK values are null",
        detail="Found 128 null CIK values out of 4000 records",
    )
    write_agent_activity(
        spec_name="test-spec", agent_id="@staff-engineer",
        activity_type="blocker", severity="blocker",
        summary="P0 DQ failure on grain uniqueness",
    )

    activities = get_agent_activity(spec_name="test-spec")
    assert len(activities) == 2

    blockers = get_agent_activity(severity="blocker")
    assert len(blockers) == 1
    assert blockers[0]["agent_id"] == "@staff-engineer"


def test_log_agent_finding_fault_tolerant(gov_env):
    """log_agent_finding should not raise even if write fails."""
    from brightsmith.infra.governance_db import log_agent_finding

    # Should succeed in test env
    result = log_agent_finding(
        spec_name="test-spec", agent_id="@test",
        summary="test finding", severity="info",
    )
    assert result is not None


def test_sync_contract(gov_env):
    """Sync a contract dict to the governance DB."""
    from brightsmith.infra.governance_db import get_contracts, sync_contract

    contract = {
        "metadata": {
            "name": "test-contract",
            "version": "1.0.0",
            "status": "active",
            "spec": "test-spec",
        },
        "schema": {
            "table": "silver.test_table",
            "namespace": "silver",
            "grain": {"columns": ["id", "date"]},
            "columns": [{"name": "id"}, {"name": "date"}, {"name": "value"}],
        },
        "quality": {
            "dq_rules": {"rules_file": "governance/dq-rules/test.json"},
            "accuracy": {"golden_dataset": "governance/golden-datasets/test.json"},
            "freshness": {"max_staleness_hours": 24},
        },
    }

    sync_contract(contract, "governance/data-contracts/test-contract.yaml")

    contracts = get_contracts()
    assert len(contracts) == 1
    assert contracts[0]["contract_name"] == "test-contract"
    assert contracts[0]["column_count"] == 3
    assert contracts[0]["has_dq_rules"] is True


def test_sync_glossary_term(gov_env):
    """Sync a glossary term to the governance DB."""
    from brightsmith.infra.governance_db import _query_table, sync_glossary_term

    term = {
        "term_id": "BT-001",
        "name": "Revenue",
        "definition": "Total income from business operations",
        "category": "measurement",
        "source": "domain-standard",
        "approval_status": "approved",
    }

    sync_glossary_term(term)

    rows = _query_table("glossary_terms", "SELECT * FROM arrow_table WHERE term_id = $1", ["BT-001"])
    assert len(rows) == 1
    assert rows[0]["term"] == "Revenue"


# ---------------------------------------------------------------------------
# Idempotency test
# ---------------------------------------------------------------------------


def test_idempotent_writes(gov_env):
    """Writing the same data twice should produce 0 duplicates."""
    from brightsmith.infra.governance_db import write_dq_run

    now = datetime.now(timezone.utc)
    result1 = write_dq_run(
        run_id="idem-run", spec_name="idem-spec", table_name="raw.test",
        executed_at=now, rules_total=5, rules_passed=5, rules_failed=0,
        rules_errored=0, score_pct=100.0, p0_passed=True,
    )
    result2 = write_dq_run(
        run_id="idem-run", spec_name="idem-spec", table_name="raw.test",
        executed_at=now, rules_total=5, rules_passed=5, rules_failed=0,
        rules_errored=0, score_pct=100.0, p0_passed=True,
    )

    assert result1.get("promoted", 0) == 1
    assert result2.get("promoted", 0) == 0
    assert result2.get("skipped", 0) == 1


# ---------------------------------------------------------------------------
# Governance summary test
# ---------------------------------------------------------------------------


def test_governance_summary(gov_env):
    """get_governance_summary should aggregate across all specs."""
    from brightsmith.infra.governance_db import get_governance_summary, write_spec_registry

    write_spec_registry(
        spec_name="spec-a", zone="raw", status="COMPLETE",
        output_tables=["raw.a"], updated_by="@test",
        dq_rules_total=10, dq_rules_passing=10, dq_rules_failing=0,
        dq_p0_passed=True, has_contract=True, has_lineage=True,
    )
    write_spec_registry(
        spec_name="spec-b", zone="base", status="IN_PROGRESS",
        output_tables=["base.b"], updated_by="@test",
        dq_rules_total=5, dq_rules_passing=4, dq_rules_failing=1,
        dq_p0_passed=True,
    )

    summary = get_governance_summary()
    assert summary["dq_overall"]["rules_total"] == 15
    assert summary["dq_overall"]["rules_passing"] == 14
    assert summary["dq_overall"]["score_pct"] == pytest.approx(93.3, abs=0.1)
    assert summary["governance_completeness"]["total_specs"] == 2
    assert summary["governance_completeness"]["with_dq"] == 2
    assert summary["governance_completeness"]["with_contract"] == 1


# ---------------------------------------------------------------------------
# Sync from files test
# ---------------------------------------------------------------------------


def test_sync_from_files(gov_env):
    """sync_from_files should backfill from existing governance artifacts."""
    from brightsmith.infra.governance_db import get_current_specs, sync_from_files

    # Create a pipeline state file
    state_dir = gov_env / "governance" / "pipeline-state"
    state_dir.mkdir(parents=True)
    state = {
        "spec": "sync-test",
        "zone": "raw",
        "status": "IN_PROGRESS",
        "output_tables": ["raw.sync_table"],
        "steps": {
            "governance-reviewer-pre": {
                "status": "COMPLETED",
                "agent": "@governance-reviewer",
                "completed_at": "2026-03-29T10:00:00+00:00",
                "requires": [],
                "blocking": False,
                "skippable": False,
            },
        },
        "skipped_steps": {},
        "approvals": {},
    }
    (state_dir / "sync-test-pipeline.json").write_text(json.dumps(state))

    # Create a glossary file
    glossary_dir = gov_env / "governance"
    (glossary_dir / "business-glossary.json").write_text(json.dumps({
        "terms": [
            {
                "term_id": "BT-001",
                "name": "Test Term",
                "definition": "A test business term",
                "category": "entity",
                "source": "project-specific",
                "approval_status": "approved",
            },
        ],
    }))

    counts = sync_from_files()
    assert counts["spec_registry"] >= 1
    assert counts["pipeline_events"] >= 1
    assert counts["glossary_terms"] >= 1

    specs = get_current_specs()
    assert any(s["spec_name"] == "sync-test" for s in specs)
