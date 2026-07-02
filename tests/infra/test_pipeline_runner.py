"""Tests for headless pipeline runner."""

import json
import os
import subprocess
import sys
from pathlib import Path

from brightsmith.run import (
    EXIT_DQ_FAILURE,
    EXIT_SUCCESS,
    EXIT_TRANSFORM_ERROR,
    GoldenResult,
    PipelineResult,
    ZoneResult,
    previous_zone,
    run_pipeline,
)

ROOT_ENV_VAR = "BRIGHTSMITH_PROJECT_ROOT"


def test_pipeline_result_to_dict():
    """PipelineResult should serialize to a valid JSON-compatible dict."""
    result = PipelineResult()
    result.add_zone_result("bronze", ZoneResult(zone="bronze", status="SUCCESS", rows_promoted=100))
    result.finalize()
    d = result.to_dict()
    assert d["run_id"]
    assert d["status"] == "SUCCESS"
    assert d["zones"]["bronze"]["rows_promoted"] == 100
    # Round-trip through JSON
    assert json.loads(json.dumps(d)) == d


def test_zone_result_defaults():
    """ZoneResult should have sensible defaults."""
    zr = ZoneResult(zone="bronze")
    assert zr.status == "PENDING"
    assert zr.dq_p0_passed is True
    assert zr.rows_promoted == 0


def test_previous_zone():
    """previous_zone should return the zone before the given one."""
    assert previous_zone("silver") == "bronze"
    assert previous_zone("gold") == "silver"
    assert previous_zone("bronze") is None


def test_pipeline_result_finalize_success():
    """Finalize should set SUCCESS when all zones pass."""
    result = PipelineResult()
    result.add_zone_result("bronze", ZoneResult(zone="bronze", status="SUCCESS"))
    result.add_zone_result("silver", ZoneResult(zone="silver", status="SUCCESS"))
    result.finalize()
    assert result.status == "SUCCESS"
    assert result.exit_code == EXIT_SUCCESS


def test_pipeline_result_finalize_dq_failure():
    """Finalize should detect DQ failures."""
    result = PipelineResult()
    result.add_zone_result("bronze", ZoneResult(
        zone="bronze", status="FAILED", dq_p0_passed=False,
        dq_p0_failures=["rule-1"],
    ))
    result.finalize()
    assert result.status == "DQ_FAILURE"
    assert result.exit_code == 1


def test_pipeline_result_finalize_contract_warning():
    """Contract violations should produce SUCCESS_WITH_WARNINGS."""
    result = PipelineResult()
    result.add_zone_result("bronze", ZoneResult(
        zone="bronze", status="SUCCESS", contracts_violated=1,
    ))
    result.finalize()
    assert result.status == "SUCCESS_WITH_WARNINGS"
    assert result.exit_code == 3


def test_dry_run_skips_execution():
    """Dry run should not execute any zones."""
    result = run_pipeline(zones=["bronze"], dry_run=True)
    assert result.status == "DRY_RUN"
    assert result.zones["bronze"].status == "SKIPPED"


def test_validate_only_with_no_zones():
    """Validate-only with explicit empty zones should still work."""
    result = run_pipeline(zones=[], validate_only=True)
    result.finalize()
    assert result.exit_code == EXIT_SUCCESS


def test_json_output_format_valid():
    """to_dict() output should be valid JSON."""
    result = PipelineResult()
    result.golden_datasets = GoldenResult(checked=5, passed=4, failed=1, pass_rate=80.0)
    result.finalize()
    d = result.to_dict()
    assert d["golden_datasets"]["checked"] == 5
    assert d["golden_datasets"]["pass_rate"] == 80.0


def test_exit_code_0_on_success():
    """Successful pipeline should exit 0."""
    result = PipelineResult()
    result.add_zone_result("bronze", ZoneResult(zone="bronze", status="SUCCESS"))
    result.finalize()
    assert result.exit_code == 0


def test_golden_result_defaults():
    """GoldenResult should have sensible defaults."""
    gr = GoldenResult()
    assert gr.checked == 0
    assert gr.pass_rate == 0.0


# ---------------------------------------------------------------------------
# WP-1.2 — Real headless DQ gate (audit Q1, decision D3).
#
# The DQ gate must EXECUTE rules against the real warehouse, not count every
# rule as passed. These behavioral tests exercise the actual CLI in a FRESH
# process rooted at a tmp dir via BRIGHTSMITH_PROJECT_ROOT, so every config-
# derived path (warehouse, catalog, dq-rules, AND the governance warehouse
# that run_rules writes to as a side effect) resolves under tmp. Without this
# isolation, concurrent governance writes to the shared data/ warehouse hit
# `CommitFailedException: branch main has changed`.
# ---------------------------------------------------------------------------


def _seed_bronze_warehouse(tmp_path: Path) -> None:
    """Seed a populated bronze.seed_facts Iceberg table at config-derived paths."""
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


def _write_rules(tmp_path: Path, rules: list[dict], tables: list[str]) -> None:
    """Write a DQ rules JSON file into the tmp project's governance dir."""
    rules_dir = tmp_path / "governance" / "dq-rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "test-bronze.json").write_text(
        json.dumps({"spec": "test-bronze", "tables": tables, "rules": rules}, indent=2)
    )


def _write_noop_manifest(tmp_path: Path) -> None:
    """Register a side-effect-free bronze transform so `--zone bronze` executes the DQ gate."""
    (tmp_path / "noop_transform.py").write_text(
        "def main():\n    return {'rows_promoted': 0, 'rows_skipped': 0}\n"
    )
    (tmp_path / "domain").mkdir(parents=True, exist_ok=True)
    (tmp_path / "domain" / "manifest.yaml").write_text(
        "name: test\n"
        "version: '0.1'\n"
        "pipeline:\n"
        "  bronze:\n"
        "    module: noop_transform\n"
        "    function: main\n"
    )


def _run_runner(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke `python -m brightsmith.run` in a fresh process rooted at tmp_path."""
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


def test_failing_p0_rule_exits_dq_failure_and_names_rule(tmp_path):
    """A failing P0 rule must fail the gate (exit EXIT_DQ_FAILURE) and name the rule."""
    _seed_bronze_warehouse(tmp_path)
    _write_noop_manifest(tmp_path)
    _write_rules(
        tmp_path,
        [{
            "rule_id": "BRZ-FAIL",
            "priority": "P0",
            "status": "active",
            "category": "completeness",
            "description": "no rows with val=a",
            # seed has one row with val='a' -> COUNT=1, threshold wants 0 -> FAIL
            "sql": "SELECT COUNT(*) FROM bronze.seed_facts WHERE val = 'a'",
            "threshold": "result = 0",
        }],
        tables=["bronze.seed_facts"],
    )

    proc = _run_runner(tmp_path, "--zone", "bronze")
    assert proc.returncode == EXIT_DQ_FAILURE, proc.stdout + proc.stderr
    assert "BRZ-FAIL" in proc.stdout, proc.stdout


def test_erroring_p0_rule_exits_nonzero(tmp_path):
    """A P0 rule that cannot execute (missing table) is a failure, not a pass (decision D3)."""
    _seed_bronze_warehouse(tmp_path)
    _write_noop_manifest(tmp_path)
    _write_rules(
        tmp_path,
        [{
            "rule_id": "BRZ-ERR",
            "priority": "P0",
            "status": "active",
            "category": "completeness",
            "description": "references a table that does not exist",
            "sql": "SELECT COUNT(*) FROM bronze.does_not_exist",
            "threshold": "result = 0",
        }],
        tables=["bronze.does_not_exist"],
    )

    proc = _run_runner(tmp_path, "--zone", "bronze")
    assert proc.returncode != EXIT_SUCCESS, proc.stdout + proc.stderr
    assert "BRZ-ERR" in proc.stdout, proc.stdout


def test_all_passing_rules_exit_success(tmp_path):
    """When every rule passes against real data, the pipeline exits 0."""
    _seed_bronze_warehouse(tmp_path)
    _write_noop_manifest(tmp_path)
    _write_rules(
        tmp_path,
        [{
            "rule_id": "BRZ-PASS",
            "priority": "P0",
            "status": "active",
            "category": "completeness",
            "description": "no rows with the impossible value",
            "sql": "SELECT COUNT(*) FROM bronze.seed_facts WHERE val = 'zzz'",
            "threshold": "result = 0",
        }],
        tables=["bronze.seed_facts"],
    )

    proc = _run_runner(tmp_path, "--zone", "bronze")
    assert proc.returncode == EXIT_SUCCESS, proc.stdout + proc.stderr
    assert "DQ:   1 passed" in proc.stdout, proc.stdout


def test_alias_namespace_rule_hits_canonical_zone(tmp_path):
    """A rule declared against `raw.` must execute against the canonical bronze table."""
    _seed_bronze_warehouse(tmp_path)
    _write_noop_manifest(tmp_path)
    _write_rules(
        tmp_path,
        [{
            "rule_id": "RAW-FAIL",
            "priority": "P0",
            "status": "active",
            "category": "completeness",
            # raw.seed_facts resolves to bronze.seed_facts via ZONE_ALIASES
            "sql": "SELECT COUNT(*) FROM raw.seed_facts WHERE val = 'a'",
            "threshold": "result = 0",
        }],
        tables=["raw.seed_facts"],
    )

    proc = _run_runner(tmp_path, "--zone", "bronze")
    assert proc.returncode == EXIT_DQ_FAILURE, proc.stdout + proc.stderr
    assert "RAW-FAIL" in proc.stdout, proc.stdout


def test_validate_only_runs_real_dq(tmp_path):
    """--validate-only skips the transform but must still execute the real DQ gate."""
    _seed_bronze_warehouse(tmp_path)
    # No manifest/transform needed: validate-only never executes the zone module.
    _write_rules(
        tmp_path,
        [{
            "rule_id": "VO-FAIL",
            "priority": "P0",
            "status": "active",
            "category": "completeness",
            "sql": "SELECT COUNT(*) FROM bronze.seed_facts WHERE val = 'a'",
            "threshold": "result = 0",
        }],
        tables=["bronze.seed_facts"],
    )

    proc = _run_runner(tmp_path, "--zone", "bronze", "--validate-only")
    assert proc.returncode == EXIT_DQ_FAILURE, proc.stdout + proc.stderr
    assert "VO-FAIL" in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# H2 — SKIPPED misclassification (docs/technical-audit-2026-07-02.md).
#
# `_execute_zone_module` used to raise bare `ValueError` for "no module
# registered", and `run_pipeline` caught `ValueError` to detect that case —
# which also caught any `ValueError` raised by the domain transform itself,
# silently reclassifying real failures as SKIPPED with exit 0. Fixed by
# raising a dedicated `ZoneNotRegisteredError` and narrowing the catch.
# ---------------------------------------------------------------------------


def _write_failing_transform_manifest(tmp_path: Path) -> None:
    """Register a bronze transform whose main() raises ValueError, simulating
    a real domain failure (e.g. append_data's strict-mode column error)."""
    (tmp_path / "failing_transform.py").write_text(
        "def main():\n"
        "    raise ValueError('simulated strict-mode misspelled-column error')\n"
    )
    (tmp_path / "domain").mkdir(parents=True, exist_ok=True)
    (tmp_path / "domain" / "manifest.yaml").write_text(
        "name: test\n"
        "version: '0.1'\n"
        "pipeline:\n"
        "  bronze:\n"
        "    module: failing_transform\n"
        "    function: main\n"
    )


def test_registered_zone_value_error_yields_failed_not_skipped(tmp_path):
    """A registered zone whose transform raises ValueError must FAIL loudly
    (exit EXIT_TRANSFORM_ERROR), never be silently reclassified as SKIPPED.
    """
    _write_failing_transform_manifest(tmp_path)

    proc = _run_runner(tmp_path, "--zone", "bronze")
    assert proc.returncode == EXIT_TRANSFORM_ERROR, proc.stdout + proc.stderr
    assert "SKIPPED" not in proc.stdout, proc.stdout
    assert "simulated strict-mode misspelled-column error" in proc.stdout, proc.stdout


def test_unregistered_zone_still_skipped(tmp_path):
    """A zone with no module registered at all must still be SKIPPED (not
    FAILED) and must not fail the run.
    """
    _write_noop_manifest(tmp_path)  # registers only "bronze"

    proc = _run_runner(tmp_path, "--zone", "silver")
    assert proc.returncode == EXIT_SUCCESS, proc.stdout + proc.stderr
    assert "SKIPPED" in proc.stdout, proc.stdout


def test_registered_zone_value_error_yields_failed_in_process(tmp_path):
    """In-process equivalent of the subprocess test above: exercises
    run_pipeline() directly against a monkeypatched zone registry so the
    FAILED/TRANSFORM_ERROR outcome is asserted on the PipelineResult object,
    not just process exit code / stdout text.
    """
    from unittest.mock import patch

    def _raise_value_error() -> dict:
        raise ValueError("simulated strict-mode misspelled-column error")

    with (
        patch("brightsmith.run._ZONE_REGISTRY", {"bronze": "dummy:main"}),
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._execute_zone_module", side_effect=lambda zone: _raise_value_error()),
        patch("brightsmith.run._preflight_relocation"),
    ):
        result = run_pipeline(zones=["bronze"])

    assert result.zones["bronze"].status == "FAILED"
    assert result.status == "TRANSFORM_ERROR"
    assert result.exit_code == EXIT_TRANSFORM_ERROR


def test_unregistered_zone_still_skipped_in_process():
    """In-process equivalent: an unregistered zone must yield SKIPPED via
    run_pipeline(), not FAILED.
    """
    from unittest.mock import patch

    with (
        patch("brightsmith.run._ZONE_REGISTRY", {}),
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._preflight_relocation"),
    ):
        result = run_pipeline(zones=["silver"])

    assert result.zones["silver"].status == "SKIPPED"
    assert result.exit_code == EXIT_SUCCESS
