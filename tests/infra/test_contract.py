"""Tests for machine-readable data contracts."""

import json
from pathlib import Path

import yaml

from grist.infra.contract import (
    ContractDiffItem,
    ContractVerificationResult,
    bump_version,
    check_version_bump_required,
    diff_contract,
    generate_contract,
    list_contracts,
    load_contract,
    parse_version,
    save_contract,
    verify_contract,
)


def _make_contract(**overrides) -> dict:
    """Create a minimal valid contract dict."""
    contract = {
        "apiVersion": "grist/v1",
        "kind": "DataContract",
        "metadata": {
            "name": "test-table",
            "version": "1.0.0",
            "status": "active",
            "owner": "@data-steward",
            "domain": "",
            "created": "2026-03-19",
            "spec": "docs/specs/test.md",
        },
        "schema": {
            "table": "consumable.test_table",
            "namespace": "consumable",
            "grain": {"columns": ["id"], "description": "One row per entity"},
            "columns": [
                {"name": "id", "type": "integer", "required": True, "business_term": "BT-001", "is_cde": True, "description": "Entity ID"},
                {"name": "value", "type": "double", "required": False, "business_term": None, "is_cde": False, "description": "Metric value"},
            ],
        },
        "quality": {
            "freshness": {"max_staleness_hours": 24, "measured_by": "ingested_at"},
            "completeness": {"min_row_count": 1, "required_columns": ["id"]},
            "accuracy": {"golden_dataset": "", "min_pass_rate_pct": 80},
            "uniqueness": {"grain_unique": True},
            "dq_rules": {"rules_file": "", "p0_pass_required": True},
        },
        "lineage": {"sources": []},
        "consumers": [],
        "compatibility": {
            "breaking_changes": ["column_removed", "column_type_changed", "grain_changed"],
            "non_breaking_changes": ["column_added", "description_changed"],
            "deprecation_notice_days": 30,
        },
    }
    # Apply overrides
    for key, val in overrides.items():
        if "." in key:
            parts = key.split(".")
            d = contract
            for p in parts[:-1]:
                d = d[p]
            d[parts[-1]] = val
        else:
            contract[key] = val
    return contract


def test_save_and_load_contract(tmp_path):
    """Save and load should round-trip a contract."""
    contract = _make_contract()
    path = save_contract(contract, contracts_dir=tmp_path)
    assert path.exists()
    assert path.suffix == ".yaml"

    loaded = load_contract("test-table", contracts_dir=tmp_path)
    assert loaded is not None
    assert loaded["metadata"]["name"] == "test-table"
    assert loaded["metadata"]["version"] == "1.0.0"
    assert len(loaded["schema"]["columns"]) == 2


def test_load_contract_missing(tmp_path):
    """Loading a non-existent contract should return None."""
    result = load_contract("nonexistent", contracts_dir=tmp_path)
    assert result is None


def test_list_contracts(tmp_path):
    """List should return all contracts with metadata."""
    for name in ["alpha", "beta"]:
        c = _make_contract()
        c["metadata"]["name"] = name
        save_contract(c, contracts_dir=tmp_path)

    results = list_contracts(contracts_dir=tmp_path)
    assert len(results) == 2
    names = [r["name"] for r in results]
    assert "alpha" in names
    assert "beta" in names


def test_list_contracts_empty(tmp_path):
    """Empty directory should return empty list."""
    results = list_contracts(contracts_dir=tmp_path)
    assert results == []


def test_verify_missing_contract(tmp_path):
    """Verifying a missing contract should FAIL."""
    results = verify_contract("nonexistent", contracts_dir=tmp_path)
    assert len(results) == 1
    assert results[0].status == "FAIL"
    assert "not found" in results[0].detail


def test_parse_version():
    """parse_version should handle semver strings."""
    assert parse_version("1.0.0") == (1, 0, 0)
    assert parse_version("2.3.1") == (2, 3, 1)
    assert parse_version("1.0") == (1, 0, 0)


def test_bump_version_breaking():
    """Breaking changes should bump major version."""
    assert bump_version("1.0.0", "BREAKING") == "2.0.0"
    assert bump_version("2.3.1", "BREAKING") == "3.0.0"


def test_bump_version_non_breaking():
    """Non-breaking changes should bump minor version."""
    assert bump_version("1.0.0", "NON_BREAKING") == "1.1.0"
    assert bump_version("1.2.3", "NON_BREAKING") == "1.3.0"


def test_bump_version_patch():
    """Patch changes should bump patch version."""
    assert bump_version("1.0.0", "PATCH") == "1.0.1"


def test_version_bump_required_for_breaking():
    """Breaking changes without major bump should be rejected."""
    diffs = [ContractDiffItem("BREAKING", "column removed")]
    result = check_version_bump_required(diffs, "1.0.0", "1.1.0")
    assert result is not None
    assert "Breaking" in result


def test_version_bump_ok_for_breaking():
    """Breaking changes with major bump should be accepted."""
    diffs = [ContractDiffItem("BREAKING", "column removed")]
    result = check_version_bump_required(diffs, "1.0.0", "2.0.0")
    assert result is None


def test_version_bump_required_for_non_breaking():
    """Non-breaking changes without minor bump should be rejected."""
    diffs = [ContractDiffItem("NON_BREAKING", "column added")]
    result = check_version_bump_required(diffs, "1.0.0", "1.0.0")
    assert result is not None


def test_no_diffs_no_bump_needed():
    """No changes should not require a version bump."""
    result = check_version_bump_required([], "1.0.0", "1.0.0")
    assert result is None


def test_contract_lifecycle_transitions(tmp_path):
    """Contract should support DRAFT → ACTIVE → DEPRECATED."""
    contract = _make_contract()
    assert contract["metadata"]["status"] == "active"

    # Can be draft
    contract["metadata"]["status"] = "draft"
    save_contract(contract, contracts_dir=tmp_path)
    loaded = load_contract("test-table", contracts_dir=tmp_path)
    assert loaded["metadata"]["status"] == "draft"

    # Can be active
    contract["metadata"]["status"] = "active"
    save_contract(contract, contracts_dir=tmp_path)
    loaded = load_contract("test-table", contracts_dir=tmp_path)
    assert loaded["metadata"]["status"] == "active"

    # Can be deprecated
    contract["metadata"]["status"] = "deprecated"
    save_contract(contract, contracts_dir=tmp_path)
    loaded = load_contract("test-table", contracts_dir=tmp_path)
    assert loaded["metadata"]["status"] == "deprecated"


def test_diff_no_drift(tmp_path):
    """Diff with no contract should return info message."""
    diffs = diff_contract("nonexistent", contracts_dir=tmp_path)
    assert len(diffs) == 1
    assert diffs[0].change_type == "INFO"


def test_verification_result_fields():
    """ContractVerificationResult should store all fields."""
    r = ContractVerificationResult("schema_match", "PASS", "19/19 columns")
    assert r.check == "schema_match"
    assert r.status == "PASS"
    assert r.detail == "19/19 columns"


def test_contract_diff_item_fields():
    """ContractDiffItem should store all fields."""
    d = ContractDiffItem("BREAKING", "Column 'revenue' removed")
    assert d.change_type == "BREAKING"
    assert d.description == "Column 'revenue' removed"
