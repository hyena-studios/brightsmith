"""DQ results say what kind of run produced them, and only real runs count.

2026-09-08 audit §3, three P0s with one root cause — a result could not say
where it came from:

* a chaos (shadow) run was recorded as a real run and overwrote the spec's
  DQ score, and became the spec's "latest" result;
* a hand-written result file synced from disk with no ``p0_passed`` field was
  recorded as a P0 PASS;
* the scorecard took each rule's priority from a metadata lookup and ignored
  the priority already stored on the result row, so a lookup miss turned a
  failing P0 into an informational note and printed "P0 Gate: PASS".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.infra.test_governance_db import gov_env  # noqa: F401 — fixture


def _run(spec: str, run_id: str, score: float, mode: str, *, minutes_ago: int = 0, rules: list[dict] | None = None):
    from brightsmith.infra.dq_runner import _write_governance_results

    results = rules or [{"rule_id": "R-1", "priority": "P0", "passed": score == 100.0, "error": None}]
    _write_governance_results(
        {
            "run_id": run_id,
            "spec": spec,
            "mode": mode,
            "executed_at": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(),
            "rules_total": len(results),
            "rules_passed": sum(1 for r in results if r["passed"]),
            "rules_failed": sum(1 for r in results if not r["passed"]),
            "rules_errored": 0,
            "p0_passed": all(r["passed"] for r in results if r["priority"] == "P0"),
            "results": results,
        },
        rules=[{"rule_id": r["rule_id"], "tables": ["silver.t"]} for r in results],
    )


def test_shadow_run_does_not_become_latest_or_move_the_score(gov_env):  # noqa: F811
    from brightsmith.infra.dq_runner import get_latest_results
    from brightsmith.infra.governance_db import get_current_specs, get_dq_runs

    _run("s", "real-1", 100.0, "real", minutes_ago=5)
    _run("s", "chaos-1", 0.0, "shadow")

    assert {r["run_id"]: r["mode"] for r in get_dq_runs("s")} == {"real-1": "real", "chaos-1": "shadow"}
    assert get_latest_results("s")["run_id"] == "real-1"
    [spec] = [row for row in get_current_specs() if row["spec_name"] == "s"]
    assert spec["dq_score_pct"] == pytest.approx(100.0)
    assert spec["dq_p0_passed"] is True


def test_run_rules_labels_shadow_runs(monkeypatch):
    """The mode comes from run_rules itself, not from callers remembering to set it."""
    from brightsmith.infra import dq_runner

    captured: dict = {}
    monkeypatch.setattr(dq_runner, "load_rules", lambda spec=None: [])
    monkeypatch.setattr(dq_runner, "_write_governance_results", lambda run, rules: captured.update(run))
    for shadow, expected in ((True, "shadow"), (False, "real")):
        dq_runner.run_rules(spec="s", catalog=object(), shadow=shadow)
        assert captured["mode"] == expected


def test_imported_result_file_without_p0_is_not_a_pass(gov_env):  # noqa: F811
    import brightsmith.config as cfg
    from brightsmith.infra.governance.sync import _sync_dq_results
    from brightsmith.infra.governance_db import get_dq_rule_results, get_dq_runs, get_latest_dq_run

    cfg.DQ_RESULTS_DIR.mkdir(parents=True)
    cfg.DQ_RULES_DIR.mkdir(parents=True)
    (cfg.DQ_RESULTS_DIR / "hand-written.json").write_text(json.dumps({
        "run_id": "hand-1",
        "spec": "s",
        "executed_at": datetime.now(UTC).isoformat(),
        "results": [{"rule_id": "R-1", "passed": False}],
    }))

    _sync_dq_results()

    [run] = get_dq_runs("s")
    assert (run["mode"], run["p0_passed"]) == ("imported", False)
    assert [r["mode"] for r in get_dq_rule_results("hand-1")] == ["imported"]
    assert get_latest_dq_run("s") is None  # an import is never the spec's current result


def test_rows_written_before_mode_existed_read_as_real(gov_env):  # noqa: F811
    """Upgrading must not hide every historical run: NULL mode reads as real,
    and the first write after upgrade adds the column to the old table."""
    from pyiceberg.schema import Schema

    from brightsmith.infra.governance.queries import _governance_catalog
    from brightsmith.infra.governance.schemas import DQ_RUNS_SCHEMA
    from brightsmith.infra.governance_db import get_latest_dq_run, write_dq_run
    from brightsmith.infra.iceberg_setup import append_data, get_or_create_table

    old_schema = Schema(*[f for f in DQ_RUNS_SCHEMA.fields if f.name != "mode"])
    old_table = get_or_create_table(_governance_catalog("dq_runs"), "governance_product", "dq_runs", old_schema)
    now = datetime.now(UTC)
    append_data(old_table, [{
        "record_id": "legacy", "run_id": "legacy-1", "spec_name": "s", "table_name": "silver.t",
        "executed_at": now - timedelta(days=1), "rules_total": 1, "rules_passed": 1, "rules_failed": 0,
        "rules_errored": 0, "rules_warning": 0, "score_pct": 100.0, "p0_passed": True, "updated_at": now,
    }])
    assert get_latest_dq_run("s")["run_id"] == "legacy-1"

    write_dq_run(
        run_id="chaos-1", spec_name="s", table_name="silver.t", executed_at=now, rules_total=1,
        rules_passed=0, rules_failed=1, rules_errored=0, score_pct=0.0, p0_passed=False, mode="shadow",
    )
    latest = get_latest_dq_run("s")
    assert (latest["run_id"], latest["mode"]) == ("legacy-1", None)


def test_unknown_mode_rejected(gov_env):  # noqa: F811
    from brightsmith.infra.governance_db import write_dq_run

    with pytest.raises(ValueError, match="mode"):
        write_dq_run(
            run_id="x", spec_name="s", table_name="t", executed_at=datetime.now(UTC), rules_total=0,
            rules_passed=0, rules_failed=0, rules_errored=0, score_pct=0.0, p0_passed=True, mode="test",
        )


# --- scorecard -------------------------------------------------------------


def _scorecard(results: list[dict], mode: str | None = None) -> str:
    from brightsmith.infra.dq_scorecard import generate_scorecard

    run = {"run_id": "r", "spec": "s", "executed_at": "2026-10-24T00:00:00Z", "results": results}
    if mode:
        run["mode"] = mode
    return generate_scorecard(run, "s").read_text()


def _failing(rule_id: str, **extra) -> dict:
    return {"rule_id": rule_id, "spec": "s", "passed": False, "detail": "actual=5", "error": None, **extra}


def test_scorecard_uses_priority_on_the_result_row(gov_env):  # noqa: F811
    """No rules file for the spec, so the metadata lookup misses: the row's own
    priority must still make the P0 failure block."""
    content = _scorecard([_failing("R-1", priority="P0")])
    assert "P0 Gate: FAIL" in content
    assert "R-1** (P0 — BLOCKING)" in content


def test_scorecard_failure_with_unknown_priority_blocks(gov_env):  # noqa: F811
    content = _scorecard([_failing("R-1")])
    assert "P0 Gate: FAIL" in content
    assert "unknown priority — BLOCKING" in content


def test_scorecard_p1_failure_does_not_block(gov_env):  # noqa: F811
    content = _scorecard([_failing("R-1", priority="P1")])
    assert "P0 Gate: PASS" in content
    assert "P1 Warnings:** 1" in content


def test_scorecard_for_shadow_run_is_not_a_gate(gov_env):  # noqa: F811
    content = _scorecard([{"rule_id": "R-1", "spec": "s", "passed": True, "priority": "P0"}], mode="shadow")
    assert "P0 Gate: NOT EVALUATED" in content
    assert "P0 Gate: PASS" not in content


def test_full_sync_never_takes_registry_dq_status_from_result_files(gov_env):  # noqa: F811
    """Governance review of Phase 0: _enrich_spec_registry read the newest
    result file directly and defaulted a missing p0_passed to True, so a file
    with 2/2 rules failing left the registry at dq_p0_passed=True."""
    import brightsmith.config as cfg
    from brightsmith.infra.governance.sync import sync_from_files
    from brightsmith.infra.governance_db import get_current_specs

    cfg.DQ_RESULTS_DIR.mkdir(parents=True)
    cfg.DQ_RULES_DIR.mkdir(parents=True)
    cfg.PIPELINE_STATE_DIR.mkdir(parents=True)
    (cfg.PIPELINE_STATE_DIR / "s-pipeline.json").write_text(json.dumps({"spec": "s", "zone": "silver", "steps": {}}))
    (cfg.DQ_RESULTS_DIR / "s-20261024.json").write_text(json.dumps({
        "run_id": "hand-2", "spec": "s", "executed_at": datetime.now(UTC).isoformat(),
        "rules_total": 2, "rules_passed": 0, "rules_failed": 2,
        "results": [{"rule_id": "R-1", "passed": False}, {"rule_id": "R-2", "passed": False}],
    }))

    sync_from_files()

    [spec] = [row for row in get_current_specs() if row["spec_name"] == "s"]
    assert spec["dq_p0_passed"] is not True


def test_full_sync_takes_registry_dq_status_from_latest_real_run(gov_env):  # noqa: F811
    import brightsmith.config as cfg
    from brightsmith.infra.governance.sync import sync_from_files
    from brightsmith.infra.governance_db import get_current_specs

    _run("s", "real-1", 0.0, "real", minutes_ago=1)
    cfg.PIPELINE_STATE_DIR.mkdir(parents=True)
    (cfg.PIPELINE_STATE_DIR / "s-pipeline.json").write_text(json.dumps({"spec": "s", "zone": "silver", "steps": {}}))

    sync_from_files()

    [spec] = [row for row in get_current_specs() if row["spec_name"] == "s"]
    assert spec["dq_p0_passed"] is False
    assert spec["dq_score_pct"] == pytest.approx(0.0)
