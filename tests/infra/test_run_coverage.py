"""In-process coverage tests for run.py's manifest parsing, orchestration,
readiness, history, and CLI surfaces (W1 — docs/technical-audit-2026-07-02.md,
finding H3, which named run.py at 44% alongside pipeline_gate.py's 25%).

tests/infra/test_pipeline_runner.py already characterizes DQ-gate and H2
behavior end-to-end (real Iceberg data, some via subprocess). These tests
fill in the manifest-shape parsing (H5.2 internals), preflight-relocation,
contract/DQ/golden helper branches, headless-readiness diagnostics, run
history, and CLI/`_print_summary` paths that were still dark — mostly via
direct calls + mocks/monkeypatch rather than full pipeline runs, so they stay
fast.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import brightsmith.run as run_mod
from brightsmith.run import (
    EXIT_CONFIG_ERROR,
    EXIT_SUCCESS,
    GoldenResult,
    PipelineManifestShapeError,
    PipelineResult,
    ZoneNotRegisteredError,
    ZoneResult,
    _check_contracts_for_zone,
    _contract_matches_zone,
    _execute_zone_module,
    _import_step_module,
    _is_file_path_module,
    _load_flat_zone_registry,
    _load_nested_zone_registry,
    _parse_step_string,
    _preflight_relocation,
    _rule_matches_zone,
    _run_dq_for_zone,
    _verify_contracts_for_zone,
    _verify_golden_datasets,
    _ZoneStep,
    check_headless_ready,
    register_zone,
    run_pipeline,
)


@pytest.fixture(autouse=True)
def _clear_zone_registry():
    """_ZONE_REGISTRY is process-global state; isolate every test."""
    saved = dict(run_mod._ZONE_REGISTRY)
    run_mod._ZONE_REGISTRY.clear()
    yield
    run_mod._ZONE_REGISTRY.clear()
    run_mod._ZONE_REGISTRY.update(saved)


@pytest.fixture
def restore_config():
    import brightsmith.config as cfg

    saved = cfg.get_config()
    yield cfg
    cfg.configure(
        project_root=saved.project_root,
        project_name=saved.project_name,
        require_human_approval=saved.require_human_approval,
    )


# ---------------------------------------------------------------------------
# PipelineResult.finalize — the FAILED-without-error branch
# ---------------------------------------------------------------------------


def test_finalize_failed_zone_without_error_or_dq_reason():
    """A FAILED zone with dq_p0_passed True and no error string still yields
    a generic FAILED result (not silently swallowed into SUCCESS)."""
    result = PipelineResult()
    result.add_zone_result("bronze", ZoneResult(zone="bronze", status="FAILED"))
    result.finalize()
    assert result.status == "FAILED"
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Zone registration — legacy string form + alias normalization
# ---------------------------------------------------------------------------


def test_parse_step_string_with_and_without_function():
    assert _parse_step_string("pkg.mod:run") == _ZoneStep(module="pkg.mod", function="run", file_path=False)
    assert _parse_step_string("pkg.mod") == _ZoneStep(module="pkg.mod", function="main", file_path=False)


def test_register_zone_normalizes_alias():
    register_zone("raw", "pkg.mod:main")
    assert "bronze" in run_mod._ZONE_REGISTRY
    assert run_mod._ZONE_REGISTRY["bronze"][0].module == "pkg.mod"


def test_is_file_path_module():
    assert _is_file_path_module("src/silver/foo.py") is True
    assert _is_file_path_module("silver.foo") is False


# ---------------------------------------------------------------------------
# _load_flat_zone_registry
# ---------------------------------------------------------------------------


def test_load_flat_zone_registry_skips_non_dict_and_moduleless_entries():
    from brightsmith.infra.governance.serializers import normalize_zone

    pipeline = {
        "bronze": "not-a-dict",  # skipped: not isinstance dict
        "silver": {},  # skipped: no module key
        "gold": {"module": "pkg.gold", "function": "run"},
    }
    _load_flat_zone_registry(pipeline, normalize_zone)
    assert "bronze" not in run_mod._ZONE_REGISTRY
    assert "silver" not in run_mod._ZONE_REGISTRY
    assert run_mod._ZONE_REGISTRY["gold"][0].function == "run"


# ---------------------------------------------------------------------------
# _load_nested_zone_registry
# ---------------------------------------------------------------------------


def test_load_nested_zone_registry_rejects_non_dict_zones_block():
    from brightsmith.infra.governance.serializers import normalize_zone

    with pytest.raises(PipelineManifestShapeError, match="pipeline.zones"):
        _load_nested_zone_registry(["not", "a", "dict"], normalize_zone)


def test_load_nested_zone_registry_rejects_non_list_non_dict_steps():
    from brightsmith.infra.governance.serializers import normalize_zone

    with pytest.raises(PipelineManifestShapeError, match="must be a list"):
        _load_nested_zone_registry({"bronze": "not-a-list"}, normalize_zone)


def test_load_nested_zone_registry_rejects_non_dict_step():
    from brightsmith.infra.governance.serializers import normalize_zone

    with pytest.raises(PipelineManifestShapeError, match="must be"):
        _load_nested_zone_registry({"bronze": ["not-a-dict-step"]}, normalize_zone)


def test_load_nested_zone_registry_skips_mcp_class_declaration():
    """A step with 'class' and no 'function' is an MCP server registration,
    not a callable transform — skipped, not registered."""
    from brightsmith.infra.governance.serializers import normalize_zone

    _load_nested_zone_registry({"mcp": [{"class": "MyServer"}]}, normalize_zone)
    assert "mcp" not in run_mod._ZONE_REGISTRY


def test_load_nested_zone_registry_accepts_single_dict_per_zone():
    """A single step dict (not wrapped in a list) is accepted too."""
    from brightsmith.infra.governance.serializers import normalize_zone

    _load_nested_zone_registry({"bronze": {"module": "pkg.b", "function": "run"}}, normalize_zone)
    assert run_mod._ZONE_REGISTRY["bronze"][0].module == "pkg.b"


def test_load_nested_zone_registry_aggregates_ordered_steps():
    from brightsmith.infra.governance.serializers import normalize_zone

    _load_nested_zone_registry(
        {"silver": [{"module": "pkg.s1"}, {"module": "pkg.s2", "function": "run"}]},
        normalize_zone,
    )
    steps = run_mod._ZONE_REGISTRY["silver"]
    assert [s.module for s in steps] == ["pkg.s1", "pkg.s2"]
    assert steps[1].function == "run"


# ---------------------------------------------------------------------------
# _load_zone_registry — top-level shape dispatch
# ---------------------------------------------------------------------------


def test_load_zone_registry_noop_when_already_registered():
    run_mod._ZONE_REGISTRY["bronze"] = [_ZoneStep(module="already", function="main")]
    with patch("brightsmith.domain_loader.load_manifest") as m:
        run_mod._load_zone_registry()
        m.assert_not_called()


def test_load_zone_registry_no_manifest_leaves_registry_empty():
    with patch("brightsmith.domain_loader.load_manifest", side_effect=FileNotFoundError):
        run_mod._load_zone_registry()
    assert run_mod._ZONE_REGISTRY == {}


def test_load_zone_registry_no_pipeline_section_leaves_registry_empty():
    manifest = type("M", (), {"pipeline": None})()
    with patch("brightsmith.domain_loader.load_manifest", return_value=manifest):
        run_mod._load_zone_registry()
    assert run_mod._ZONE_REGISTRY == {}


def test_load_zone_registry_pipeline_not_a_mapping_raises():
    manifest = type("M", (), {"pipeline": ["not", "a", "dict"]})()
    with patch("brightsmith.domain_loader.load_manifest", return_value=manifest):
        with pytest.raises(PipelineManifestShapeError, match="must be a mapping"):
            run_mod._load_zone_registry()


def test_load_zone_registry_unrecognized_shape_raises():
    """A pipeline dict whose values are a mix of dict/non-dict, with no
    'zones' key, matches neither the flat nor nested shape."""
    manifest = type("M", (), {"pipeline": {"bronze": "a string, not a step dict"}})()
    with patch("brightsmith.domain_loader.load_manifest", return_value=manifest):
        with pytest.raises(PipelineManifestShapeError, match="did not match a recognized shape"):
            run_mod._load_zone_registry()


def test_load_zone_registry_dispatches_nested_shape():
    manifest = type("M", (), {"pipeline": {"zones": {"bronze": [{"module": "pkg.b"}]}}})()
    with patch("brightsmith.domain_loader.load_manifest", return_value=manifest):
        run_mod._load_zone_registry()
    assert run_mod._ZONE_REGISTRY["bronze"][0].module == "pkg.b"


def test_load_zone_registry_dispatches_flat_shape():
    manifest = type("M", (), {"pipeline": {"bronze": {"module": "pkg.b", "function": "main"}}})()
    with patch("brightsmith.domain_loader.load_manifest", return_value=manifest):
        run_mod._load_zone_registry()
    assert run_mod._ZONE_REGISTRY["bronze"][0].module == "pkg.b"


# ---------------------------------------------------------------------------
# _import_step_module / _execute_zone_module
# ---------------------------------------------------------------------------


def test_import_step_module_missing_file_raises_import_error(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    step = _ZoneStep(module="does/not/exist.py", function="main", file_path=True)
    with pytest.raises(ImportError, match="not found"):
        _import_step_module(step)


def test_import_step_module_loads_relative_file(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    (tmp_path / "step_mod.py").write_text("VALUE = 42\n")
    mod = _import_step_module(_ZoneStep(module="step_mod.py", function="main", file_path=True))
    assert mod.VALUE == 42


def test_execute_zone_module_raises_when_unregistered():
    with pytest.raises(ZoneNotRegisteredError, match="silver"):
        _execute_zone_module("silver")


def test_execute_zone_module_aggregates_multiple_steps(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    (tmp_path / "step_a.py").write_text(
        "def main():\n    return {'rows_promoted': 3, 'rows_skipped': 1}\n"
    )
    (tmp_path / "step_b.py").write_text(
        "def main():\n    return {'rows_promoted': 5, 'rows_skipped': 0}\n"
    )
    run_mod._ZONE_REGISTRY["bronze"] = [
        _ZoneStep(module="step_a.py", function="main", file_path=True),
        _ZoneStep(module="step_b.py", function="main", file_path=True),
    ]
    result = _execute_zone_module("bronze")
    assert result == {"rows_promoted": 8, "rows_skipped": 1}


# ---------------------------------------------------------------------------
# _preflight_relocation
# ---------------------------------------------------------------------------


def test_preflight_relocation_noop_when_catalog_missing(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    _preflight_relocation(["bronze"])  # no catalog file yet — must not raise


def test_preflight_relocation_noop_on_empty_zones():
    _preflight_relocation([])  # returns immediately, no config/catalog touched


def test_preflight_relocation_propagates_relocation_error(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
    from brightsmith.infra.iceberg_setup import WarehouseRelocationError, get_catalog, get_or_create_table

    catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
    schema = Schema(NestedField(1, "record_id", StringType(), required=False))
    get_or_create_table(catalog, "bronze", "t1", schema)

    with patch.object(catalog, "load_table", side_effect=WarehouseRelocationError("moved")):
        with patch("brightsmith.infra.iceberg_setup.get_catalog", return_value=catalog):
            with pytest.raises(WarehouseRelocationError):
                _preflight_relocation(["bronze"])


def test_preflight_relocation_swallows_non_relocation_errors(tmp_path, restore_config):
    """A table-load error that ISN'T a relocation is deferred to per-zone
    execution — the preflight must not raise (allowlisted broad except)."""
    restore_config.configure(project_root=tmp_path)
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
    from brightsmith.infra.iceberg_setup import get_catalog, get_or_create_table

    catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
    schema = Schema(NestedField(1, "record_id", StringType(), required=False))
    get_or_create_table(catalog, "bronze", "t1", schema)

    with patch.object(catalog, "load_table", side_effect=RuntimeError("transient")):
        with patch("brightsmith.infra.iceberg_setup.get_catalog", return_value=catalog):
            _preflight_relocation(["bronze"])  # must not raise


# ---------------------------------------------------------------------------
# run_pipeline — orchestration branches
# ---------------------------------------------------------------------------


def test_run_pipeline_no_zones_and_not_validate_only_is_config_error():
    with patch("brightsmith.run._load_zone_registry"):
        result = run_pipeline(zones=[])
    assert result.status == "CONFIG_ERROR"
    assert result.exit_code == EXIT_CONFIG_ERROR


def test_run_pipeline_relocation_error_yields_config_error(capsys):
    from brightsmith.infra.iceberg_setup import WarehouseRelocationError

    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._preflight_relocation", side_effect=WarehouseRelocationError("moved!")),
    ):
        result = run_pipeline(zones=["bronze"])
    assert result.status == "CONFIG_ERROR"
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "moved!" in capsys.readouterr().err


def test_run_pipeline_source_contract_failure_fails_zone_before_execution():
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._preflight_relocation"),
        patch("brightsmith.run._check_contracts_for_zone", return_value=False),
        patch("brightsmith.run._execute_zone_module") as exec_mock,
    ):
        result = run_pipeline(zones=["silver"])
    assert result.zones["silver"].status == "FAILED"
    assert "contracts failed verification" in result.zones["silver"].error
    exec_mock.assert_not_called()


def test_run_pipeline_dq_execution_exception_yields_failed_zone():
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._preflight_relocation"),
        patch("brightsmith.run._execute_zone_module", return_value={"rows_promoted": 1, "rows_skipped": 0}),
        patch("brightsmith.run._run_dq_for_zone", side_effect=RuntimeError("dq engine down")),
    ):
        result = run_pipeline(zones=["bronze"])
    assert result.zones["bronze"].status == "FAILED"
    assert "DQ execution failed" in result.zones["bronze"].error
    assert "dq engine down" in result.zones["bronze"].error


def test_run_pipeline_contract_violation_after_success_is_a_warning():
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._preflight_relocation"),
        patch("brightsmith.run._execute_zone_module", return_value={"rows_promoted": 1, "rows_skipped": 0}),
        patch("brightsmith.run._run_dq_for_zone", return_value=(True, 1, 0, [])),
        patch("brightsmith.run._verify_contracts_for_zone", return_value=(0, 1)),
        patch("brightsmith.run._verify_golden_datasets", return_value=GoldenResult()),
    ):
        result = run_pipeline(zones=["bronze"])
    assert result.zones["bronze"].status == "SUCCESS"
    assert result.zones["bronze"].contracts_violated == 1
    assert any("contract" in w for w in result.zones["bronze"].warnings)
    assert result.status == "SUCCESS_WITH_WARNINGS"


def test_run_pipeline_dq_p0_failure_fails_zone_and_stops():
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._preflight_relocation"),
        patch("brightsmith.run._execute_zone_module", return_value={"rows_promoted": 1, "rows_skipped": 0}),
        patch("brightsmith.run._run_dq_for_zone", return_value=(False, 0, 1, ["RULE-X"])),
        patch("brightsmith.run._verify_contracts_for_zone") as verify_mock,
    ):
        result = run_pipeline(zones=["bronze"])
    assert result.zones["bronze"].status == "FAILED"
    assert "RULE-X" in result.zones["bronze"].error
    assert result.status == "DQ_FAILURE"
    verify_mock.assert_not_called()  # gate stops before contract verification


def test_run_pipeline_defaults_zones_from_registry():
    """zones=None (the CLI default) derives the zone list from _ZONE_REGISTRY
    in canonical ZONE_ORDER, not just whatever order the manifest declared."""
    run_mod._ZONE_REGISTRY["gold"] = [_ZoneStep(module="pkg.gold", function="main")]
    run_mod._ZONE_REGISTRY["bronze"] = [_ZoneStep(module="pkg.bronze", function="main")]
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._preflight_relocation"),
        patch("brightsmith.run._execute_zone_module", return_value={"rows_promoted": 0, "rows_skipped": 0}),
        patch("brightsmith.run._run_dq_for_zone", return_value=(True, 0, 0, [])),
        patch("brightsmith.run._verify_contracts_for_zone", return_value=(0, 0)),
        patch("brightsmith.run._verify_golden_datasets", return_value=GoldenResult()),
    ):
        result = run_pipeline()
    assert list(result.zones.keys()) == ["bronze", "gold"]


def test_run_pipeline_validate_only_skips_execution_but_runs_dq():
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.run._preflight_relocation"),
        patch("brightsmith.run._execute_zone_module") as exec_mock,
        patch("brightsmith.run._run_dq_for_zone", return_value=(True, 0, 0, [])),
        patch("brightsmith.run._verify_contracts_for_zone", return_value=(0, 0)),
        patch("brightsmith.run._verify_golden_datasets", return_value=GoldenResult()),
    ):
        result = run_pipeline(zones=["bronze"], validate_only=True)
    exec_mock.assert_not_called()
    assert result.zones["bronze"].status == "SUCCESS"


# ---------------------------------------------------------------------------
# _run_dq_for_zone
# ---------------------------------------------------------------------------


def test_run_dq_for_zone_no_matching_rules_short_circuits():
    with patch("brightsmith.infra.dq_runner.load_rules", return_value=[]):
        ok, passed, failed, p0 = _run_dq_for_zone("bronze")
    assert (ok, passed, failed, p0) == (True, 0, 0, [])


def test_run_dq_for_zone_partitions_by_zone_and_flags_p0_failures():
    """_run_dq_for_zone passes a zone-scoped rule_filter to run_rules() (W4)
    instead of running every rule and discarding non-matching results after
    the fact — the fake run_rules below mimics dq_runner.run_rules' own
    rule_filter application so this test exercises the actual contract
    _run_dq_for_zone now relies on."""
    rules = [
        {"rule_id": "B1", "priority": "P0", "sql": "SELECT COUNT(*) FROM bronze.t", "tables": ["bronze.t"]},
        {"rule_id": "S1", "priority": "P1", "sql": "SELECT COUNT(*) FROM silver.t", "tables": ["silver.t"]},
    ]
    all_results = {
        "B1": {"rule_id": "B1", "passed": False},
        "S1": {"rule_id": "S1", "passed": True},
    }

    def fake_run_rules(*, rule_filter=None, **kwargs):
        matched = [r for r in rules if rule_filter is None or rule_filter(r)]
        return {"results": [all_results[r["rule_id"]] for r in matched]}

    with (
        patch("brightsmith.infra.dq_runner.load_rules", return_value=rules),
        patch("brightsmith.infra.dq_runner.run_rules", side_effect=fake_run_rules),
    ):
        ok, passed, failed, p0 = _run_dq_for_zone("bronze")
    assert ok is False
    assert passed == 0
    assert failed == 1
    assert p0 == ["B1"]

    with (
        patch("brightsmith.infra.dq_runner.load_rules", return_value=rules),
        patch("brightsmith.infra.dq_runner.run_rules", side_effect=fake_run_rules),
    ):
        ok2, passed2, failed2, p02 = _run_dq_for_zone("silver")
    assert ok2 is True
    assert passed2 == 1
    assert failed2 == 0


def test_rule_matches_zone_via_sql_and_alias():
    rule = {"sql": "SELECT * FROM raw.foo", "tables": []}
    assert _rule_matches_zone(rule, "bronze") is True
    assert _rule_matches_zone(rule, "silver") is False


# ---------------------------------------------------------------------------
# W4 (audit finding M2) — each DQ rule executes exactly once per pipeline run,
# not once per zone. Real Iceberg tables + a spy on execute_sql_rule so this
# fails if _run_dq_for_zone ever goes back to calling run_rules() unfiltered.
# ---------------------------------------------------------------------------


def test_dq_rules_execute_exactly_once_across_multi_zone_pipeline(tmp_path, restore_config):
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    from brightsmith.infra.dq_runner import execute_sql_rule as real_execute_sql_rule
    from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table

    restore_config.configure(project_root=tmp_path)

    warehouse = tmp_path / "data" / "bronze" / "iceberg_warehouse"
    catalog_db = tmp_path / "data" / "catalog" / "catalog.db"
    catalog = get_catalog(warehouse, catalog_db)

    schema = Schema(NestedField(1, "record_id", StringType(), required=False))
    append_data(get_or_create_table(catalog, "bronze", "seed_facts", schema), [{"record_id": "r1"}])
    append_data(get_or_create_table(catalog, "silver", "seed_facts", schema), [{"record_id": "r1"}])

    rules_dir = tmp_path / "governance" / "dq-rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "test.json").write_text(json.dumps({
        "spec": "test",
        "tables": [],
        "rules": [
            {
                "rule_id": "BZ-1",
                "priority": "P1",
                "status": "active",
                "category": "completeness",
                "sql": "SELECT COUNT(*) FROM bronze.seed_facts",
                "threshold": "result >= 0",
            },
            {
                "rule_id": "SV-1",
                "priority": "P1",
                "status": "active",
                "category": "completeness",
                "sql": "SELECT COUNT(*) FROM silver.seed_facts",
                "threshold": "result >= 0",
            },
        ],
    }))

    (tmp_path / "noop.py").write_text("def main():\n    return {'rows_promoted': 0, 'rows_skipped': 0}\n")
    run_mod._ZONE_REGISTRY["bronze"] = [_ZoneStep(module="noop.py", function="main", file_path=True)]
    run_mod._ZONE_REGISTRY["silver"] = [_ZoneStep(module="noop.py", function="main", file_path=True)]

    calls: list[str] = []

    def _spy(rule, con):
        calls.append(rule["rule_id"])
        return real_execute_sql_rule(rule, con)

    with patch("brightsmith.infra.dq_runner.execute_sql_rule", side_effect=_spy):
        result = run_pipeline(zones=["bronze", "silver"])

    assert result.status == "SUCCESS", {z: r.error for z, r in result.zones.items()}
    # Each rule id executed exactly once total — not once per zone (M2/W4).
    assert calls.count("BZ-1") == 1, calls
    assert calls.count("SV-1") == 1, calls
    assert sorted(calls) == ["BZ-1", "SV-1"]
    # And gating stayed per-zone: bronze's DQ result reflects only BZ-1.
    assert result.zones["bronze"].dq_rules_passed == 1
    assert result.zones["silver"].dq_rules_passed == 1


# ---------------------------------------------------------------------------
# _check_contracts_for_zone / _verify_contracts_for_zone / _contract_matches_zone
# ---------------------------------------------------------------------------


def test_contract_matches_zone_alias_and_malformed():
    assert _contract_matches_zone({"table": "raw.foo"}, "bronze") is True
    assert _contract_matches_zone({"table": "no-dot"}, "bronze") is False
    assert _contract_matches_zone({}, "bronze") is False


def test_check_contracts_for_zone_no_contracts_is_a_pass():
    with patch("brightsmith.infra.contract.list_contracts", return_value=[]):
        assert _check_contracts_for_zone("bronze") is True


def test_check_contracts_for_zone_fail_result_fails_gate():
    from brightsmith.infra.contract import ContractVerificationResult

    contracts = [{"name": "c1", "table": "bronze.foo"}]
    fail = [ContractVerificationResult("schema_match", "FAIL", "boom")]
    with (
        patch("brightsmith.infra.contract.list_contracts", return_value=contracts),
        patch("brightsmith.infra.contract.verify_contract", return_value=fail),
    ):
        assert _check_contracts_for_zone("bronze") is False


def test_verify_contracts_for_zone_counts_valid_and_violated():
    from brightsmith.infra.contract import ContractVerificationResult

    contracts = [
        {"name": "c1", "table": "bronze.a"},
        {"name": "c2", "table": "bronze.b"},
    ]
    results_by_name = {
        "c1": [ContractVerificationResult("schema_match", "PASS", "")],
        "c2": [ContractVerificationResult("schema_match", "FAIL", "boom")],
    }
    with (
        patch("brightsmith.infra.contract.list_contracts", return_value=contracts),
        patch("brightsmith.infra.contract.verify_contract", side_effect=lambda name: results_by_name[name]),
    ):
        valid, violated = _verify_contracts_for_zone("bronze")
    assert (valid, violated) == (1, 1)


# ---------------------------------------------------------------------------
# _verify_golden_datasets
# ---------------------------------------------------------------------------


def test_verify_golden_datasets_empty_is_zero_result():
    with patch("brightsmith.infra.golden_dataset.list_golden_datasets", return_value=[]):
        result = _verify_golden_datasets()
    assert result == GoldenResult()


def test_verify_golden_datasets_computes_pass_rate():
    from brightsmith.infra.golden_dataset import VerificationResult

    datasets = [{"spec": "s1"}]
    checks = [
        VerificationResult(description="a", expected=1, actual=1, diff_pct=0.0, status="MATCH", filters={}, column="a"),
        VerificationResult(description="b", expected=2, actual=3, diff_pct=50.0, status="MISMATCH", filters={}, column="b"),
    ]
    with (
        patch("brightsmith.infra.golden_dataset.list_golden_datasets", return_value=datasets),
        patch("brightsmith.infra.golden_dataset.verify_golden_dataset", return_value=checks),
    ):
        result = _verify_golden_datasets()
    assert result.checked == 2
    assert result.passed == 1
    assert result.failed == 1
    assert result.pass_rate == 50.0


# ---------------------------------------------------------------------------
# check_headless_ready — readiness diagnostics
# ---------------------------------------------------------------------------


def test_headless_ready_zone_module_not_importable_is_an_issue():
    run_mod._ZONE_REGISTRY["bronze"] = [_ZoneStep(module="no.such.module", function="main", file_path=False)]
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.infra.contract.list_contracts", return_value=[{"name": "c", "status": "draft"}]),
    ):
        ready, issues = check_headless_ready()
    assert ready is False
    assert any("not importable" in i for i in issues)


def test_headless_ready_flags_anthropic_import(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    src = tmp_path / "src" / "raw"
    src.mkdir(parents=True)
    (src / "bad.py").write_text("import anthropic\n")

    run_mod._ZONE_REGISTRY["bronze"] = []
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.infra.contract.list_contracts", return_value=[{"name": "c", "status": "draft"}]),
    ):
        ready, issues = check_headless_ready()
    assert ready is False
    assert any("LLM import found" in i for i in issues)


def test_headless_ready_contract_check_exception_is_a_blocking_issue():
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.infra.contract.list_contracts", side_effect=RuntimeError("contract db down")),
    ):
        ready, issues = check_headless_ready()
    assert ready is False
    assert any("Contract verification could not be completed" in i for i in issues)


def test_headless_ready_active_contract_failure_is_an_issue():
    from brightsmith.infra.contract import ContractVerificationResult

    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.infra.contract.list_contracts", return_value=[{"name": "c1", "status": "active"}]),
        patch("brightsmith.infra.contract.verify_contract", return_value=[ContractVerificationResult("x", "FAIL", "boom")]),
    ):
        ready, issues = check_headless_ready()
    assert ready is False
    assert any("verification FAILED" in i for i in issues)


def test_headless_ready_no_dq_rules_files_is_an_issue(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    (tmp_path / "governance" / "dq-rules").mkdir(parents=True)
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.infra.contract.list_contracts", return_value=[{"name": "c", "status": "draft"}]),
    ):
        ready, issues = check_headless_ready()
    assert any("No DQ rules files found" in i for i in issues)


def test_headless_ready_missing_dq_rules_dir_is_an_issue(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    with (
        patch("brightsmith.run._load_zone_registry"),
        patch("brightsmith.infra.contract.list_contracts", return_value=[{"name": "c", "status": "draft"}]),
    ):
        ready, issues = check_headless_ready()
    assert any("DQ rules directory missing" in i for i in issues)


# ---------------------------------------------------------------------------
# _save_run_history
# ---------------------------------------------------------------------------


def test_save_run_history_writes_timestamped_file(tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    result = PipelineResult()
    result.finalize()
    path = run_mod._save_run_history(result)
    assert path.exists()
    saved = json.loads(path.read_text())
    assert saved["run_id"] == result.run_id


# ---------------------------------------------------------------------------
# main() CLI + _print_summary + _cmd_headless_ready
# ---------------------------------------------------------------------------


def test_main_headless_ready_flag_dispatches(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["run", "--headless-ready"])
    with patch("brightsmith.run.check_headless_ready", return_value=(True, [])):
        run_mod.main()
    assert "READY for headless execution." in capsys.readouterr().out


def test_cmd_headless_ready_not_ready_exits_config_error(capsys):
    with patch("brightsmith.run.check_headless_ready", return_value=(False, ["issue one"])):
        with pytest.raises(SystemExit) as exc:
            run_mod._cmd_headless_ready()
    assert exc.value.code == EXIT_CONFIG_ERROR
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "issue one" in out


def test_main_json_output_and_exit_code(monkeypatch, tmp_path, restore_config, capsys):
    restore_config.configure(project_root=tmp_path)
    monkeypatch.setattr("sys.argv", ["run", "--zone", "bronze", "--output", "json"])
    fake_result = PipelineResult()
    fake_result.add_zone_result("bronze", ZoneResult(zone="bronze", status="SUCCESS"))
    fake_result.finalize()

    with patch("brightsmith.run.run_pipeline", return_value=fake_result):
        with pytest.raises(SystemExit) as exc:
            run_mod.main()
    assert exc.value.code == EXIT_SUCCESS
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "SUCCESS"

    # Run history was saved under the tmp project root.
    history_dir = tmp_path / "governance" / "run-history"
    assert history_dir.exists()
    assert list(history_dir.glob("*.json"))


def test_main_zone_all_passes_none_to_run_pipeline(monkeypatch, tmp_path, restore_config):
    restore_config.configure(project_root=tmp_path)
    monkeypatch.setattr("sys.argv", ["run", "--zone", "all"])
    fake_result = PipelineResult()
    fake_result.finalize()
    with patch("brightsmith.run.run_pipeline", return_value=fake_result) as rp:
        with pytest.raises(SystemExit):
            run_mod.main()
    assert rp.call_args.kwargs["zones"] is None


def test_print_summary_covers_all_branches(capsys):
    result = PipelineResult()
    result.add_zone_result("bronze", ZoneResult(
        zone="bronze", status="SUCCESS", rows_promoted=10, rows_skipped=2,
        dq_rules_passed=3, dq_rules_failed=1, contracts_valid=2, contracts_violated=1,
        warnings=["heads up"],
    ))
    result.add_zone_result("silver", ZoneResult(zone="silver", status="FAILED", error="boom"))
    result.add_zone_result("gold", ZoneResult(zone="gold", status="SKIPPED"))
    result.golden_datasets = GoldenResult(checked=4, passed=3, failed=1, pass_rate=75.0)
    result.finalize()

    run_mod._print_summary(result)
    out = capsys.readouterr().out
    assert "[PASS]" in out
    assert "[FAIL]" in out
    assert "SKIPPED" in out
    assert "Rows: 10 promoted, 2 skipped" in out
    assert "DQ:   3 passed, 1 failed" in out
    assert "Contracts: 2 valid, 1 violated" in out
    assert "Error: boom" in out
    assert "Warning: heads up" in out
    assert "Golden datasets: 3/4 (75%)" in out
