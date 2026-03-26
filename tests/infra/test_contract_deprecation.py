"""Tests for contract deprecation extensions."""

import yaml

from brightsmith.infra.contract import (
    bump_version,
    deprecate_contract,
    load_contract,
    save_contract,
)


def _make_contract(**overrides) -> dict:
    """Create a minimal valid contract dict."""
    contract = {
        "apiVersion": "brightsmith/v1",
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
            "namespace": "gold",
            "grain": {"columns": ["id"], "description": "One row per entity"},
            "columns": [
                {"name": "id", "type": "integer", "required": True},
                {"name": "value", "type": "double", "required": False},
            ],
        },
        "quality": {},
        "lineage": {"sources": []},
        "consumers": [],
        "compatibility": {
            "breaking_changes": ["column_removed", "column_type_changed"],
            "non_breaking_changes": ["column_added"],
            "deprecation_notice_days": 30,
        },
    }
    for key, val in overrides.items():
        if key in contract:
            if isinstance(contract[key], dict) and isinstance(val, dict):
                contract[key].update(val)
            else:
                contract[key] = val
    return contract


def test_deprecate_contract_sets_status(tmp_path):
    """Status changes to 'deprecated'."""
    contract = _make_contract()
    save_contract(contract, tmp_path)

    result = deprecate_contract("test-table", "test-table-v2", "2026-06-25", tmp_path)

    assert result is not None
    assert result["metadata"]["status"] == "deprecated"

    reloaded = load_contract("test-table", tmp_path)
    assert reloaded["metadata"]["status"] == "deprecated"


def test_deprecate_contract_adds_fields(tmp_path):
    """deprecated_at, archive_after, successor_contract populated."""
    contract = _make_contract()
    save_contract(contract, tmp_path)

    result = deprecate_contract("test-table", "test-table-v2", "2026-06-25", tmp_path)

    compat = result["compatibility"]
    assert compat["archive_after"] == "2026-06-25"
    assert compat["successor_contract"] == "test-table-v2"
    assert "deprecated_at" in compat


def test_deprecate_contract_preserves_schema(tmp_path):
    """Schema section unchanged after deprecation."""
    contract = _make_contract()
    original_schema = contract["schema"].copy()
    save_contract(contract, tmp_path)

    result = deprecate_contract("test-table", "test-table-v2", "2026-06-25", tmp_path)

    assert result["schema"]["table"] == original_schema["table"]
    assert len(result["schema"]["columns"]) == len(original_schema["columns"])


def test_deprecate_contract_not_found(tmp_path):
    """Returns None when contract doesn't exist."""
    result = deprecate_contract("nonexistent", "successor", "2026-06-25", tmp_path)
    assert result is None


def test_bump_version_explicit_patch():
    """bump_version('1.2.3', 'PATCH') returns '1.2.4'."""
    assert bump_version("1.2.3", "PATCH") == "1.2.4"
    assert bump_version("2.0.0", "PATCH") == "2.0.1"
    assert bump_version("0.0.0", "PATCH") == "0.0.1"
