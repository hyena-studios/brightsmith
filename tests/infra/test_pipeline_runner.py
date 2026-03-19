"""Tests for headless pipeline runner."""

import json

from grist.run import (
    EXIT_CONFIG_ERROR,
    EXIT_SUCCESS,
    GoldenResult,
    PipelineResult,
    ZoneResult,
    previous_zone,
    register_zone,
    run_pipeline,
)


def test_pipeline_result_to_dict():
    """PipelineResult should serialize to a valid JSON-compatible dict."""
    result = PipelineResult()
    result.add_zone_result("raw", ZoneResult(zone="raw", status="SUCCESS", rows_promoted=100))
    result.finalize()
    d = result.to_dict()
    assert d["run_id"]
    assert d["status"] == "SUCCESS"
    assert d["zones"]["raw"]["rows_promoted"] == 100
    # Round-trip through JSON
    assert json.loads(json.dumps(d)) == d


def test_zone_result_defaults():
    """ZoneResult should have sensible defaults."""
    zr = ZoneResult(zone="raw")
    assert zr.status == "PENDING"
    assert zr.dq_p0_passed is True
    assert zr.rows_promoted == 0


def test_previous_zone():
    """previous_zone should return the zone before the given one."""
    assert previous_zone("base") == "raw"
    assert previous_zone("consumable") == "base"
    assert previous_zone("raw") is None


def test_pipeline_result_finalize_success():
    """Finalize should set SUCCESS when all zones pass."""
    result = PipelineResult()
    result.add_zone_result("raw", ZoneResult(zone="raw", status="SUCCESS"))
    result.add_zone_result("base", ZoneResult(zone="base", status="SUCCESS"))
    result.finalize()
    assert result.status == "SUCCESS"
    assert result.exit_code == EXIT_SUCCESS


def test_pipeline_result_finalize_dq_failure():
    """Finalize should detect DQ failures."""
    result = PipelineResult()
    result.add_zone_result("raw", ZoneResult(
        zone="raw", status="FAILED", dq_p0_passed=False,
        dq_p0_failures=["rule-1"],
    ))
    result.finalize()
    assert result.status == "DQ_FAILURE"
    assert result.exit_code == 1


def test_pipeline_result_finalize_contract_warning():
    """Contract violations should produce SUCCESS_WITH_WARNINGS."""
    result = PipelineResult()
    result.add_zone_result("raw", ZoneResult(
        zone="raw", status="SUCCESS", contracts_violated=1,
    ))
    result.finalize()
    assert result.status == "SUCCESS_WITH_WARNINGS"
    assert result.exit_code == 3


def test_dry_run_skips_execution():
    """Dry run should not execute any zones."""
    result = run_pipeline(zones=["raw"], dry_run=True)
    assert result.status == "DRY_RUN"
    assert result.zones["raw"].status == "SKIPPED"


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
    result.add_zone_result("raw", ZoneResult(zone="raw", status="SUCCESS"))
    result.finalize()
    assert result.exit_code == 0


def test_golden_result_defaults():
    """GoldenResult should have sensible defaults."""
    gr = GoldenResult()
    assert gr.checked == 0
    assert gr.pass_rate == 0.0
