"""In-process coverage tests for pipeline_gate.py's validation, transition,
status, audit, and CLI surfaces (W1 — docs/technical-audit-2026-07-02.md,
finding H3: "governance enforcement core is the least-tested code in the
repo", 25% coverage).

tests/infra/test_pipeline_gate.py already characterizes the CLI's
cross-process behavior via subprocess.run — real and valuable, but
pytest-cov cannot see coverage inside a spawned Python process, so
``validate``, ``_validate_zone_specific``, ``_validate_warehouse_population``,
``check_zone_transition``, ``status_summary``, ``audit_report``, and every
``_cmd_*`` CLI handler stayed dark despite being exercised by those tests.

These tests call the same code IN-PROCESS: most hand-build ``gate._state``
directly (no I/O) the way ``test_check_prerequisites_blocks_then_clears``
already does in the sibling file; a few that need a real Iceberg warehouse or
on-disk state files use ``brightsmith.config.configure()`` to point every
config-derived path at ``tmp_path``, restored after each test.
"""

from __future__ import annotations

import json

import pytest

from brightsmith.infra.pipeline_gate import (
    BRONZE_ZONE_STEPS,
    GOLD_GREENFIELD_STEPS,
    PipelineGate,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_config():
    """Snapshot and restore the global config around a test (matches
    tests/infra/test_config.py's convention)."""
    import brightsmith.config as cfg

    saved = cfg.get_config()
    yield cfg
    cfg.configure(
        project_root=saved.project_root,
        project_name=saved.project_name,
        require_human_approval=saved.require_human_approval,
    )


def _bronze_gate(tmp_path, spec="s1", all_completed=False) -> PipelineGate:
    """A PipelineGate with a hand-built bronze state — zero I/O."""
    gate = PipelineGate(spec, state_dir=tmp_path)
    status = "COMPLETED" if all_completed else "NOT_STARTED"
    steps = {
        step.name: {
            "status": status,
            "agent": step.agent,
            "requires": list(step.requires),
            "blocking": step.blocking,
            "skippable": step.skippable,
        }
        for step in BRONZE_ZONE_STEPS
    }
    gate._state = {
        "spec": spec,
        "zone": "bronze",
        "mode": "greenfield",
        "started": "2026-01-01T00:00:00+00:00",
        "steps": steps,
        "skipped_steps": {},
        "approvals": {},
    }
    return gate


def _gold_gate(tmp_path, spec="g1", mode="greenfield") -> PipelineGate:
    steps_def = GOLD_GREENFIELD_STEPS
    gate = PipelineGate(spec, state_dir=tmp_path)
    steps = {
        step.name: {
            "status": "COMPLETED",
            "agent": step.agent,
            "requires": list(step.requires),
            "blocking": step.blocking,
            "skippable": step.skippable,
        }
        for step in steps_def
    }
    gate._state = {
        "spec": spec,
        "zone": "gold",
        "mode": mode,
        "started": "2026-01-01T00:00:00+00:00",
        "steps": steps,
        "skipped_steps": {"cab-review": {"reason": "new table", "evidence": "governance/x.md"}},
        "approvals": {},
    }
    steps["cab-review"]["status"] = "SKIPPED"
    return gate


def _seed_warehouse(tmp_path, zone="bronze", table="seed_facts", rows=None) -> None:
    """Seed a populated Iceberg table at the config-derived warehouse path.

    Requires ``config.configure(project_root=tmp_path)`` to already be in effect.
    """
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
    from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table

    catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
    schema = Schema(
        NestedField(1, "record_id", StringType(), required=False),
        NestedField(2, "val", StringType(), required=False),
    )
    tbl = get_or_create_table(catalog, zone, table, schema)
    if rows is None:
        rows = [{"record_id": "r1", "val": "a"}]
    if rows:
        append_data(tbl, rows)


# ---------------------------------------------------------------------------
# validate() — top-level orchestration
# ---------------------------------------------------------------------------


def test_validate_names_not_started_steps(tmp_path):
    gate = _bronze_gate(tmp_path, all_completed=False)
    valid, issues = gate.validate()
    assert valid is False
    assert any("governance-reviewer-pre" in i and "NOT_STARTED" in i for i in issues)


def test_validate_skip_missing_reason_and_evidence(tmp_path):
    gate = _bronze_gate(tmp_path)
    gate._state["skipped_steps"]["pii-scanner"] = {"reason": "", "evidence": ""}
    gate._state["steps"]["pii-scanner"]["status"] = "SKIPPED"
    valid, issues = gate.validate()
    assert valid is False
    assert any("skipped without reason" in i for i in issues)
    assert any("skipped without evidence" in i for i in issues)


def test_validate_skip_of_non_skippable_step_is_an_issue(tmp_path):
    gate = _bronze_gate(tmp_path)
    # primary-agent is not skippable per BRONZE_ZONE_STEPS.
    gate._state["skipped_steps"]["primary-agent"] = {
        "reason": "because", "evidence": "governance/x.md",
    }
    gate._state["steps"]["primary-agent"]["status"] = "SKIPPED"
    valid, issues = gate.validate()
    assert valid is False
    assert any("is NOT skippable but was skipped" in i for i in issues)


def test_validate_output_file_missing(tmp_path):
    gate = _bronze_gate(tmp_path, all_completed=True)
    gate._state["steps"]["governance-reviewer-pre"]["output"] = "governance/does-not-exist.md"
    valid, issues = gate.validate()
    assert valid is False
    assert any("output file missing" in i for i in issues)


def test_validate_output_hash_match_is_not_an_issue(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _hash_file

    out = tmp_path / "governance" / "eda" / "out.md"
    out.parent.mkdir(parents=True)
    out.write_text("stable content\n")

    gate = _bronze_gate(tmp_path, all_completed=True)
    gate._state["steps"]["governance-reviewer-pre"]["output"] = "governance/eda/out.md"
    gate._state["steps"]["governance-reviewer-pre"]["output_hash"] = _hash_file(out)
    _seed_warehouse(tmp_path)

    valid, issues = gate.validate()
    assert not any("modified after completion" in i for i in issues), issues


def test_validate_output_hash_mismatch_is_flagged(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    out = tmp_path / "governance" / "eda" / "out.md"
    out.parent.mkdir(parents=True)
    out.write_text("v1\n")

    gate = _bronze_gate(tmp_path, all_completed=True)
    gate._state["steps"]["governance-reviewer-pre"]["output"] = "governance/eda/out.md"
    gate._state["steps"]["governance-reviewer-pre"]["output_hash"] = "sha256:not-the-real-hash"

    valid, issues = gate.validate()
    assert any("modified after completion" in i for i in issues)


# ---------------------------------------------------------------------------
# _validate_zone_specific — gold/mcp DQ rules, golden dataset, physical model,
# CAB PENDING decisions
# ---------------------------------------------------------------------------


def test_zone_specific_gold_requires_dq_rules_file(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_golden_dataset(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)

    valid, issues = gate.validate()
    assert any("DQ rules file missing" in i for i in issues)


def test_zone_specific_gold_dq_rules_file_with_zero_rules(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    dq_dir = tmp_path / "governance" / "dq-rules"
    dq_dir.mkdir(parents=True)
    (dq_dir / f"{gate.spec}.json").write_text(json.dumps({"rules": []}))
    _write_golden_dataset(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)

    valid, issues = gate.validate()
    assert any("contains 0 rules" in i for i in issues)


def test_zone_specific_gold_dq_rules_file_malformed_json(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    dq_dir = tmp_path / "governance" / "dq-rules"
    dq_dir.mkdir(parents=True)
    (dq_dir / f"{gate.spec}.json").write_text("{not json")
    _write_golden_dataset(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)

    valid, issues = gate.validate()
    assert any("could not be read as JSON" in i for i in issues)


def test_zone_specific_gold_requires_golden_dataset(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_dq_rules(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)

    valid, issues = gate.validate()
    assert any("Golden dataset missing" in i for i in issues)


def test_zone_specific_gold_golden_dataset_too_few_values(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_dq_rules(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)
    gd_dir = tmp_path / "governance" / "golden-datasets"
    gd_dir.mkdir(parents=True)
    (gd_dir / f"{gate.spec}-golden.json").write_text(json.dumps({"values": [1, 2]}))

    valid, issues = gate.validate()
    assert any("has 2 values (minimum 3)" in i for i in issues)


def test_zone_specific_gold_golden_dataset_malformed_json(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_dq_rules(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)
    gd_dir = tmp_path / "governance" / "golden-datasets"
    gd_dir.mkdir(parents=True)
    (gd_dir / f"{gate.spec}-golden.json").write_text("not json")

    valid, issues = gate.validate()
    assert any("could not be read as JSON" in i for i in issues)


def test_zone_specific_gold_golden_dataset_records_key_accepted(tmp_path, restore_config):
    """The 'records' key is an accepted alternative to 'values'."""
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_dq_rules(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)
    gd_dir = tmp_path / "governance" / "golden-datasets"
    gd_dir.mkdir(parents=True)
    (gd_dir / f"{gate.spec}-golden.json").write_text(json.dumps({"records": [1, 2, 3]}))

    valid, issues = gate.validate()
    assert not any("Golden dataset" in i for i in issues), issues


def test_zone_specific_gold_greenfield_requires_physical_model(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path, mode="greenfield")
    _write_dq_rules(tmp_path, gate.spec)
    _write_golden_dataset(tmp_path, gate.spec)

    valid, issues = gate.validate()
    assert any("Physical model missing" in i for i in issues)


def test_zone_specific_cab_pending_decision_blocks(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_dq_rules(tmp_path, gate.spec)
    _write_golden_dataset(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)

    cab_dir = tmp_path / "governance" / "cab-decisions"
    cab_dir.mkdir(parents=True)
    (cab_dir / "index.json").write_text(json.dumps({
        "decisions": [{"decision_id": "CAB-1", "spec": gate.spec, "decision": "PENDING"}],
    }))

    valid, issues = gate.validate()
    assert any("PENDING" in i and "CAB-1" in i for i in issues)


def test_zone_specific_cab_approved_decision_does_not_block(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_dq_rules(tmp_path, gate.spec)
    _write_golden_dataset(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)

    cab_dir = tmp_path / "governance" / "cab-decisions"
    cab_dir.mkdir(parents=True)
    (cab_dir / "index.json").write_text(json.dumps({
        "decisions": [{"decision_id": "CAB-1", "spec": gate.spec, "decision": "APPROVED"}],
    }))

    valid, issues = gate.validate()
    assert not any("PENDING" in i for i in issues), issues


def test_zone_specific_cab_index_corrupt_is_a_blocking_issue(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_dq_rules(tmp_path, gate.spec)
    _write_golden_dataset(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)

    cab_dir = tmp_path / "governance" / "cab-decisions"
    cab_dir.mkdir(parents=True)
    (cab_dir / "index.json").write_text("not json")

    valid, issues = gate.validate()
    assert any("CAB index could not be read" in i for i in issues)


def test_zone_specific_gold_all_present_passes(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="gold")
    gate = _gold_gate(tmp_path)
    _write_dq_rules(tmp_path, gate.spec)
    _write_golden_dataset(tmp_path, gate.spec)
    _write_physical_model(tmp_path, gate.spec)

    valid, issues = gate.validate()
    assert valid is True, issues


def _write_dq_rules(tmp_path, spec):
    dq_dir = tmp_path / "governance" / "dq-rules"
    dq_dir.mkdir(parents=True, exist_ok=True)
    (dq_dir / f"{spec}.json").write_text(json.dumps({
        "rules": [{"rule_id": "R1", "priority": "P1", "sql": "SELECT 1", "threshold": "result = 1"}],
    }))


def _write_golden_dataset(tmp_path, spec):
    gd_dir = tmp_path / "governance" / "golden-datasets"
    gd_dir.mkdir(parents=True, exist_ok=True)
    (gd_dir / f"{spec}-golden.json").write_text(json.dumps({"values": [1, 2, 3]}))


def _write_physical_model(tmp_path, spec):
    m_dir = tmp_path / "governance" / "models"
    m_dir.mkdir(parents=True, exist_ok=True)
    (m_dir / f"{spec}-physical.md").write_text("# physical model\n")


# ---------------------------------------------------------------------------
# _validate_warehouse_population
# ---------------------------------------------------------------------------


def test_warehouse_population_catalog_missing(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    gate = _bronze_gate(tmp_path, all_completed=True)
    issues = gate._validate_warehouse_population("bronze")
    assert any("Iceberg catalog not found" in i for i in issues)


def test_warehouse_population_namespace_missing(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="silver")  # different namespace than "bronze"
    gate = _bronze_gate(tmp_path, all_completed=True)
    issues = gate._validate_warehouse_population("bronze")
    assert any("Namespace 'bronze' not found" in i for i in issues)


def test_warehouse_population_no_tables_in_namespace(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
    from brightsmith.infra.iceberg_setup import get_catalog

    catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
    catalog.create_namespace("bronze")

    gate = _bronze_gate(tmp_path, all_completed=True)
    issues = gate._validate_warehouse_population("bronze")
    assert any("No tables found" in i for i in issues)


def test_warehouse_population_zero_row_table(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="bronze", rows=[])
    gate = _bronze_gate(tmp_path, all_completed=True)
    issues = gate._validate_warehouse_population("bronze")
    assert any("has 0 rows" in i for i in issues)


def test_warehouse_population_populated_table_passes(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="bronze")
    gate = _bronze_gate(tmp_path, all_completed=True)
    issues = gate._validate_warehouse_population("bronze")
    assert issues == []


def test_warehouse_population_table_read_failure_is_reported(tmp_path, restore_config, monkeypatch):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="bronze")

    import brightsmith.infra.iceberg_setup as iceberg_mod

    class _BoomCatalog:
        def list_namespaces(self):
            return [("bronze",)]

        def list_tables(self, ns):
            return [("bronze", "seed_facts")]

        def load_table(self, ident):
            raise RuntimeError("simulated table load failure")

    # get_catalog is imported INSIDE _validate_warehouse_population at call
    # time, so patching the source module (not pipeline_gate's namespace) is
    # what actually takes effect.
    monkeypatch.setattr(iceberg_mod, "get_catalog", lambda *a, **k: _BoomCatalog())
    gate = _bronze_gate(tmp_path, all_completed=True)
    issues = gate._validate_warehouse_population("bronze")
    assert any("Failed to read table" in i and "simulated table load failure" in i for i in issues)


def test_warehouse_population_relocation_error_surfaces_loudly(tmp_path, restore_config, monkeypatch):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="bronze")

    import brightsmith.infra.iceberg_setup as iceberg_mod
    from brightsmith.infra.iceberg_setup import WarehouseRelocationError

    def _boom(*a, **k):
        raise WarehouseRelocationError("warehouse looks relocated: run relocate --apply")

    monkeypatch.setattr(iceberg_mod, "get_catalog", _boom)
    gate = _bronze_gate(tmp_path, all_completed=True)
    issues = gate._validate_warehouse_population("bronze")
    assert any("relocated" in i for i in issues)


def test_warehouse_population_outer_exception_is_reported(tmp_path, restore_config, monkeypatch):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="bronze")

    import brightsmith.infra.iceberg_setup as iceberg_mod

    def _boom(*a, **k):
        raise OSError("disk exploded")

    monkeypatch.setattr(iceberg_mod, "get_catalog", _boom)
    gate = _bronze_gate(tmp_path, all_completed=True)
    issues = gate._validate_warehouse_population("bronze")
    assert any("Warehouse verification failed" in i for i in issues)


# ---------------------------------------------------------------------------
# check_zone_transition
# ---------------------------------------------------------------------------


def test_check_transition_state_dir_missing(tmp_path):
    missing = tmp_path / "no-such-dir"
    ready, issues = PipelineGate.check_zone_transition("bronze", "silver", state_dir=missing)
    assert ready is False
    assert any("does not exist" in i for i in issues)


def test_check_transition_no_specs_for_zone(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    ready, issues = PipelineGate.check_zone_transition("bronze", "silver", state_dir=tmp_path)
    assert ready is False
    assert any("No specs found" in i for i in issues)


def test_check_transition_pda_not_completed_blocks(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="bronze")
    gate = _bronze_gate(tmp_path, spec="t1", all_completed=True)
    gate._save()

    ready, issues = PipelineGate.check_zone_transition("bronze", "silver", state_dir=tmp_path)
    assert ready is False
    assert any("principal-data-architect review not completed" in i for i in issues)


def test_check_transition_pda_completed_passes_bronze(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="bronze")
    gate = _bronze_gate(tmp_path, spec="t2", all_completed=True)
    gate._state["steps"]["principal-data-architect"] = {
        "status": "COMPLETED", "agent": "@principal-data-architect", "requires": ["staff-engineer"],
    }
    gate._save()

    ready, issues = PipelineGate.check_zone_transition("bronze", "silver", state_dir=tmp_path)
    assert ready is True, issues


def test_check_transition_insight_manager_required_from_silver(tmp_path, restore_config):
    """insight-manager is required at silver->gold and gold->mcp, NOT bronze->silver."""
    restore_config.configure(project_root=tmp_path)
    _seed_warehouse(tmp_path, zone="silver")
    gate = _bronze_gate(tmp_path, spec="t3", all_completed=True)
    gate._state["zone"] = "silver"
    gate._state["steps"]["principal-data-architect"] = {"status": "COMPLETED", "agent": "@principal-data-architect"}
    gate._save()

    ready, issues = PipelineGate.check_zone_transition("silver", "gold", state_dir=tmp_path)
    assert ready is False
    assert any("insight-manager report not completed" in i for i in issues)


def test_check_transition_aggregates_spec_validation_issues(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    gate = _bronze_gate(tmp_path, spec="incomplete-spec", all_completed=False)
    gate._save()

    ready, issues = PipelineGate.check_zone_transition("bronze", "silver", state_dir=tmp_path)
    assert ready is False
    assert any("[incomplete-spec]" in i for i in issues)


# ---------------------------------------------------------------------------
# status_summary
# ---------------------------------------------------------------------------


def test_status_summary_lists_steps_and_approvals(tmp_path):
    gate = _bronze_gate(tmp_path, all_completed=True)
    gate._state["approvals"]["business-terms"] = {
        "status": "APPROVED", "decided_by": "human:test", "decided_at": "2026-01-01T00:00:00+00:00",
    }
    summary = gate.status_summary()
    assert "governance-reviewer-pre" in summary
    assert "Approvals:" in summary
    assert "business-terms: APPROVED by human:test" in summary


def test_status_summary_marks_skipped_steps(tmp_path):
    gate = _bronze_gate(tmp_path)
    gate._state["skipped_steps"]["pii-scanner"] = {"reason": "r", "evidence": "e"}
    summary = gate.status_summary()
    lines = [ln for ln in summary.splitlines() if ln.strip().startswith("pii-scanner")]
    assert lines and "SKIPPED" in lines[0]


# ---------------------------------------------------------------------------
# audit_report
# ---------------------------------------------------------------------------


def test_audit_report_no_state_dir():
    from pathlib import Path

    report = PipelineGate.audit_report(state_dir=Path("/nonexistent/path/xyz"))
    assert "No pipeline state directory found." in report


def test_audit_report_markdown_includes_issues_approvals_and_skips(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    gate = _bronze_gate(tmp_path, spec="audit-md", all_completed=True)
    gate._state["steps"]["governance-reviewer-pre"]["output"] = "governance/eda/out.md"
    from brightsmith.infra.pipeline_gate import _hash_file
    out = tmp_path / "governance" / "eda" / "out.md"
    out.parent.mkdir(parents=True)
    out.write_text("v1")
    gate._state["steps"]["governance-reviewer-pre"]["output_hash"] = _hash_file(out)
    gate._state["approvals"]["business-terms"] = {
        "status": "APPROVED", "decided_by": "human:x", "decided_at": "2026-01-01T00:00:00+00:00",
        "notes": "looks good",
    }
    gate._state["skipped_steps"]["pii-scanner"] = {"reason": "no PII", "evidence": "governance/x.md"}
    gate._state["steps"]["pii-scanner"]["status"] = "SKIPPED"
    gate._save()

    report = PipelineGate.audit_report(fmt="markdown", state_dir=tmp_path)
    assert "audit-md" in report
    assert "### Skipped Steps" in report
    assert "business-terms" in report
    assert "### Output Integrity" in report
    assert "MATCH" in report


def test_audit_report_markdown_flags_modified_output(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    gate = _bronze_gate(tmp_path, spec="audit-mod", all_completed=True)
    out = tmp_path / "governance" / "eda" / "out.md"
    out.parent.mkdir(parents=True)
    out.write_text("v1")
    gate._state["steps"]["governance-reviewer-pre"]["output"] = "governance/eda/out.md"
    gate._state["steps"]["governance-reviewer-pre"]["output_hash"] = "sha256:deadbeef"
    gate._save()

    report = PipelineGate.audit_report(fmt="markdown", state_dir=tmp_path)
    assert "MODIFIED" in report


def test_audit_report_json_roundtrips(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    gate = _bronze_gate(tmp_path, spec="audit-json2", all_completed=False)
    gate._save()

    report = PipelineGate.audit_report(fmt="json", state_dir=tmp_path)
    data = json.loads(report)
    entry = next(s for s in data["specs"] if s["spec"] == "audit-json2")
    assert entry["valid"] is False
    assert entry["issues"]


# ---------------------------------------------------------------------------
# CLI handlers — called in-process for coverage credit (subprocess tests in
# test_pipeline_gate.py exercise the same commands cross-process).
# ---------------------------------------------------------------------------


def _ns(**kwargs):
    import argparse
    return argparse.Namespace(**kwargs)


def test_cli_init_and_check(tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_check, _cmd_init

    _cmd_init(_ns(spec="cli-1", zone="bronze", mode="greenfield"))
    out = capsys.readouterr().out
    assert "Initialized pipeline state" in out

    _cmd_check(_ns(spec="cli-1", step="governance-reviewer-pre"))
    out = capsys.readouterr().out
    assert "CLEAR" in out

    with pytest.raises(SystemExit) as exc:
        _cmd_check(_ns(spec="cli-1", step="primary-agent"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "BLOCKED" in err


def test_cli_complete_and_skip(tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_complete, _cmd_init, _cmd_skip

    _cmd_init(_ns(spec="cli-2", zone="silver", mode="greenfield"))
    capsys.readouterr()

    _cmd_complete(_ns(spec="cli-2", step="governance-reviewer-pre", output="", finding=None))
    out = capsys.readouterr().out
    assert "COMPLETED" in out

    with pytest.raises(SystemExit) as exc:
        _cmd_skip(_ns(spec="cli-2", step="data-steward", reason="", evidence=""))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR" in err


def test_cli_approve(tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_approve, _cmd_init

    _cmd_init(_ns(spec="cli-3", zone="bronze", mode="greenfield"))
    capsys.readouterr()
    _cmd_approve(_ns(spec="cli-3", artifact="business-terms", decision="APPROVED", by="human:x", notes="", document=""))
    out = capsys.readouterr().out
    assert "APPROVED by human:x" in out


def test_cli_validate_single_and_all(tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_init, _cmd_validate

    _cmd_init(_ns(spec="cli-4", zone="bronze", mode="greenfield"))
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        _cmd_validate(_ns(spec="cli-4", all=False))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out

    with pytest.raises(SystemExit) as exc:
        _cmd_validate(_ns(spec=None, all=True))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "cli-4" in out


def test_cli_validate_all_no_state_dir(tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_validate

    with pytest.raises(SystemExit) as exc:
        _cmd_validate(_ns(spec=None, all=True))
    assert exc.value.code == 1
    assert "No pipeline state directory found." in capsys.readouterr().out


def test_cli_check_transition(tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_check_transition

    with pytest.raises(SystemExit) as exc:
        _cmd_check_transition(_ns(from_zone="bronze", to_zone="silver"))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "NOT READY" in out


def test_cli_status(tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_init, _cmd_status

    _cmd_init(_ns(spec="cli-5", zone="bronze", mode="greenfield"))
    capsys.readouterr()
    _cmd_status(_ns(spec="cli-5"))
    out = capsys.readouterr().out
    assert "Pipeline Status: cli-5" in out


def test_cli_audit(tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_audit, _cmd_init

    _cmd_init(_ns(spec="cli-6", zone="bronze", mode="greenfield"))
    capsys.readouterr()
    _cmd_audit(_ns(format="markdown"))
    out = capsys.readouterr().out
    assert "cli-6" in out


def test_main_dispatches_to_status(tmp_path, restore_config, capsys, monkeypatch):
    """main() end-to-end argument parsing for one representative subcommand
    (the other subcommands are exercised directly above); this covers the
    argparse wiring + dispatch table itself."""
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import _cmd_init, main

    _cmd_init(_ns(spec="main-1", zone="bronze", mode="greenfield"))
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["pipeline_gate", "status", "main-1"])
    main()
    out = capsys.readouterr().out
    assert "Pipeline Status: main-1" in out


def test_main_no_command_prints_help(monkeypatch, capsys):
    from brightsmith.infra.pipeline_gate import main

    monkeypatch.setattr("sys.argv", ["pipeline_gate"])
    main()
    out = capsys.readouterr().out
    assert "usage" in out.lower() or "Pipeline Gate" in out


def test_main_dispatches_every_subcommand(tmp_path, restore_config, capsys, monkeypatch):
    """Drive main()'s full if/elif dispatch table (not just _cmd_* directly) so
    the argparse wiring for every subcommand gets coverage credit too."""
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import main

    monkeypatch.setattr("sys.argv", ["pipeline_gate", "init", "main-all", "--zone", "bronze"])
    main()
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["pipeline_gate", "check", "main-all", "governance-reviewer-pre"])
    main()
    assert "CLEAR" in capsys.readouterr().out

    monkeypatch.setattr("sys.argv", ["pipeline_gate", "complete", "main-all", "governance-reviewer-pre"])
    main()
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["pipeline_gate", "approve", "main-all", "business-terms", "--decision", "APPROVED", "--by", "human:x"])
    main()
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["pipeline_gate", "validate", "main-all"])
    with pytest.raises(SystemExit):
        main()
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["pipeline_gate", "check-transition", "bronze", "silver"])
    with pytest.raises(SystemExit):
        main()
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["pipeline_gate", "audit", "--format", "json"])
    main()
    assert "main-all" in capsys.readouterr().out

    # insight-manager is a ZONE_TRANSITION_STEPS entry (skippable=True,
    # looked up via _get_step_def's fallback loop regardless of the gate's
    # own zone) — used here purely to exercise the CLI's success path since
    # no BRONZE_ZONE_STEPS entry is itself skippable.
    monkeypatch.setattr("sys.argv", ["pipeline_gate", "skip", "main-all", "insight-manager", "--reason", "r", "--evidence", "e"])
    main()
    assert "SKIPPED" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# start_step — a public state-machine method with no wired CLI subcommand
# (grep confirms no caller in the repo today), but part of the documented
# PipelineGate API (see the class docstring's usage example).
# ---------------------------------------------------------------------------


def test_start_step_checks_prerequisites_then_marks_in_progress(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import GateBlockedError, PipelineGate

    gate = PipelineGate("start-1", state_dir=tmp_path)
    gate.init(zone="bronze")

    with pytest.raises(GateBlockedError):
        gate.start_step("primary-agent")

    gate.start_step("governance-reviewer-pre")
    assert gate._state["steps"]["governance-reviewer-pre"]["status"] == "IN_PROGRESS"
    assert gate._state["steps"]["governance-reviewer-pre"]["started_at"]

    # Re-starting an already-registered step overwrites status/timestamp
    # rather than re-registering it from the step definition.
    gate.start_step("governance-reviewer-pre")
    assert gate._state["steps"]["governance-reviewer-pre"]["status"] == "IN_PROGRESS"


def test_start_step_registers_unknown_step_from_definition(tmp_path, restore_config):
    """start_step on a step not yet present in `steps` (e.g. a zone-transition
    step) registers it fresh from the canonical Step definition."""
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import PipelineGate

    gate = PipelineGate("start-2", state_dir=tmp_path)
    gate.init(zone="bronze")
    for step in gate._state["steps"]:
        gate._state["steps"][step]["status"] = "COMPLETED"
    gate._state["steps"]["staff-engineer"]["status"] = "COMPLETED"

    gate.start_step("principal-data-architect")
    assert gate._state["steps"]["principal-data-architect"]["agent"] == "@principal-data-architect"
    assert gate._state["steps"]["principal-data-architect"]["status"] == "IN_PROGRESS"


def test_complete_step_with_output_records_hash(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    from brightsmith.infra.pipeline_gate import PipelineGate

    out = tmp_path / "governance" / "eda" / "report.md"
    out.parent.mkdir(parents=True)
    out.write_text("report content\n")

    gate = PipelineGate("complete-hash", state_dir=tmp_path)
    gate.init(zone="bronze")
    gate.complete_step("governance-reviewer-pre", output="governance/eda/report.md")

    step = gate._state["steps"]["governance-reviewer-pre"]
    assert step["output"] == "governance/eda/report.md"
    assert step["output_hash"].startswith("sha256:")
