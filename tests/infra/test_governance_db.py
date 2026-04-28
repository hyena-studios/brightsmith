"""Tests for governance admin database."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


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
    cfg.CAB_DECISIONS_DIR = tmp_dir / "governance" / "cab-decisions"
    cfg.AUDIT_TRAIL_DIR = tmp_dir / "governance" / "audit-trail"
    cfg.APPROVALS_DIR = tmp_dir / "governance" / "approvals"


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
        "CAB_DECISIONS_DIR": cfg.CAB_DECISIONS_DIR,
        "AUDIT_TRAIL_DIR": cfg.AUDIT_TRAIL_DIR,
        "APPROVALS_DIR": cfg.APPROVALS_DIR,
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
    """All 8 governance tables should be created lazily."""
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
    assert specs[0]["zone"] == "bronze"
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

    # Verify columns were also written
    from brightsmith.infra.governance_db import get_contract_columns
    cols = get_contract_columns("test-contract")
    assert len(cols) == 3


def test_sync_contract_writes_columns(gov_env):
    """sync_contract should write per-column records to contract_columns."""
    from brightsmith.infra.governance_db import get_contract_columns, sync_contract

    contract = {
        "metadata": {
            "name": "col-test-contract",
            "version": "2.0.0",
            "status": "active",
            "spec": "col-test-spec",
        },
        "schema": {
            "table": "silver.fact_filings",
            "namespace": "silver",
            "grain": {"columns": ["cik", "period"]},
            "columns": [
                {
                    "name": "cik",
                    "type": "string",
                    "nullable": False,
                    "is_cde": True,
                    "cde_rationale": "Primary entity identifier",
                    "is_pii": False,
                    "business_term": "BT-001",
                    "description": "Central Index Key",
                },
                {
                    "name": "revenue",
                    "type": "double",
                    "nullable": True,
                    "is_cde": False,
                    "is_pii": False,
                    "business_term": "BT-003",
                    "description": "Total revenue",
                },
                {
                    "name": "period",
                    "type": "string",
                    "nullable": False,
                    "description": "Reporting period",
                },
            ],
        },
        "quality": {},
    }

    result = sync_contract(contract, "governance/data-contracts/col-test.yaml")
    assert result["columns_promoted"] == 3
    assert result.get("columns_skipped", 0) == 0

    cols = get_contract_columns("col-test-contract")
    assert len(cols) == 3

    # Verify column ordering
    col_names = [c["column_name"] for c in cols]
    assert col_names == ["cik", "revenue", "period"]

    # Verify CDE/PII/business_term round-trip on first column
    cik = cols[0]
    assert cik["is_cde"] is True
    assert cik["cde_rationale"] == "Primary entity identifier"
    assert cik["is_pii"] is False
    assert cik["business_term"] == "BT-001"
    assert cik["data_type"] == "string"
    assert cik["is_nullable"] is False
    assert cik["ordinal_position"] == 0
    assert cik["table_name"] == "silver.fact_filings"
    assert cik["zone"] == "silver"
    assert cik["version"] == "2.0.0"

    # Verify column without explicit CDE/PII defaults
    period = cols[2]
    assert period["is_cde"] is False
    assert period["is_pii"] is False
    assert period["business_term"] is None


def test_sync_contract_columns_idempotent(gov_env):
    """Re-syncing the same contract should write 0 new column rows."""
    from brightsmith.infra.governance_db import get_contract_columns, sync_contract

    contract = {
        "metadata": {"name": "idem-col", "version": "1.0.0", "status": "active"},
        "schema": {
            "table": "gold.summary",
            "namespace": "gold",
            "columns": [{"name": "metric"}, {"name": "value"}],
        },
        "quality": {},
    }

    result1 = sync_contract(contract, "governance/data-contracts/idem-col.yaml")
    assert result1["columns_promoted"] == 2

    result2 = sync_contract(contract, "governance/data-contracts/idem-col.yaml")
    assert result2["columns_promoted"] == 0
    assert result2["columns_skipped"] == 2

    # Still only 2 rows total
    cols = get_contract_columns("idem-col")
    assert len(cols) == 2


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


# ---------------------------------------------------------------------------
# New tables: governance-database-only Spec A
# ---------------------------------------------------------------------------


# -- dq_rules --

def test_write_dq_rules(gov_env):
    """Write DQ rules and query them back."""
    from brightsmith.infra.governance_db import get_dq_rules, write_dq_rules

    rules = [
        {
            "rule_id": "RAW-001",
            "category": "completeness",
            "priority": "P0",
            "description": "CIK not null",
            "sql": "SELECT COUNT(*) FROM raw.filings WHERE cik IS NULL",
            "threshold": "result = 0",
        },
        {
            "rule_id": "RAW-002",
            "category": "validity",
            "priority": "P1",
            "description": "FY format valid",
            "sql": "SELECT COUNT(*) FROM raw.filings WHERE fy NOT LIKE 'FY%'",
            "threshold": "result = 0",
        },
    ]
    result = write_dq_rules("test-spec", "raw.filings", rules)
    assert result.get("promoted", 0) == 2

    loaded = get_dq_rules("test-spec")
    assert len(loaded) == 2
    rule_ids = {r["rule_id"] for r in loaded}
    assert rule_ids == {"RAW-001", "RAW-002"}
    assert loaded[0]["version"] == 1
    assert loaded[0]["status"] == "proposed"


def test_dq_rules_idempotent(gov_env):
    """Writing same rules twice should produce 0 new rows on second write."""
    from brightsmith.infra.governance_db import write_dq_rules

    rules = [{"rule_id": "IDEM-001", "category": "completeness", "priority": "P0",
              "description": "test", "sql": "SELECT 1", "threshold": "result = 1"}]
    r1 = write_dq_rules("idem-spec", "raw.test", rules)
    assert r1.get("promoted", 0) == 1

    # Same rule, same version — should be idempotent
    rules_v1 = [{"rule_id": "IDEM-001", "category": "completeness", "priority": "P0",
                 "description": "test", "sql": "SELECT 1", "threshold": "result = 1",
                 "version": 1}]
    r2 = write_dq_rules("idem-spec", "raw.test", rules_v1)
    assert r2.get("promoted", 0) == 0
    assert r2.get("skipped", 0) == 1


def test_dq_rules_version_increment(gov_env):
    """Approving a rule should create version 2; latest-version query returns it."""
    from brightsmith.infra.governance_db import get_dq_rules, write_dq_rules

    # Version 1: proposed
    write_dq_rules("ver-spec", "raw.test", [
        {"rule_id": "VER-001", "category": "completeness", "priority": "P0",
         "description": "test", "sql": "SELECT 1", "threshold": "result = 1"},
    ])

    # Version 2: approved (auto-increments since no explicit version)
    write_dq_rules("ver-spec", "raw.test", [
        {"rule_id": "VER-001", "category": "completeness", "priority": "P0",
         "description": "test", "sql": "SELECT 1", "threshold": "result = 1",
         "status": "approved", "approved_by": "@dq-engineer"},
    ])

    rules = get_dq_rules("ver-spec")
    assert len(rules) == 1  # Latest version only
    assert rules[0]["version"] == 2
    assert rules[0]["status"] == "approved"


def test_dq_rules_filter_by_table(gov_env):
    """Query dq_rules filtered by table_name."""
    from brightsmith.infra.governance_db import get_dq_rules, write_dq_rules

    write_dq_rules("multi-spec", "raw.table_a", [
        {"rule_id": "A-001", "category": "completeness", "priority": "P0",
         "description": "a test", "sql": "SELECT 1", "threshold": "result = 1"},
    ])
    write_dq_rules("multi-spec", "raw.table_b", [
        {"rule_id": "B-001", "category": "validity", "priority": "P1",
         "description": "b test", "sql": "SELECT 1", "threshold": "result = 1"},
    ])

    a_rules = get_dq_rules("multi-spec", table_name="raw.table_a")
    assert len(a_rules) == 1
    assert a_rules[0]["rule_id"] == "A-001"


# -- dq_acknowledgments --

def test_write_dq_acknowledgment(gov_env):
    """Write a DQ acknowledgment and query it back."""
    from brightsmith.infra.governance_db import get_dq_acknowledgments, write_dq_acknowledgment

    write_dq_acknowledgment(
        run_id="run-001", rule_id="RAW-001", spec_name="test-spec",
        acknowledged_by="@staff-engineer", reason="Known data quality issue in source",
    )

    acks = get_dq_acknowledgments(run_id="run-001")
    assert len(acks) == 1
    assert acks[0]["rule_id"] == "RAW-001"
    assert acks[0]["acknowledged_by"] == "@staff-engineer"


def test_dq_acknowledgment_idempotent(gov_env):
    """Writing same acknowledgment twice should produce 0 new rows."""
    from brightsmith.infra.governance_db import write_dq_acknowledgment

    now = datetime(2026, 3, 29, 12, 0, 0, tzinfo=timezone.utc)
    r1 = write_dq_acknowledgment(
        run_id="idem-run", rule_id="IDEM-001", spec_name="test",
        acknowledged_by="@test", reason="test", acknowledged_at=now,
    )
    r2 = write_dq_acknowledgment(
        run_id="idem-run", rule_id="IDEM-001", spec_name="test",
        acknowledged_by="@test", reason="test", acknowledged_at=now,
    )
    assert r1.get("promoted", 0) == 1
    assert r2.get("promoted", 0) == 0


def test_dq_acknowledgment_join_pattern(gov_env):
    """Write rule result + acknowledgment, verify both queryable for join."""
    from brightsmith.infra.governance_db import (
        get_dq_acknowledgments,
        get_dq_rule_results,
        write_dq_acknowledgment,
        write_dq_rule_results,
    )

    now = datetime.now(timezone.utc)
    write_dq_rule_results("join-run", "join-spec", [
        {"rule_id": "J-001", "category": "completeness", "priority": "P0",
         "description": "null check", "passed": False, "executed_at": now.isoformat()},
    ])
    write_dq_acknowledgment(
        run_id="join-run", rule_id="J-001", spec_name="join-spec",
        acknowledged_by="@engineer", reason="Source data issue",
    )

    results = get_dq_rule_results("join-run")
    acks = get_dq_acknowledgments(run_id="join-run")
    assert len(results) == 1
    assert len(acks) == 1
    assert results[0]["rule_id"] == acks[0]["rule_id"]


# -- cab_decisions --

def test_write_cab_decision(gov_env):
    """Write a CAB decision and query it back."""
    from brightsmith.infra.governance_db import get_cab_decisions, write_cab_decision

    write_cab_decision(
        decision_id="CAB-001", spec_name="test-spec", table_name="silver.facts",
        classification="MINOR", classification_reasons=["Added new column"],
        decision="APPROVED", decided_by="@cab-agent",
        schema_diff={"added": ["new_col"]},
    )

    decisions = get_cab_decisions(spec_name="test-spec")
    assert len(decisions) == 1
    assert decisions[0]["decision_id"] == "CAB-001"
    assert decisions[0]["classification"] == "MINOR"
    assert decisions[0]["decision"] == "APPROVED"


def test_cab_decision_idempotent(gov_env):
    """Same decision_id should not create duplicate rows."""
    from brightsmith.infra.governance_db import write_cab_decision

    r1 = write_cab_decision(
        decision_id="IDEM-CAB", spec_name="test", table_name="silver.t",
        classification="PATCH", classification_reasons=["desc update"],
        decision="APPROVED",
    )
    r2 = write_cab_decision(
        decision_id="IDEM-CAB", spec_name="test", table_name="silver.t",
        classification="PATCH", classification_reasons=["desc update"],
        decision="APPROVED",
    )
    assert r1.get("promoted", 0) == 1
    assert r2.get("promoted", 0) == 0


def test_cab_decision_query_by_id(gov_env):
    """Query CAB decision by decision_id."""
    from brightsmith.infra.governance_db import get_cab_decisions, write_cab_decision

    write_cab_decision(
        decision_id="LOOKUP-001", spec_name="s", table_name="t",
        classification="MAJOR", classification_reasons=["column removed"],
        decision="PENDING",
    )
    results = get_cab_decisions(decision_id="LOOKUP-001")
    assert len(results) == 1
    assert results[0]["classification"] == "MAJOR"


# -- golden_datasets --

def test_write_golden_dataset_values(gov_env):
    """Write golden values and query them back."""
    from brightsmith.infra.governance_db import get_golden_dataset, write_golden_dataset_values

    values = [
        {"value_description": "Apple revenue Q1", "column_name": "revenue",
         "expected_value": "123456.78", "filters": {"cik": "320193", "period": "Q1"}},
        {"value_description": "Apple assets Q1", "column_name": "total_assets",
         "expected_value": "999999.99", "filters": {"cik": "320193", "period": "Q1"}},
    ]
    result = write_golden_dataset_values("gd-spec", "gold.summary", values)
    assert result.get("promoted", 0) == 2

    loaded = get_golden_dataset("gd-spec")
    assert len(loaded) == 2


def test_golden_dataset_idempotent(gov_env):
    """Same golden values should not duplicate."""
    from brightsmith.infra.governance_db import write_golden_dataset_values

    values = [{"value_description": "test", "column_name": "val",
               "expected_value": "42", "filters": {"id": "1"}}]
    r1 = write_golden_dataset_values("idem-gd", "gold.t", values)
    r2 = write_golden_dataset_values("idem-gd", "gold.t", values)
    assert r1.get("promoted", 0) == 1
    assert r2.get("promoted", 0) == 0


def test_golden_dataset_deterministic_grain(gov_env):
    """Equivalent but differently-ordered filter dicts should produce same grain hash."""
    from brightsmith.infra.governance_db import write_golden_dataset_values

    v1 = [{"value_description": "test", "column_name": "v",
           "expected_value": "1", "filters": {"a": "1", "b": "2"}}]
    v2 = [{"value_description": "test", "column_name": "v",
           "expected_value": "1", "filters": {"b": "2", "a": "1"}}]

    r1 = write_golden_dataset_values("det-spec", "t", v1)
    r2 = write_golden_dataset_values("det-spec", "t", v2)
    assert r1.get("promoted", 0) == 1
    # Second write with different key order should be detected as duplicate
    assert r2.get("promoted", 0) == 0
    assert r2.get("skipped", 0) == 1


# -- run_history --

def test_write_run_history(gov_env):
    """Write a run history record and query it back."""
    from brightsmith.infra.governance_db import get_run_history, write_run_history

    now = datetime.now(timezone.utc)
    write_run_history(
        run_id="run-2026-001", started_at=now, status="SUCCESS",
        zones_summary={"raw": {"status": "SUCCESS", "rows": 1000}},
        duration_seconds=45.2,
    )

    history = get_run_history()
    assert len(history) == 1
    assert history[0]["run_id"] == "run-2026-001"
    assert history[0]["status"] == "SUCCESS"


def test_run_history_idempotent(gov_env):
    """Same run_id should not duplicate."""
    from brightsmith.infra.governance_db import write_run_history

    now = datetime.now(timezone.utc)
    r1 = write_run_history(run_id="idem-run", started_at=now, status="SUCCESS", zones_summary={})
    r2 = write_run_history(run_id="idem-run", started_at=now, status="SUCCESS", zones_summary={})
    assert r1.get("promoted", 0) == 1
    assert r2.get("promoted", 0) == 0


# -- chaos_manifests --

def test_write_chaos_manifest(gov_env):
    """Write a chaos manifest and query it back."""
    from brightsmith.infra.governance_db import get_chaos_manifest, write_chaos_manifest

    write_chaos_manifest(
        run_id="chaos-001", source_table="raw.filings", shadow_table="raw.filings_shadow",
        total_rows=5000, corruption_rate=0.05, rows_corrupted=250,
        columns_corrupted=3, total_corruptions=750,
        seed=42, dimensions_covered=["null_injection", "type_swap"],
        corruptions_sample=[{"row": 1, "col": "cik", "type": "null_injection"}],
    )

    manifest = get_chaos_manifest("chaos-001")
    assert manifest is not None
    assert manifest["total_rows"] == 5000
    assert manifest["corruption_rate"] == pytest.approx(0.05)
    assert manifest["seed"] == 42


def test_chaos_manifest_idempotent(gov_env):
    """Same run_id should not duplicate."""
    from brightsmith.infra.governance_db import write_chaos_manifest

    r1 = write_chaos_manifest(
        run_id="idem-chaos", source_table="t", shadow_table="s",
        total_rows=100, corruption_rate=0.1, rows_corrupted=10,
        columns_corrupted=2, total_corruptions=20,
    )
    r2 = write_chaos_manifest(
        run_id="idem-chaos", source_table="t", shadow_table="s",
        total_rows=100, corruption_rate=0.1, rows_corrupted=10,
        columns_corrupted=2, total_corruptions=20,
    )
    assert r1.get("promoted", 0) == 1
    assert r2.get("promoted", 0) == 0


# -- documents --

def test_write_document(gov_env):
    """Write a document and query it back."""
    from brightsmith.infra.governance_db import get_document, write_document

    write_document(
        doc_type="review", doc_name="test-spec-pre-review",
        title="Pre-Implementation Review: test-spec",
        content="## Review\n\nThis spec looks good.",
        spec_name="test-spec", agent_id="@governance-reviewer",
    )

    doc = get_document("review", "test-spec-pre-review")
    assert doc is not None
    assert doc["title"] == "Pre-Implementation Review: test-spec"
    assert "This spec looks good" in doc["content"]
    assert doc["version"] == 1


def test_document_idempotent(gov_env):
    """Same doc_type + doc_name + version should not duplicate."""
    from brightsmith.infra.governance_db import write_document

    r1 = write_document(
        doc_type="model", doc_name="test-physical", title="Physical Model",
        content="# Model", version=1,
    )
    r2 = write_document(
        doc_type="model", doc_name="test-physical", title="Physical Model",
        content="# Model", version=1,
    )
    assert r1.get("promoted", 0) == 1
    assert r2.get("promoted", 0) == 0


def test_document_version_auto_increment(gov_env):
    """Auto-increment version when not specified."""
    from brightsmith.infra.governance_db import get_document, write_document

    write_document(
        doc_type="insight", doc_name="zone-transition",
        title="Zone Transition Insight", content="Version 1",
    )
    write_document(
        doc_type="insight", doc_name="zone-transition",
        title="Zone Transition Insight v2", content="Version 2",
    )

    doc = get_document("insight", "zone-transition")
    assert doc is not None
    assert doc["version"] == 2
    assert doc["title"] == "Zone Transition Insight v2"


def test_get_documents_by_type(gov_env):
    """Query documents by type, getting latest version per doc_name."""
    from brightsmith.infra.governance_db import get_documents_by_type, write_document

    write_document(doc_type="review", doc_name="spec-a-pre", title="A Pre", content="a")
    write_document(doc_type="review", doc_name="spec-b-pre", title="B Pre", content="b")
    write_document(doc_type="model", doc_name="spec-a-physical", title="A Phys", content="c")

    reviews = get_documents_by_type("review")
    assert len(reviews) == 2

    models = get_documents_by_type("model")
    assert len(models) == 1


def test_get_documents_by_type_and_spec(gov_env):
    """Query documents by type filtered by spec_name."""
    from brightsmith.infra.governance_db import get_documents_by_type, write_document

    write_document(doc_type="review", doc_name="s1-pre", title="S1", content="x", spec_name="spec-1")
    write_document(doc_type="review", doc_name="s2-pre", title="S2", content="y", spec_name="spec-2")

    s1_reviews = get_documents_by_type("review", spec_name="spec-1")
    assert len(s1_reviews) == 1
    assert s1_reviews[0]["doc_name"] == "s1-pre"


# -- pipeline_events content column --

def test_pipeline_event_with_content(gov_env):
    """Pipeline event should carry approval content."""
    from brightsmith.infra.governance_db import get_pipeline_events, write_pipeline_event

    approval_doc = "# Approval\n\nApproved with notes."
    write_pipeline_event(
        spec_name="content-spec", step_name="business-terms",
        event_type="APPROVED", approval_decision="APPROVED",
        approval_by="human:jeff", content=approval_doc,
    )

    events = get_pipeline_events("content-spec")
    assert len(events) == 1
    assert events[0]["content"] == approval_doc
    assert events[0]["approval_decision"] == "APPROVED"


# -- 20 tables created --

def test_all_15_tables_created(gov_env):
    """All 20 governance tables should be created lazily."""
    from brightsmith.infra.governance_db import _get_governance_table, _TABLE_CONFIGS

    assert len(_TABLE_CONFIGS) == 20
    for table_name in _TABLE_CONFIGS:
        table = _get_governance_table(table_name)
        assert table is not None
        schema, _ = _TABLE_CONFIGS[table_name]
        assert len(table.schema().fields) == len(schema.fields)


# -- migrate_files_to_iceberg --

def test_migrate_files_to_iceberg(gov_env):
    """migrate_files_to_iceberg should import file artifacts to Iceberg tables."""
    from brightsmith.infra.governance_db import (
        get_cab_decisions,
        get_document,
        get_golden_dataset,
        migrate_files_to_iceberg,
    )

    # Create test fixtures
    # CAB decision
    cab_dir = gov_env / "governance" / "cab-decisions"
    cab_dir.mkdir(parents=True)
    (cab_dir / "cab-test-001.json").write_text(json.dumps({
        "decision_id": "cab-test-001",
        "spec_name": "migrate-test",
        "table_name": "silver.facts",
        "classification": "MINOR",
        "classification_reasons": ["new column"],
        "decision": "APPROVED",
    }))

    # Golden dataset
    gd_dir = gov_env / "governance" / "golden-datasets"
    gd_dir.mkdir(parents=True)
    (gd_dir / "migrate-test-golden.json").write_text(json.dumps({
        "spec": "migrate-test",
        "table": "gold.summary",
        "values": [
            {"value_description": "test val", "column_name": "revenue",
             "expected_value": "100", "filters": {"id": "1"}},
            {"value_description": "test val 2", "column_name": "assets",
             "expected_value": "200", "filters": {"id": "1"}},
            {"value_description": "test val 3", "column_name": "equity",
             "expected_value": "300", "filters": {"id": "1"}},
        ],
    }))

    # Review document
    reviews_dir = gov_env / "governance" / "reviews"
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "migrate-test-pre-review.md").write_text(
        "## Pre-Implementation Review\n\nLooks good."
    )

    report = migrate_files_to_iceberg()

    # Verify CAB decisions migrated
    assert report["cab_decisions"]["files"] == 1
    assert report["cab_decisions"]["rows"] == 1
    decisions = get_cab_decisions(decision_id="cab-test-001")
    assert len(decisions) == 1

    # Verify golden datasets migrated
    assert report["golden_datasets"]["files"] == 1
    assert report["golden_datasets"]["rows"] == 3
    gd = get_golden_dataset("migrate-test")
    assert len(gd) == 3

    # Verify documents migrated
    assert report["documents"]["files"] >= 1
    assert report["documents"]["rows"] >= 1
    doc = get_document("review", "migrate-test-pre-review")
    assert doc is not None

    # Verify idempotency: re-run should produce 0 new rows
    report2 = migrate_files_to_iceberg()
    assert report2["cab_decisions"]["rows"] == 0
    assert report2["golden_datasets"]["rows"] == 0
    assert report2["documents"]["rows"] == 0


# ---------------------------------------------------------------------------
# Iceberg-first write tests (feature-brightsmith-iceberg-writes)
# ---------------------------------------------------------------------------


def test_dq_runner_iceberg_only(gov_env, monkeypatch):
    """DQ results should be written to Iceberg without runtime result files."""
    import brightsmith.config as cfg
    import brightsmith.infra.dq_runner as dq_runner_mod
    from brightsmith.infra.governance_db import get_dq_runs

    # Patch module-level constants in dq_runner to point at test dirs
    monkeypatch.setattr(dq_runner_mod, "DQ_RULES_DIR", cfg.DQ_RULES_DIR)
    monkeypatch.setattr(dq_runner_mod, "DQ_RESULTS_DIR", cfg.DQ_RESULTS_DIR)

    # Set up minimal DQ rules file with no SQL (to avoid Iceberg scan)
    cfg.DQ_RULES_DIR.mkdir(parents=True, exist_ok=True)

    # Write a trivial rule file (empty rules list)
    rule_file = cfg.DQ_RULES_DIR / "iceberg-first-test.json"
    rule_file.write_text(json.dumps({
        "spec": "iceberg-first-test",
        "tables": ["raw.test_table"],
        "rules": [],
    }))

    # Ensure warehouse/catalog paths are set for the runner
    cfg.WAREHOUSE_PATH = gov_env / "data" / "warehouse"
    cfg.WAREHOUSE_PATH.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dq_runner_mod, "WAREHOUSE_PATH", cfg.WAREHOUSE_PATH)
    monkeypatch.setattr(dq_runner_mod, "CATALOG_PATH", cfg.CATALOG_PATH)

    result = dq_runner_mod.run_rules(spec="iceberg-first-test")
    assert result["run_id"] is not None
    assert result["rules_total"] == 0

    # Verify Iceberg got the run record
    runs = get_dq_runs("iceberg-first-test")
    assert len(runs) >= 1
    assert runs[0]["run_id"] == result["run_id"]

    # Runtime path does not write generated governance files.
    result_files = list(cfg.DQ_RESULTS_DIR.glob("iceberg-first-test-*.json"))
    assert len(result_files) == 0


def test_save_contract_writes_iceberg_only(gov_env):
    """save_contract() should sync to Iceberg without writing runtime YAML."""
    from brightsmith.infra.contract import save_contract
    from brightsmith.infra.governance_db import get_contracts

    contract = {
        "metadata": {
            "name": "auto-sync-test",
            "version": "1.0.0",
            "status": "draft",
            "spec": "test-spec",
        },
        "schema": {
            "table": "gold.metrics",
            "namespace": "gold",
            "grain": {"columns": ["id"]},
            "columns": [
                {"name": "id", "type": "string"},
                {"name": "value", "type": "double"},
            ],
        },
        "quality": {},
    }

    contracts_dir = gov_env / "governance" / "data-contracts"
    path = save_contract(contract, contracts_dir)

    assert not path.exists()

    # Iceberg should have the contract
    contracts = get_contracts()
    assert any(c["contract_name"] == "auto-sync-test" for c in contracts)


def test_pipeline_gate_emits_start_event(gov_env):
    """start_step() should emit a STARTED event to governance DB."""
    import brightsmith.config as cfg

    cfg.PIPELINE_STATE_DIR = gov_env / "governance" / "pipeline-state"

    from brightsmith.infra.governance_db import get_pipeline_events
    from brightsmith.infra.pipeline_gate import PipelineGate

    gate = PipelineGate("start-event-test", state_dir=cfg.PIPELINE_STATE_DIR)
    gate.init(zone="bronze")
    gate.start_step("governance-reviewer-pre")

    events = get_pipeline_events("start-event-test")
    event_types = [e["event_type"] for e in events]
    assert "STARTED" in event_types


def test_scorecard_writes_document(gov_env):
    """generate_scorecard() should write to governance.documents table."""
    from brightsmith.infra.dq_scorecard import generate_scorecard
    from brightsmith.infra.governance_db import get_document

    run_result = {
        "run_id": "sc-test-001",
        "spec": "scorecard-doc-test",
        "executed_at": "2026-03-30T21:00:00Z",
        "rules_total": 2,
        "rules_passed": 2,
        "rules_failed": 0,
        "results": [
            {
                "rule_id": "TST-001",
                "spec": "scorecard-doc-test",
                "passed": True,
                "detail": "ok",
                "raw_value": 0,
            },
            {
                "rule_id": "TST-002",
                "spec": "scorecard-doc-test",
                "passed": True,
                "detail": "ok",
                "raw_value": 0,
            },
        ],
    }

    path = generate_scorecard(run_result, "scorecard-doc-test")
    assert path.exists()

    # Verify Iceberg document was also written
    doc = get_document("dq_scorecard", "scorecard-doc-test-scorecard")
    assert doc is not None
    assert "DQ Scorecard" in doc["title"]


def test_acknowledgment_writes_iceberg_only(gov_env, monkeypatch):
    """DQ acknowledgments create Iceberg rows and no acknowledgment JSON."""
    import brightsmith.config as cfg
    import brightsmith.infra.dq_runner as dq_runner_mod
    from brightsmith.infra.governance_db import get_dq_acknowledgments

    monkeypatch.setattr(dq_runner_mod, "DQ_RESULTS_DIR", cfg.DQ_RESULTS_DIR)
    dq_runner_mod.acknowledge_failures("ack-spec", "run-123", "accepted exception")

    acks = get_dq_acknowledgments(run_id="run-123")
    assert len(acks) == 1
    assert not list(cfg.DQ_RESULTS_DIR.glob("*-ack-*.json"))


def test_exporters_generate_files_from_iceberg(gov_env):
    """Generated governance files come from explicit exporters."""
    from brightsmith.infra.governance_db import (
        export_contracts_to_files,
        export_dq_results_to_files,
        sync_contract,
        write_dq_run,
    )

    sync_contract(
        {
            "metadata": {"name": "export-contract", "version": "1.0.0", "status": "active"},
            "schema": {"table": "gold.metrics", "namespace": "gold", "columns": [{"name": "id"}]},
            "quality": {},
        },
        "governance/data-contracts/export-contract.yaml",
    )
    write_dq_run(
        run_id="export-run",
        spec_name="export-spec",
        table_name="gold.metrics",
        executed_at=datetime.now(timezone.utc),
        rules_total=0,
        rules_passed=0,
        rules_failed=0,
        rules_errored=0,
        score_pct=0.0,
        p0_passed=True,
    )

    contract_paths = export_contracts_to_files()
    result_paths = export_dq_results_to_files()
    assert any(path.name == "export-contract.yaml" for path in contract_paths)
    assert any(path.name == "export-spec-export-run.json" for path in result_paths)


def test_iceberg_write_failures_are_not_swallowed(gov_env, monkeypatch):
    """Required Iceberg write failures fail the caller."""
    import brightsmith.infra.dq_runner as dq_runner_mod

    def fail(*args, **kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr(dq_runner_mod, "_write_governance_results", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        dq_runner_mod.run_rules(spec="missing-rules")


def test_aliases_normalize_before_persistence(gov_env):
    """Deprecated zone aliases persist as canonical names."""
    from brightsmith.infra.governance_db import get_contracts, get_current_specs, sync_contract, write_spec_registry

    write_spec_registry("alias-spec", "raw", "IN_PROGRESS", ["raw.table"], "@test")
    sync_contract(
        {
            "metadata": {"name": "alias-contract", "version": "1.0.0", "status": "active"},
            "schema": {"table": "consumable.metrics", "namespace": "consumable", "columns": [{"name": "id"}]},
            "quality": {},
        },
        "governance/data-contracts/alias-contract.yaml",
    )

    assert get_current_specs()[0]["zone"] == "bronze"
    assert get_contracts()[0]["table_name"] == "gold.metrics"
    assert get_contracts()[0]["zone"] == "gold"


def test_business_term_id_and_is_cde_are_independent(gov_env):
    """Two columns can share a business term while differing on CDE status."""
    from brightsmith.infra.governance_db import get_contract_columns, sync_contract, write_business_term

    write_business_term("BT-ACCOUNT-001", "Account Number", metadata={"legacy_cde": True})
    sync_contract(
        {
            "metadata": {"name": "cde-independent", "version": "1.0.0", "status": "active"},
            "schema": {
                "table": "gold.accounts",
                "namespace": "gold",
                "columns": [
                    {"name": "acct_num_reporting", "business_term_id": "BT-ACCOUNT-001", "is_cde": True},
                    {"name": "acct_num_display", "business_term_id": "BT-ACCOUNT-001", "is_cde": False},
                ],
            },
            "quality": {},
        },
        "governance/data-contracts/cde-independent.yaml",
    )

    cols = get_contract_columns("cde-independent")
    assert {col["business_term_id"] for col in cols} == {"BT-ACCOUNT-001"}
    assert [col["is_cde"] for col in cols] == [True, False]


def test_product_write_fails_on_missing_enterprise_reference(gov_env):
    """Product records fail when required enterprise references are unknown."""
    from brightsmith.infra.governance_db import sync_contract

    with pytest.raises(ValueError, match="Unknown enterprise governance reference"):
        sync_contract(
            {
                "metadata": {"name": "missing-ref", "version": "1.0.0", "status": "active"},
                "schema": {
                    "table": "gold.accounts",
                    "namespace": "gold",
                    "columns": [{"name": "acct_num", "business_term_id": "BT-MISSING", "is_cde": False}],
                },
                "quality": {},
            },
            "governance/data-contracts/missing-ref.yaml",
        )
