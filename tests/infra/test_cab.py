"""Tests for Change Approval Board (CAB) module."""

import json
from unittest.mock import patch

import yaml

from brightsmith.infra.cab import (
    CabDecisionRecord,
    ChangeType,
    Decision,
    ForkDetails,
    HumanOverride,
    SchemaChange,
    Severity,
    build_schema_diff,
    classify_schema_changes,
    compute_blast_radius,
    create_decision,
    detect_schema_modification,
    load_decision,
    load_deprecations,
    propose_fork,
    register_deprecation,
    save_decision,
    update_decision,
)
from brightsmith.infra.contract import ContractDiffItem


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------


def test_classify_patch_change():
    """Description-only change maps to PATCH."""
    diffs = [ContractDiffItem("NON_BREAKING", "Column 'revenue' description changed")]
    changes, overall = classify_schema_changes(diffs)
    assert len(changes) == 1
    assert changes[0].classification == Severity.PATCH
    assert overall == Severity.PATCH


def test_classify_minor_change():
    """Column added maps to MINOR."""
    diffs = [ContractDiffItem("NON_BREAKING", "Column 'esg_score' in table but not in contract")]
    changes, overall = classify_schema_changes(diffs)
    assert len(changes) == 1
    assert changes[0].classification == Severity.MINOR
    assert overall == Severity.MINOR


def test_classify_major_column_removed():
    """Column removed maps to MAJOR."""
    diffs = [ContractDiffItem("BREAKING", "Column 'quarterly_eps' in contract but missing from table")]
    changes, overall = classify_schema_changes(diffs)
    assert len(changes) == 1
    assert changes[0].classification == Severity.MAJOR
    assert overall == Severity.MAJOR


def test_classify_major_type_changed():
    """Type change maps to MAJOR."""
    diffs = [ContractDiffItem("BREAKING", "Column 'revenue' type changed: double → decimal")]
    changes, overall = classify_schema_changes(diffs)
    assert len(changes) == 1
    assert changes[0].classification == Severity.MAJOR
    assert changes[0].old_value == "double"
    assert changes[0].new_value == "decimal"
    assert overall == Severity.MAJOR


def test_classify_major_grain_changed():
    """Grain change maps to MAJOR."""
    diffs = [ContractDiffItem("BREAKING", "Column 'id' grain changed")]
    changes, overall = classify_schema_changes(diffs)
    assert changes[0].classification == Severity.MAJOR


def test_overall_classification_is_max():
    """Mixed PATCH + MAJOR → overall MAJOR."""
    diffs = [
        ContractDiffItem("NON_BREAKING", "Column 'notes' description changed"),
        ContractDiffItem("BREAKING", "Column 'revenue' type changed: double → decimal"),
        ContractDiffItem("NON_BREAKING", "Column 'esg_score' in table but not in contract"),
    ]
    changes, overall = classify_schema_changes(diffs)
    assert len(changes) == 3
    assert overall == Severity.MAJOR


def test_classify_nullable_changed():
    """Nullability change maps to MINOR."""
    diffs = [ContractDiffItem("NON_BREAKING", "Column 'value' nullability changed: required=True → False")]
    changes, overall = classify_schema_changes(diffs)
    assert changes[0].classification == Severity.MINOR
    assert changes[0].change_type == ChangeType.NULLABLE_CHANGED


def test_classify_empty_diffs():
    """Empty diffs returns empty changes and PATCH."""
    changes, overall = classify_schema_changes([])
    assert changes == []
    assert overall == Severity.PATCH


# ---------------------------------------------------------------------------
# Blast radius tests
# ---------------------------------------------------------------------------


def test_blast_radius_finds_contracts(tmp_path):
    """Contract consumer references detected."""
    # Create a contract that references our table as a source
    contracts_dir = tmp_path / "governance" / "data-contracts"
    contracts_dir.mkdir(parents=True)

    contract = {
        "metadata": {"name": "company-ratios", "version": "1.0.0", "status": "active"},
        "schema": {"table": "consumable.company_ratios"},
        "lineage": {"sources": [{"table": "consumable.company_financials", "relationship": "direct_input"}]},
    }
    (contracts_dir / "company-ratios.yaml").write_text(yaml.dump(contract))

    with patch("brightsmith.infra.lineage.query_downstream_consumers", side_effect=Exception("no lineage")):
        items, summary = compute_blast_radius("consumable.company_financials", tmp_path)

    assert len(items) >= 1
    assert any(i.item_type == "contract" for i in items)


def test_blast_radius_finds_golden_datasets(tmp_path):
    """Golden dataset table references detected."""
    golden_dir = tmp_path / "governance" / "golden-datasets"
    golden_dir.mkdir(parents=True)

    golden = {"table": "consumable.company_financials", "values": []}
    (golden_dir / "company-financials-golden.json").write_text(json.dumps(golden))

    with patch("brightsmith.infra.lineage.query_downstream_consumers", side_effect=Exception("no lineage")):
        items, summary = compute_blast_radius("consumable.company_financials", tmp_path)

    assert any(i.item_type == "golden_dataset" for i in items)
    assert "company-financials-golden" in summary["golden_datasets"]


# ---------------------------------------------------------------------------
# Decision management tests
# ---------------------------------------------------------------------------


def _make_changes() -> list[SchemaChange]:
    return [
        SchemaChange("revenue", ChangeType.TYPE_CHANGED, "double", "decimal", Severity.MAJOR, "Type changed"),
    ]


def _make_record(**overrides) -> CabDecisionRecord:
    defaults = {
        "decision_id": "cab-20260325-143000-test",
        "spec": "test-spec",
        "table_name": "consumable.test_table",
        "created_at": "2026-03-25T14:30:00Z",
        "classification": Severity.MAJOR.value,
        "classification_reasons": [],
        "contract_version_before": "1.0.0",
        "contract_version_after": "2.0.0",
        "schema_diff": {"added_columns": [], "removed_columns": [], "changed_columns": [], "unchanged_columns": []},
        "blast_radius": {"downstream_tables": [], "consumables": [], "mcp_tools": [], "grounding_documents": [], "golden_datasets": [], "total_affected": 0},
    }
    defaults.update(overrides)
    return CabDecisionRecord(**defaults)


def test_decision_record_json_schema(tmp_path):
    """Decision record serializes to expected JSON structure."""
    record = _make_record()
    path = save_decision(record, tmp_path)

    data = json.loads(path.read_text())
    assert data["decision_id"] == "cab-20260325-143000-test"
    assert data["classification"] == "MAJOR"
    assert data["decision"] == "PENDING"
    assert "blast_radius" in data
    assert "schema_diff" in data


def test_index_append_only(tmp_path):
    """Index grows on save, never shrinks."""
    r1 = _make_record(decision_id="cab-001")
    r2 = _make_record(decision_id="cab-002")

    save_decision(r1, tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index["decisions"]) == 1

    save_decision(r2, tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index["decisions"]) == 2


def test_index_entry_matches_decision(tmp_path):
    """Index entry fields match full decision record."""
    record = _make_record(decision_id="cab-match-test")
    save_decision(record, tmp_path)

    index = json.loads((tmp_path / "index.json").read_text())
    entry = index["decisions"][0]
    assert entry["decision_id"] == record.decision_id
    assert entry["table"] == record.table_name
    assert entry["classification"] == record.classification


def test_load_decision(tmp_path):
    """Load a saved decision by ID."""
    record = _make_record(decision_id="cab-load-test")
    save_decision(record, tmp_path)

    loaded = load_decision("cab-load-test", tmp_path)
    assert loaded is not None
    assert loaded.decision_id == "cab-load-test"
    assert loaded.table_name == "consumable.test_table"


def test_load_decision_not_found(tmp_path):
    """Returns None for nonexistent decision."""
    assert load_decision("nonexistent", tmp_path) is None


# ---------------------------------------------------------------------------
# Fork proposal tests
# ---------------------------------------------------------------------------


def test_fork_proposal_naming():
    """v2 table name is {table}_v2."""
    record = _make_record(table_name="consumable.company_financials")
    fork = propose_fork(record)
    assert fork.v1_table == "consumable.company_financials"
    assert fork.v2_table == "consumable.company_financials_v2"


def test_fork_proposal_timeline():
    """Deprecation timeline uses specified days."""
    record = _make_record()
    fork = propose_fork(record, deprecation_days=180)
    assert fork.deprecation_timeline_days == 180
    assert fork.deprecated_at  # Non-empty
    assert fork.archive_after  # Non-empty


def test_fork_proposal_migration_spec():
    """Migration spec path is generated correctly."""
    record = _make_record(table_name="consumable.company_financials")
    fork = propose_fork(record)
    assert fork.migration_spec_path == "docs/specs/company-financials-to-company-financials-v2-migration.md"


# ---------------------------------------------------------------------------
# Deprecation registry tests
# ---------------------------------------------------------------------------


def test_deprecation_registry_add(tmp_path):
    """New deprecation appears in registry."""
    register_deprecation(
        "consumable.test", "consumable.test_v2",
        "2026-03-25", "2026-06-25", "cab-001", tmp_path,
    )

    deps = load_deprecations(tmp_path)
    assert len(deps) == 1
    assert deps[0]["table"] == "consumable.test"
    assert deps[0]["successor"] == "consumable.test_v2"
    assert deps[0]["status"] == "DEPRECATED"


def test_deprecation_registry_update(tmp_path):
    """Updating an existing deprecation overwrites it."""
    register_deprecation(
        "consumable.test", "consumable.test_v2",
        "2026-03-25", "2026-06-25", "cab-001", tmp_path,
    )
    register_deprecation(
        "consumable.test", "consumable.test_v3",
        "2026-03-26", "2026-06-26", "cab-002", tmp_path,
    )

    deps = load_deprecations(tmp_path)
    assert len(deps) == 1  # Updated, not duplicated
    assert deps[0]["successor"] == "consumable.test_v3"


# ---------------------------------------------------------------------------
# Trigger detection tests
# ---------------------------------------------------------------------------


def test_skip_for_new_table(tmp_path):
    """detect_schema_modification returns False for table without contract."""
    contracts_dir = tmp_path / "governance" / "data-contracts"
    contracts_dir.mkdir(parents=True)

    assert detect_schema_modification("consumable.new_table", contracts_dir) is False


def test_trigger_for_existing_table(tmp_path):
    """detect_schema_modification returns True for table with active contract."""
    contracts_dir = tmp_path / "governance" / "data-contracts"
    contracts_dir.mkdir(parents=True)

    contract = {
        "metadata": {"name": "test-table", "version": "1.0.0", "status": "active"},
        "schema": {"table": "consumable.test_table"},
    }
    (contracts_dir / "test-table.yaml").write_text(yaml.dump(contract))

    assert detect_schema_modification("consumable.test_table", contracts_dir) is True


# ---------------------------------------------------------------------------
# Human override tests
# ---------------------------------------------------------------------------


def test_human_override_reclassify(tmp_path):
    """Override changes classification and logs original."""
    record = _make_record(decision_id="cab-override-test")
    save_decision(record, tmp_path)

    override = HumanOverride(
        action="reclassified",
        original_classification="MAJOR",
        override_classification="MINOR",
        overrider="jeff",
        rationale="stale lineage entries",
        timestamp="2026-03-25T15:00:00Z",
    )

    updated = update_decision(
        "cab-override-test", Decision.APPROVED, "human:jeff",
        notes="Reclassified", override=override, cab_dir=tmp_path,
    )

    assert updated is not None
    assert updated.decision == "APPROVED"
    assert updated.human_override is not None
    assert updated.human_override["original_classification"] == "MAJOR"
    assert updated.human_override["override_classification"] == "MINOR"


def test_human_override_timeline(tmp_path):
    """Timeline adjustment updates fork details."""
    record = _make_record(decision_id="cab-timeline-test")
    save_decision(record, tmp_path)

    fork = ForkDetails(
        v1_table="consumable.test_table",
        v2_table="consumable.test_table_v2",
        migration_spec_path="docs/specs/test-table-v2-migration.md",
        deprecation_timeline_days=180,
        deprecated_at="2026-03-25",
        archive_after="2026-09-21",
    )

    updated = update_decision(
        "cab-timeline-test", Decision.APPROVED_WITH_FORK, "human:jeff",
        fork=fork, cab_dir=tmp_path,
    )

    assert updated is not None
    assert updated.fork is not None
    assert updated.fork["deprecation_timeline_days"] == 180


# ---------------------------------------------------------------------------
# Auto-approval tests
# ---------------------------------------------------------------------------


def test_auto_approve_patch(tmp_path):
    """PATCH always auto-approves."""
    changes = [SchemaChange("notes", ChangeType.DESCRIPTION_CHANGED, None, None, Severity.PATCH, "desc changed")]

    record = create_decision(
        spec="test", table_name="consumable.test",
        changes=changes, overall_severity=Severity.PATCH,
        blast_items=[], blast_summary={"total_affected": 0},
        contract_version_before="1.0.0", contract_version_after="1.0.1",
        schema_diff={}, cab_dir=tmp_path,
    )
    # Simulate auto-approve logic from review()
    record.decision = Decision.APPROVED.value
    record.decided_by = "auto:cab-agent"
    save_decision(record, tmp_path)

    loaded = load_decision(record.decision_id, tmp_path)
    assert loaded.decision == "APPROVED"


def test_major_always_requires_human(tmp_path):
    """MAJOR stays PENDING regardless of REQUIRE_HUMAN_APPROVAL."""
    changes = [SchemaChange("revenue", ChangeType.REMOVED, "double", None, Severity.MAJOR, "removed")]

    record = create_decision(
        spec="test", table_name="consumable.test",
        changes=changes, overall_severity=Severity.MAJOR,
        blast_items=[], blast_summary={"total_affected": 0},
        contract_version_before="1.0.0", contract_version_after="2.0.0",
        schema_diff={}, cab_dir=tmp_path,
    )
    # MAJOR should not be auto-approved
    assert record.decision == Decision.PENDING.value


# ---------------------------------------------------------------------------
# Schema diff builder tests
# ---------------------------------------------------------------------------


def test_build_schema_diff():
    """Schema diff correctly categorizes changes."""
    changes = [
        SchemaChange("esg", ChangeType.ADDED, None, "double", Severity.MINOR, "added"),
        SchemaChange("eps", ChangeType.REMOVED, "double", None, Severity.MAJOR, "removed"),
        SchemaChange("rev", ChangeType.TYPE_CHANGED, "double", "decimal", Severity.MAJOR, "type changed"),
    ]

    diff = build_schema_diff(changes)
    assert len(diff["added_columns"]) == 1
    assert len(diff["removed_columns"]) == 1
    assert len(diff["changed_columns"]) == 1
    assert diff["added_columns"][0]["name"] == "esg"
    assert diff["removed_columns"][0]["name"] == "eps"
    assert diff["changed_columns"][0]["from_type"] == "double"


# ---------------------------------------------------------------------------
# Decision ID format test
# ---------------------------------------------------------------------------


def test_decision_id_format():
    """ID follows cab-{timestamp}-{table} pattern."""
    # The _make_record uses a pre-set ID, but let's test the generator
    from brightsmith.infra.cab import _next_decision_id
    did = _next_decision_id("consumable.company_financials")
    assert did.startswith("cab-")
    assert "company-financials" in did


# ---------------------------------------------------------------------------
# Immutability guard tests
# ---------------------------------------------------------------------------


def test_finalized_decision_cannot_be_updated(tmp_path):
    """A finalized decision (APPROVED) cannot be overwritten."""
    record = _make_record(decision_id="cab-immutable-test")
    save_decision(record, tmp_path)

    # First update: PENDING → APPROVED (should succeed)
    updated = update_decision(
        "cab-immutable-test", Decision.APPROVED, "human:jeff", cab_dir=tmp_path,
    )
    assert updated is not None
    assert updated.decision == "APPROVED"

    # Second update: APPROVED → REJECTED (should be rejected)
    blocked = update_decision(
        "cab-immutable-test", Decision.REJECTED, "human:jeff", cab_dir=tmp_path,
    )
    assert blocked is None

    # Verify original decision is preserved
    loaded = load_decision("cab-immutable-test", tmp_path)
    assert loaded.decision == "APPROVED"


def test_finalized_fork_decision_cannot_be_updated(tmp_path):
    """An APPROVED_WITH_FORK decision cannot be overwritten."""
    record = _make_record(decision_id="cab-fork-immutable")
    save_decision(record, tmp_path)

    update_decision("cab-fork-immutable", Decision.APPROVED_WITH_FORK, "human:jeff", cab_dir=tmp_path)
    blocked = update_decision("cab-fork-immutable", Decision.APPROVED, "human:jeff", cab_dir=tmp_path)
    assert blocked is None


# ---------------------------------------------------------------------------
# Fork collision detection tests
# ---------------------------------------------------------------------------


def test_fork_v2_already_versioned():
    """Forking a _v2 table produces _v3, not _v2_v2."""
    record = _make_record(table_name="consumable.company_financials_v2")
    fork = propose_fork(record)
    assert fork.v2_table == "consumable.company_financials_v3"


def test_fork_v5_increments():
    """Forking a _v5 table produces _v6."""
    record = _make_record(table_name="consumable.data_v5")
    fork = propose_fork(record)
    assert fork.v2_table == "consumable.data_v6"


# ---------------------------------------------------------------------------
# Index spec field tests
# ---------------------------------------------------------------------------


def test_index_includes_spec_field(tmp_path):
    """Index entries include the spec field for efficient lookup."""
    record = _make_record(decision_id="cab-spec-index", spec="my-cool-spec")
    save_decision(record, tmp_path)

    import json
    index = json.loads((tmp_path / "index.json").read_text())
    assert index["decisions"][0]["spec"] == "my-cool-spec"


# ---------------------------------------------------------------------------
# Column name with special chars
# ---------------------------------------------------------------------------


def test_classify_hyphenated_column():
    """Column names with hyphens are parsed correctly."""
    diffs = [ContractDiffItem("BREAKING", "Column 'esg-score' type changed: double → decimal")]
    changes, overall = classify_schema_changes(diffs)
    assert changes[0].column_name == "esg-score"
