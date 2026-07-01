"""Tests for WP-3.2 — Zone-name canonicalization.

Verifies that:
  - ``normalize_zone`` maps every alias to its canonical name.
  - Internal logic branches on canonical names only.
  - Alias names are accepted at every boundary (rule matching, contract
    matching, zone registry) and normalised before reaching logic.
  - ``_verify_contracts_for_zone`` (which previously only matched canonical
    prefixes) now also matches alias-prefixed contracts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Unit tests: normalize_zone
# ---------------------------------------------------------------------------


def test_normalize_zone_canonical_passthrough():
    """Canonical names pass through normalize_zone unchanged."""
    from brightsmith.infra.governance.serializers import normalize_zone

    for canonical in ("bronze", "silver", "gold", "mcp"):
        assert normalize_zone(canonical) == canonical


def test_normalize_zone_aliases_map_to_canonical():
    """Every alias maps to its canonical medallion name."""
    from brightsmith.infra.governance.serializers import normalize_zone

    assert normalize_zone("raw") == "bronze"
    assert normalize_zone("base") == "silver"
    assert normalize_zone("consumable") == "gold"
    assert normalize_zone("ai_ready") == "mcp"


def test_normalize_zone_empty_and_none():
    """normalize_zone returns an empty string for falsy input."""
    from brightsmith.infra.governance.serializers import normalize_zone

    assert normalize_zone(None) == ""
    assert normalize_zone("") == ""


def test_normalize_zone_unknown_name_passthrough():
    """An unknown zone name is returned as-is (no silent failure)."""
    from brightsmith.infra.governance.serializers import normalize_zone

    assert normalize_zone("unknown_zone") == "unknown_zone"


# ---------------------------------------------------------------------------
# Unit tests: _rule_matches_zone
# ---------------------------------------------------------------------------


def test_rule_matches_zone_canonical_table():
    """A rule with a bronze.table entry matches the bronze zone."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": ["bronze.facts"], "sql": "SELECT 1"}
    assert _rule_matches_zone(rule, "bronze") is True


def test_rule_matches_zone_alias_table():
    """A rule with a raw.table entry (alias for bronze) still matches bronze."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": ["raw.facts"], "sql": "SELECT 1"}
    assert _rule_matches_zone(rule, "bronze") is True


def test_rule_matches_zone_alias_in_sql():
    """A rule whose SQL references raw.table (alias) matches the bronze zone."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": [], "sql": "SELECT COUNT(*) FROM raw.facts WHERE x IS NULL"}
    assert _rule_matches_zone(rule, "bronze") is True


def test_rule_does_not_match_different_zone():
    """A bronze rule must not match the silver zone."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": ["bronze.facts"], "sql": "SELECT 1"}
    assert _rule_matches_zone(rule, "silver") is False


def test_rule_alias_does_not_match_wrong_zone():
    """A raw rule (alias for bronze) must not match the silver zone."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": ["raw.facts"]}
    assert _rule_matches_zone(rule, "silver") is False


# ---------------------------------------------------------------------------
# Unit tests: _contract_matches_zone
# ---------------------------------------------------------------------------


def test_contract_matches_zone_canonical():
    """A contract with a bronze.table entry matches the bronze zone."""
    from brightsmith.run import _contract_matches_zone

    contract = {"table": "bronze.facts"}
    assert _contract_matches_zone(contract, "bronze") is True


def test_contract_matches_zone_alias():
    """A contract with a raw.table entry (alias for bronze) matches the bronze zone."""
    from brightsmith.run import _contract_matches_zone

    contract = {"table": "raw.facts"}
    assert _contract_matches_zone(contract, "bronze") is True


def test_contract_does_not_match_wrong_zone():
    """A raw contract (alias for bronze) does not match silver."""
    from brightsmith.run import _contract_matches_zone

    contract = {"table": "raw.facts"}
    assert _contract_matches_zone(contract, "silver") is False


def test_contract_matches_consumable_to_gold():
    """A contract with consumable.table (alias for gold) matches the gold zone."""
    from brightsmith.run import _contract_matches_zone

    contract = {"table": "consumable.company_facts"}
    assert _contract_matches_zone(contract, "gold") is True


def test_contract_matches_ai_ready_to_mcp():
    """A contract with ai_ready.table (alias for mcp) matches the mcp zone."""
    from brightsmith.run import _contract_matches_zone

    contract = {"table": "ai_ready.tools"}
    assert _contract_matches_zone(contract, "mcp") is True


# ---------------------------------------------------------------------------
# Unit tests: register_zone normalizes aliases
# ---------------------------------------------------------------------------


def test_register_zone_normalizes_alias():
    """register_zone accepts an alias zone name and stores it canonically."""
    from brightsmith.run import _ZONE_REGISTRY, register_zone

    # Store the original registry state to restore it after.
    original = dict(_ZONE_REGISTRY)
    try:
        register_zone("raw", "my_module:main")
        assert "bronze" in _ZONE_REGISTRY, "alias 'raw' must be stored as 'bronze'"
        assert "raw" not in _ZONE_REGISTRY, "alias name must not remain in registry"
        assert _ZONE_REGISTRY["bronze"] == "my_module:main"
    finally:
        _ZONE_REGISTRY.clear()
        _ZONE_REGISTRY.update(original)


def test_register_zone_canonical_unchanged():
    """register_zone stores a canonical name as-is."""
    from brightsmith.run import _ZONE_REGISTRY, register_zone

    original = dict(_ZONE_REGISTRY)
    try:
        register_zone("silver", "silver_module:main")
        assert "silver" in _ZONE_REGISTRY
        assert _ZONE_REGISTRY["silver"] == "silver_module:main"
    finally:
        _ZONE_REGISTRY.clear()
        _ZONE_REGISTRY.update(original)


# ---------------------------------------------------------------------------
# Behavioral test: manifest with alias zone names is executed correctly
# ---------------------------------------------------------------------------

ROOT_ENV_VAR = "BRIGHTSMITH_PROJECT_ROOT"


def _run_runner(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        ROOT_ENV_VAR: str(tmp_path),
        "PYTHONPATH": str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    return subprocess.run(
        [sys.executable, "-m", "brightsmith.run", *args],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )


def _seed_bronze_warehouse(tmp_path: Path) -> None:
    """Seed bronze.seed_facts in tmp warehouse."""
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table

    warehouse = tmp_path / "data" / "bronze" / "iceberg_warehouse"
    catalog_db = tmp_path / "data" / "catalog" / "catalog.db"
    catalog = get_catalog(warehouse, catalog_db)
    schema = Schema(
        NestedField(1, "record_id", StringType(), required=False),
        NestedField(2, "val", StringType(), required=False),
    )
    table = get_or_create_table(catalog, "bronze", "seed_facts", schema)
    append_data(table, [{"record_id": "r1", "val": "a"}, {"record_id": "r2", "val": "b"}])


def test_manifest_with_alias_zone_name_executes(tmp_path):
    """A manifest using the alias zone name 'raw' must execute the transform.

    The pipeline registry normalises 'raw' → 'bronze' at load time, so
    ``--zone bronze`` still finds and runs the registered module.
    """
    _seed_bronze_warehouse(tmp_path)

    # Noop transform that just returns success
    (tmp_path / "noop_transform.py").write_text(
        "def main():\n    return {'rows_promoted': 0, 'rows_skipped': 0}\n"
    )
    # Domain manifest uses the ALIAS zone name "raw" (not "bronze")
    (tmp_path / "domain").mkdir(parents=True, exist_ok=True)
    (tmp_path / "domain" / "manifest.yaml").write_text(
        "name: test\n"
        "version: '0.1'\n"
        "pipeline:\n"
        "  raw:\n"          # alias — should be normalised to bronze
        "    module: noop_transform\n"
        "    function: main\n"
    )
    # Write a simple passing rule so DQ gate has something to check
    rules_dir = tmp_path / "governance" / "dq-rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "test.json").write_text(json.dumps({
        "spec": "test",
        "tables": ["bronze.seed_facts"],
        "rules": [{
            "rule_id": "ALIAS-PASS",
            "priority": "P3",
            "status": "active",
            "category": "completeness",
            "sql": "SELECT COUNT(*) FROM bronze.seed_facts WHERE val = 'zzz'",
            "threshold": "result = 0",
        }],
    }))

    from brightsmith.run import EXIT_SUCCESS

    proc = _run_runner(tmp_path, "--zone", "bronze")
    assert proc.returncode == EXIT_SUCCESS, proc.stdout + proc.stderr


def test_alias_namespace_contract_matches_canonical_zone_verify(tmp_path):
    """_verify_contracts_for_zone must match contracts with alias-prefixed tables.

    Previously `_verify_contracts_for_zone` filtered with `startswith(f"{zone}.")`,
    which would miss a contract whose table is `raw.seed_facts` when zone='bronze'.
    After the WP-3.2 fix it uses `_contract_matches_zone`, which is alias-aware.
    """
    from brightsmith.run import _verify_contracts_for_zone

    # Write a contract YAML with an alias-prefixed table under the tmp project root.
    contracts_dir = tmp_path / "governance" / "data-contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "version": "1",
        "metadata": {"name": "raw-seed-facts", "status": "active"},
        "schema": {"table": "raw.seed_facts", "columns": []},  # alias namespace
        "grain": {"fields": ["record_id"]},
        "lineage": {"sources": []},
    }
    (contracts_dir / "raw-seed-facts.yaml").write_text(yaml.dump(contract))

    # Patch PROJECT_ROOT so list_contracts sees the tmp dir.
    import brightsmith.config as cfg

    original_root = cfg.PROJECT_ROOT
    try:
        cfg.PROJECT_ROOT = tmp_path
        valid, violated = _verify_contracts_for_zone("bronze")
        # The alias-aware fix means the contract is now FOUND and attempted
        # (valid + violated > 0).  Before the fix, `startswith("bronze.")`
        # missed `raw.seed_facts`, so both counts were 0.
        assert valid + violated == 1, (
            f"Expected alias-prefixed 'raw.seed_facts' contract to be found for "
            f"bronze zone (valid + violated == 1); got valid={valid}, violated={violated}"
        )
    finally:
        cfg.PROJECT_ROOT = original_root
