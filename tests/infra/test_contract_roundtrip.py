"""Contract round-trip characterisation tests — WP-0.4.

These tests encode the INTENDED behaviour of the contract lifecycle:

    generate_contract → list_contracts → load_contract → verify_contract

ALL assertions that exercise the generate→read round-trip are currently RED
because ``save_contract()`` writes only to Iceberg (via ``sync_contract``) when
called without an explicit ``contracts_dir`` argument.  ``list_contracts()``,
``load_contract()``, and ``verify_contract()`` read YAML files from
``governance/data-contracts/``, which are never written by the default flow.

Root cause: ``contract.py:save_contract`` line 150 — the YAML write is guarded by
``if contracts_dir is not None and not path.is_relative_to(PROJECT_ROOT)``, so it
is skipped whenever ``contracts_dir`` is ``None`` (the normal call path from
``generate_contract``).

Audit finding: A2 (Critical) — "contract generate → contract verify yields
'No contracts found.'"
Spec: docs/specs/audit-remediation-open-source-readiness.md §WP-0.4
Fix: WP-1.3 — ``save_contract`` will write the YAML file unconditionally (dual-write).

Tests annotated ```` will go GREEN when WP-1.3 lands.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from pyiceberg.schema import Schema
from pyiceberg.types import DoubleType, NestedField, StringType

from brightsmith.infra.contract import (
    generate_contract,
    list_contracts,
    load_contract,
    verify_contract,
)
from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table

# ---------------------------------------------------------------------------
# Shared test schema and data
# ---------------------------------------------------------------------------

_TEST_SCHEMA = Schema(
    NestedField(1, "fact_id", StringType(), required=True),
    NestedField(2, "entity", StringType(), required=False),
    NestedField(3, "value", DoubleType(), required=False),
)

_TEST_ROWS = [
    {"fact_id": "R1", "entity": "acme", "value": 1.0},
    {"fact_id": "R2", "entity": "beta", "value": 2.0},
]

# Table name used by in-process tests.
# generate_contract derives the contract name as: tbl.replace("_", "-")
# "gold.round_trip_facts" → contract name "round-trip-facts"
_TABLE_NAME = "gold.round_trip_facts"
_CONTRACT_NAME = "round-trip-facts"


# ---------------------------------------------------------------------------
# Fixture: isolated project root with a seeded Iceberg table
# ---------------------------------------------------------------------------


@pytest.fixture()
def contract_env(tmp_path, monkeypatch):
    """Redirect all brightsmith.config paths to tmp_path and seed a test table.

    All infra functions use late imports (``from brightsmith.config import …``
    inside the function body), so monkeypatching the module attributes is the
    correct isolation technique for in-process tests.
    """
    import brightsmith.config as _cfg

    warehouse = tmp_path / "data" / "bronze" / "iceberg_warehouse"
    catalog_db = tmp_path / "data" / "catalog" / "catalog.db"
    gov_warehouse = tmp_path / "data" / "governance" / "iceberg_warehouse"

    monkeypatch.setattr(_cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_cfg, "WAREHOUSE_PATH", warehouse)
    monkeypatch.setattr(_cfg, "CATALOG_PATH", catalog_db)
    monkeypatch.setattr(_cfg, "GOVERNANCE_WAREHOUSE", gov_warehouse)

    # Seed the Iceberg table the contract will be generated from.
    catalog = get_catalog(warehouse, catalog_db)
    table = get_or_create_table(catalog, "gold", "round_trip_facts", _TEST_SCHEMA)
    append_data(table, _TEST_ROWS)

    return {
        "tmp_path": tmp_path,
        "warehouse": warehouse,
        "catalog_db": catalog_db,
        "table": table,
    }


# ---------------------------------------------------------------------------
# In-process round-trip tests
# ---------------------------------------------------------------------------


def test_generate_then_list_finds_contract(contract_env):
    """After generate_contract, list_contracts() must return the generated contract.

    Current behaviour: save_contract() calls sync_contract() (Iceberg write) but
    does NOT write a YAML file when contracts_dir is None.  list_contracts() scans
    governance/data-contracts/ for *.yaml files and returns [].

    Intended behaviour (WP-1.3): save_contract() writes the YAML file to
    governance/data-contracts/ unconditionally, making the contract visible to
    list_contracts() immediately after generation.
    """
    generate_contract(_TABLE_NAME)

    contracts = list_contracts()  # scans governance/data-contracts/*.yaml

    # INTENDED: the generated contract must appear in the list.
    assert len(contracts) >= 1, (
        "generate_contract() should write a YAML file so list_contracts() can find "
        f"it; got {contracts!r} — save_contract() only writes to Iceberg (A2)"
    )
    names = [c["name"] for c in contracts]
    assert _CONTRACT_NAME in names


def test_generate_then_load_returns_contract(contract_env):
    """After generate_contract, load_contract() must return the contract dict.

    Current behaviour: no YAML file is written → load_contract() returns None.
    Intended behaviour (WP-1.3): returns the full contract dict with correct
    metadata and schema reflecting the live Iceberg table.
    """
    generate_contract(_TABLE_NAME)

    loaded = load_contract(_CONTRACT_NAME)

    # INTENDED: load_contract returns the generated contract.
    assert loaded is not None, (
        f"load_contract('{_CONTRACT_NAME}') should return the generated contract; "
        "got None — no YAML file was written by save_contract() (A2)"
    )
    assert loaded["metadata"]["name"] == _CONTRACT_NAME
    assert loaded["schema"]["table"] == _TABLE_NAME
    # Schema should have been read from the real Iceberg table (3 columns)
    assert len(loaded["schema"]["columns"]) == 3


def test_generate_then_verify_passes(contract_env):
    """After generate_contract from a real table, verify_contract() must not FAIL.

    Current behaviour: verify_contract() calls load_contract() which returns None,
    so it immediately returns [ContractVerificationResult("load", "FAIL",
    "Contract 'round-trip-facts' not found")].

    Intended behaviour (WP-1.3): the contract is found, schema checks pass (the
    contract was generated from the live table so types must match), data checks
    run against the seeded rows.
    """
    generate_contract(_TABLE_NAME, grain_columns=["fact_id"])

    results = verify_contract(_CONTRACT_NAME)

    # The "load" check must not FAIL — that means the YAML file must exist.
    load_fail = next(
        (r for r in results if r.check == "load" and r.status == "FAIL"),
        None,
    )
    assert load_fail is None, (
        f"verify_contract should find the generated contract; got load FAIL: "
        f"{load_fail.detail if load_fail else ''}  "
        "— no YAML file was written by save_contract() (A2)"
    )

    # All checks must pass (no FAIL results at all).
    fail_results = [r for r in results if r.status == "FAIL"]
    assert not fail_results, (
        f"verify_contract should pass all checks after generate; "
        f"FAIL results: {[(r.check, r.detail) for r in fail_results]}"
    )


# ---------------------------------------------------------------------------
# CLI round-trip tests (subprocess level)
#
# The CLI is the layer that agents actually call, so subprocess tests are the
# most faithful encoding of the documented workflow.
#
# Env-var used to redirect the project root: BRIGHTSMITH_PROJECT_ROOT
# (brightsmith/config.py:_resolve_project_root, priority 1)
#
# CLI commands used:
#   python -m brightsmith.infra.contract generate --table <namespace.table>
#   python -m brightsmith.infra.contract verify --all
#   python -m brightsmith.infra.contract verify <contract-name>
#   python -m brightsmith.infra.contract list
# ---------------------------------------------------------------------------


def _run_cli(args: list[str], env: dict) -> subprocess.CompletedProcess:
    """Run ``python -m brightsmith.infra.contract <args>`` as a subprocess."""
    cmd = [sys.executable, "-m", "brightsmith.infra.contract"] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _seed_table(tmp_path, namespace: str, table: str) -> None:
    """Create a minimal Iceberg table at the paths a subprocess will expect.

    The subprocess inherits BRIGHTSMITH_PROJECT_ROOT=tmp_path, so config will
    derive WAREHOUSE_PATH = tmp_path/data/bronze/iceberg_warehouse and
    CATALOG_PATH = tmp_path/data/catalog/catalog.db.  We seed with the same
    paths so the subprocess's generate command can read the schema.
    """
    warehouse = tmp_path / "data" / "bronze" / "iceberg_warehouse"
    catalog_db = tmp_path / "data" / "catalog" / "catalog.db"
    catalog = get_catalog(warehouse, catalog_db)
    tbl = get_or_create_table(catalog, namespace, table, _TEST_SCHEMA)
    append_data(tbl, _TEST_ROWS)


def test_cli_generate_then_verify_all(tmp_path):
    """CLI: 'generate --table …' then 'verify --all' must exit 0 and report VALID.

    Current behaviour:
      1. 'generate' succeeds — it writes to Iceberg, no file.
      2. 'verify --all' calls list_contracts() which finds no YAML files,
         prints "No contracts found." and exits 1.

    Intended behaviour (WP-1.3):
      'verify --all' finds the contract written by 'generate' and exits 0.
    """
    _seed_table(tmp_path, "gold", "cli_facts")
    env = {**os.environ, "BRIGHTSMITH_PROJECT_ROOT": str(tmp_path)}

    # Step 1 — generate: must succeed on current code.
    gen = _run_cli(["generate", "--table", "gold.cli_facts"], env=env)
    assert gen.returncode == 0, (
        f"'contract generate' should succeed; stderr: {gen.stderr!r}"
    )
    assert "Generated contract:" in gen.stdout

    # Step 2 — verify --all: finds the contract written by generate.
    verify = _run_cli(["verify", "--all"], env=env)

    assert verify.returncode == 0, (
        "'verify --all' should exit 0 after generate but currently exits 1 — "
        f"stdout: {verify.stdout!r}; stderr: {verify.stderr!r}\n"
        "BUG A2: save_contract writes Iceberg only; verify --all reads YAML files "
        "and prints 'No contracts found.'"
    )
    assert "No contracts found" not in verify.stdout
    assert "VALID" in verify.stdout


def test_cli_generate_then_verify_named(tmp_path):
    """CLI: 'generate' then 'verify <name>' must exit 0.

    The named-verify path bypasses list_contracts() but still calls
    load_contract() which reads from the YAML file — same failure.

    Contract name: gold.named_facts → tbl.replace('_', '-') → 'named-facts'
    """
    _seed_table(tmp_path, "gold", "named_facts")
    env = {**os.environ, "BRIGHTSMITH_PROJECT_ROOT": str(tmp_path)}

    gen = _run_cli(["generate", "--table", "gold.named_facts"], env=env)
    assert gen.returncode == 0, (
        f"'contract generate' should succeed; stderr: {gen.stderr!r}"
    )

    verify = _run_cli(["verify", "named-facts"], env=env)

    assert verify.returncode == 0, (
        "'verify named-facts' should exit 0 after generate but currently exits 1 — "
        f"stdout: {verify.stdout!r}; stderr: {verify.stderr!r}\n"
        "BUG A2: load_contract('named-facts') returns None because no YAML was written"
    )
    assert "not found" not in verify.stdout.lower()


def test_cli_list_after_generate(tmp_path):
    """CLI: 'list' after 'generate' must show the generated contract.

    Current behaviour: list_contracts() finds no YAML files and prints
    "No contracts found."
    Intended (WP-1.3): the generated contract name appears in 'list' output.

    Contract name: gold.listed_facts → 'listed-facts'
    """
    _seed_table(tmp_path, "gold", "listed_facts")
    env = {**os.environ, "BRIGHTSMITH_PROJECT_ROOT": str(tmp_path)}

    gen = _run_cli(["generate", "--table", "gold.listed_facts"], env=env)
    assert gen.returncode == 0, (
        f"'contract generate' should succeed; stderr: {gen.stderr!r}"
    )

    lst = _run_cli(["list"], env=env)

    assert "listed-facts" in lst.stdout, (
        "'list' should show 'listed-facts' after generate but currently shows: "
        f"{lst.stdout!r}\n"
        "BUG A2: list reads YAML files from governance/data-contracts/ "
        "which were never written by save_contract()"
    )
