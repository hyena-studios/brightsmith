"""Characterization tests for the governance file->Iceberg sync + migration
god-functions (M0.1).

``sync_from_files`` (384 lines) and ``migrate_files_to_iceberg`` were the two
least-covered real-logic functions. These drive them end-to-end against an
isolated tmp project + governance warehouse: the empty-project path (every
sub-step's ``if DIR.exists()`` false branch + orchestration), a real write path
through a glossary / DQ rules / golden dataset, and the idempotency guarantee
(promote-based dedup -> a second run adds 0 rows).
"""

from __future__ import annotations

import json

import pytest

import brightsmith.config as config
from brightsmith.infra.governance.migration import migrate_files_to_iceberg
from brightsmith.infra.governance.sync import sync_from_files


@pytest.fixture
def gov_project(tmp_path):
    """Isolate config at a tmp project + governance warehouse, then restore.

    ``configure`` mutates the process-global config, so we snapshot and restore
    it to avoid leaking into other tests.
    """
    from brightsmith.infra.iceberg_setup import reset_catalog_cache

    original = config.get_config()
    config.configure(project_root=tmp_path)
    # Force governance writes into tmp regardless of how paths are derived.
    config.GOVERNANCE_WAREHOUSE = tmp_path / "gov-wh"
    config.CATALOG_PATH = tmp_path / "catalog.db"
    (tmp_path / "governance").mkdir(exist_ok=True)
    reset_catalog_cache()
    try:
        yield tmp_path
    finally:
        config._CONFIG = original
        reset_catalog_cache()


def _write_glossary(root, terms):
    (root / "governance" / "business-glossary.json").write_text(json.dumps({"terms": terms}))


# --- sync_from_files -------------------------------------------------------


def test_sync_empty_project_is_all_zero_no_error(gov_project):
    """Every sub-sync's 'directory absent' branch runs cleanly and reports 0."""
    counts = sync_from_files()
    assert isinstance(counts, dict)
    # Keys contributed by the sub-steps that always return a count.
    assert counts.get("glossary_terms") == 0
    assert counts.get("dq_runs") == 0
    # No sub-step raised (each returned its slice).
    assert all(isinstance(v, int) for v in counts.values())


def test_sync_glossary_writes_terms(gov_project):
    """A glossary with two terms syncs two rows."""
    _write_glossary(gov_project, [
        {"term_id": "T1", "name": "Revenue", "definition": "money in",
         "category": "finance", "source": "test", "approval_status": "approved"},
        {"term_id": "T2", "name": "Cost", "definition": "money out",
         "category": "finance", "source": "test", "approval_status": "approved"},
    ])
    first = sync_from_files()
    assert first["glossary_terms"] == 2


def test_sync_glossary_is_idempotent(gov_project):
    """A second sync of unchanged glossary terms adds zero rows.

    Fixed by making the glossary_terms grain content-based instead of keying on
    the now()-stamped updated_at (schemas.py). Re-syncing an unchanged glossary
    is a no-op.
    """
    _write_glossary(gov_project, [
        {"term_id": "T1", "name": "Revenue", "definition": "money in",
         "category": "finance", "source": "test", "approval_status": "approved"},
    ])
    sync_from_files()
    second = sync_from_files()
    assert second["glossary_terms"] == 0


def test_sync_glossary_edit_creates_new_row(gov_project):
    """Editing a term's content (definition) is captured as a new row.

    The content-based grain must still let a genuine edit through — idempotency
    must not swallow real changes.
    """
    _write_glossary(gov_project, [
        {"term_id": "T1", "name": "Revenue", "definition": "money in",
         "category": "finance", "source": "test", "approval_status": "approved"},
    ])
    assert sync_from_files()["glossary_terms"] == 1

    # Change the definition; the edited term must sync as a new row.
    _write_glossary(gov_project, [
        {"term_id": "T1", "name": "Revenue", "definition": "total money in (edited)",
         "category": "finance", "source": "test", "approval_status": "approved"},
    ])
    assert sync_from_files()["glossary_terms"] == 1


def test_sync_pipeline_state_writes_events_and_registry(gov_project):
    """A pipeline-state file syncs step events, approvals, and a spec_registry row."""
    ps_dir = config.PIPELINE_STATE_DIR
    ps_dir.mkdir(parents=True, exist_ok=True)
    (ps_dir / "myspec-pipeline.json").write_text(json.dumps({
        "spec": "myspec",
        "zone": "silver",
        "status": "IN_PROGRESS",
        "output_tables": ["base.t"],
        "steps": {
            "primary-agent": {"status": "COMPLETED", "agent": "primary-agent",
                              "output": "x", "completed_at": "2026-06-01T00:00:00+00:00"},
            "data-analyst": {"status": "COMPLETED", "agent": "data-analyst",
                             "completed_at": "2026-06-01T01:00:00+00:00"},
        },
        "skipped_steps": {
            "pii-scanner": {"reason": "no PII expected", "skipped_at": "2026-06-01T02:00:00+00:00"},
        },
        "approvals": {
            "conceptual-model": {"status": "APPROVED", "decided_by": "human",
                                 "decided_at": "2026-06-01T03:00:00+00:00", "notes": "ok"},
        },
    }))

    counts = sync_from_files()
    # 2 completed steps + 1 skipped + 1 approval = 4 pipeline events
    assert counts["pipeline_events"] == 4
    assert counts["spec_registry"] == 1


# --- migrate_files_to_iceberg ---------------------------------------------


def test_migrate_empty_project_reports_shape(gov_project):
    """Migration on an empty project returns the full report shape (incl. the
    nested existing_sync) without error."""
    report = migrate_files_to_iceberg()
    assert "dq_rules" in report
    assert "golden_datasets" in report
    assert "existing_sync" in report
    assert report["dq_rules"] == {"files": 0, "rows": 0}


def test_migrate_dq_rules_and_golden_write_rows(gov_project):
    """DQ rules + golden dataset files migrate real rows into the governance DB."""
    dq_dir = config.DQ_RULES_DIR
    dq_dir.mkdir(parents=True, exist_ok=True)
    (dq_dir / "myspec.json").write_text(json.dumps({
        "spec": "myspec",
        "tables": ["base.t"],
        "rules": [{
            "rule_id": "R1", "category": "completeness", "priority": "P0",
            # threshold is a STRING expression in this framework's convention
            # (e.g. "result = 0"), matching the StringType schema column.
            "description": "not null", "sql": "SELECT 1", "threshold": "result = 0",
        }],
    }))

    gd_dir = config.GOLDEN_DATASETS_DIR
    gd_dir.mkdir(parents=True, exist_ok=True)
    (gd_dir / "myspec-golden.json").write_text(json.dumps({
        "spec": "myspec", "table": "base.t",
        "values": [{"description": "v", "expected_value": 1, "filters": {}, "column": "c"}],
    }))

    report = migrate_files_to_iceberg()
    assert report["dq_rules"]["rows"] >= 1
    assert report["golden_datasets"]["rows"] >= 1


def test_migrate_golden_is_idempotent_but_dq_rules_version(gov_project):
    """Pin the ACTUAL re-run semantics, which differ by table:

    * golden_datasets grain is (spec, column, filters) with no timestamp -> a
      re-migration of unchanged values adds 0 rows (genuinely idempotent).
    * dq_rules grain includes ``version``, which write_dq_rules auto-increments
      (MAX(version)+1) on every call -> a re-migration mints version 2 (rows>=1).
      This is documented versioning, but note it makes migrate_files_to_iceberg
      NOT row-count-idempotent for rules despite the function's docstring.
    """
    dq_dir = config.DQ_RULES_DIR
    dq_dir.mkdir(parents=True, exist_ok=True)
    (dq_dir / "myspec.json").write_text(json.dumps({
        "spec": "myspec", "tables": ["base.t"],
        "rules": [{
            "rule_id": "R1", "category": "completeness", "priority": "P0",
            "description": "not null", "sql": "SELECT 1", "threshold": "result = 0",
        }],
    }))
    gd_dir = config.GOLDEN_DATASETS_DIR
    gd_dir.mkdir(parents=True, exist_ok=True)
    (gd_dir / "myspec-golden.json").write_text(json.dumps({
        "spec": "myspec", "table": "base.t",
        "values": [{"description": "v", "expected_value": 1, "filters": {}, "column": "c"}],
    }))

    migrate_files_to_iceberg()
    report2 = migrate_files_to_iceberg()
    assert report2["golden_datasets"]["rows"] == 0   # idempotent
    assert report2["dq_rules"]["rows"] >= 1           # versioned (new version row)
