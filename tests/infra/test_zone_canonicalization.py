"""Tests for direct medallion zone naming.

The framework standardizes on the physical medallion namespaces
(bronze/silver/gold/mcp) everywhere; there is no zone-alias translation
layer. These tests verify that:
  - ``normalize_zone`` is a null-safe accessor, NOT a translator — legacy
    alias names (raw/base/consumable/ai_ready) are returned verbatim, not
    mapped to a medallion name.
  - Rule/contract/zone-registry matching resolves the medallion namespace
    directly, and a legacy alias namespace no longer matches its old zone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Unit tests: normalize_zone (null-safe accessor, no translation)
# ---------------------------------------------------------------------------


def test_normalize_zone_canonical_passthrough():
    """Canonical names pass through normalize_zone unchanged."""
    from brightsmith.infra.governance.serializers import normalize_zone

    for canonical in ("bronze", "silver", "gold", "mcp"):
        assert normalize_zone(canonical) == canonical


def test_normalize_zone_does_not_translate_aliases():
    """Legacy alias names are NOT mapped — they pass through verbatim.

    The alias layer (raw→bronze, …) was removed; a stale alias name is just
    an unknown namespace now, returned as-is rather than resolved.
    """
    from brightsmith.infra.governance.serializers import normalize_zone

    assert normalize_zone("raw") == "raw"
    assert normalize_zone("base") == "base"
    assert normalize_zone("consumable") == "consumable"
    assert normalize_zone("ai_ready") == "ai_ready"


def test_normalize_zone_empty_and_none():
    """normalize_zone returns an empty string for falsy input."""
    from brightsmith.infra.governance.serializers import normalize_zone

    assert normalize_zone(None) == ""
    assert normalize_zone("") == ""


def test_normalize_table_name_null_safe_no_translation():
    """normalize_table_name is null-safe and performs no namespace translation."""
    from brightsmith.infra.governance.serializers import normalize_table_name

    assert normalize_table_name("bronze.facts") == "bronze.facts"
    assert normalize_table_name("raw.facts") == "raw.facts"  # not translated
    assert normalize_table_name(None) == ""
    assert normalize_table_name("no-dot") == "no-dot"


# ---------------------------------------------------------------------------
# Unit tests: _rule_matches_zone (medallion namespace, no alias resolution)
# ---------------------------------------------------------------------------


def test_rule_matches_zone_canonical_table():
    """A rule with a bronze.table entry matches the bronze zone."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": ["bronze.facts"], "sql": "SELECT 1"}
    assert _rule_matches_zone(rule, "bronze") is True


def test_rule_matches_zone_canonical_in_sql():
    """A rule whose SQL references bronze.table matches the bronze zone."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": [], "sql": "SELECT COUNT(*) FROM bronze.facts WHERE x IS NULL"}
    assert _rule_matches_zone(rule, "bronze") is True


def test_rule_alias_table_does_not_match_medallion_zone():
    """A rule with a raw.table entry no longer matches the bronze zone."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": ["raw.facts"], "sql": "SELECT 1"}
    assert _rule_matches_zone(rule, "bronze") is False


def test_rule_alias_in_sql_does_not_match_medallion_zone():
    """A rule whose SQL references raw.table no longer matches bronze.

    ``raw`` is not a known Iceberg namespace, so it is not even extracted as
    a table reference.
    """
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": [], "sql": "SELECT COUNT(*) FROM raw.facts WHERE x IS NULL"}
    assert _rule_matches_zone(rule, "bronze") is False


def test_rule_does_not_match_different_zone():
    """A bronze rule must not match the silver zone."""
    from brightsmith.run import _rule_matches_zone

    rule = {"tables": ["bronze.facts"], "sql": "SELECT 1"}
    assert _rule_matches_zone(rule, "silver") is False


# ---------------------------------------------------------------------------
# Unit tests: _contract_matches_zone (medallion namespace, no alias resolution)
# ---------------------------------------------------------------------------


def test_contract_matches_zone_canonical():
    """A contract with a bronze.table entry matches the bronze zone."""
    from brightsmith.run import _contract_matches_zone

    assert _contract_matches_zone({"table": "bronze.facts"}, "bronze") is True
    assert _contract_matches_zone({"table": "gold.company_facts"}, "gold") is True
    assert _contract_matches_zone({"table": "mcp.tools"}, "mcp") is True


def test_contract_alias_does_not_match_medallion_zone():
    """A contract with a legacy alias namespace no longer matches its old zone."""
    from brightsmith.run import _contract_matches_zone

    assert _contract_matches_zone({"table": "raw.facts"}, "bronze") is False
    assert _contract_matches_zone({"table": "consumable.company_facts"}, "gold") is False
    assert _contract_matches_zone({"table": "ai_ready.tools"}, "mcp") is False


def test_contract_does_not_match_wrong_zone():
    """A bronze contract does not match silver."""
    from brightsmith.run import _contract_matches_zone

    assert _contract_matches_zone({"table": "bronze.facts"}, "silver") is False


# ---------------------------------------------------------------------------
# Unit tests: register_zone stores names verbatim (no alias normalization)
# ---------------------------------------------------------------------------


def test_register_zone_canonical_stored_as_is():
    """register_zone stores a medallion name as-is.

    The registry holds an ordered list of steps per zone (H5.2) — register_zone
    stores a single-step list for its dotted "module:function" argument.
    """
    from brightsmith.run import _ZONE_REGISTRY, register_zone

    original = dict(_ZONE_REGISTRY)
    try:
        register_zone("silver", "silver_module:main")
        assert "silver" in _ZONE_REGISTRY
        steps = _ZONE_REGISTRY["silver"]
        assert len(steps) == 1
        assert steps[0].module == "silver_module"
        assert steps[0].function == "main"
    finally:
        _ZONE_REGISTRY.clear()
        _ZONE_REGISTRY.update(original)


def test_register_zone_alias_not_normalized():
    """register_zone no longer maps a legacy alias to a medallion name.

    A manifest using the old ``raw`` zone key registers the literal ``raw``
    zone (which no medallion pipeline step will ever match), not ``bronze``.
    """
    from brightsmith.run import _ZONE_REGISTRY, register_zone

    original = dict(_ZONE_REGISTRY)
    try:
        register_zone("raw", "my_module:main")
        assert "raw" in _ZONE_REGISTRY, "alias name is stored verbatim"
        assert "bronze" not in _ZONE_REGISTRY, "alias is NOT translated to bronze"
    finally:
        _ZONE_REGISTRY.clear()
        _ZONE_REGISTRY.update(original)


# ---------------------------------------------------------------------------
# Behavioral test: a manifest using medallion zone names executes correctly
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


def test_manifest_with_medallion_zone_name_executes(tmp_path):
    """A manifest using the medallion zone name 'bronze' executes the transform."""
    _seed_bronze_warehouse(tmp_path)

    # Noop transform that just returns success
    (tmp_path / "noop_transform.py").write_text(
        "def main():\n    return {'rows_promoted': 0, 'rows_skipped': 0}\n"
    )
    # Domain manifest uses the medallion zone name "bronze"
    (tmp_path / "domain").mkdir(parents=True, exist_ok=True)
    (tmp_path / "domain" / "manifest.yaml").write_text(
        "name: test\n"
        "version: '0.1'\n"
        "pipeline:\n"
        "  bronze:\n"
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
            "rule_id": "BRONZE-PASS",
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


def test_medallion_namespace_contract_matches_zone_verify(tmp_path):
    """_verify_contracts_for_zone matches contracts with medallion-prefixed tables."""
    from brightsmith.run import _verify_contracts_for_zone

    contracts_dir = tmp_path / "governance" / "data-contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "version": "1",
        "metadata": {"name": "bronze-seed-facts", "status": "active"},
        "schema": {"table": "bronze.seed_facts", "columns": []},
        "grain": {"fields": ["record_id"]},
        "lineage": {"sources": []},
    }
    (contracts_dir / "bronze-seed-facts.yaml").write_text(yaml.dump(contract))

    import brightsmith.config as cfg

    original_root = cfg.PROJECT_ROOT
    try:
        cfg.PROJECT_ROOT = tmp_path
        valid, violated = _verify_contracts_for_zone("bronze")
        # The bronze-prefixed contract is FOUND and attempted (valid + violated > 0).
        assert valid + violated == 1, (
            f"Expected 'bronze.seed_facts' contract to be found for the bronze "
            f"zone (valid + violated == 1); got valid={valid}, violated={violated}"
        )
    finally:
        cfg.PROJECT_ROOT = original_root
