"""Characterization tests for the pipeline gate — the governance enforcement core.

These tests DEFINE the INTENDED behavior of the pipeline gate CLI. The gate is
the contract the Claude Code agents actually touch (subprocess CLI calls), so the
gate-level scenarios run via real `subprocess.run` against a tmp project root — NOT
in-process — because the bug class being encoded here is precisely *cross-process
state* (audit findings A1/T1/T3).

Today `PipelineGate._save()` is a no-op (`pipeline_gate.py:281-283`) while `_load()`
reads `{spec}-pipeline.json`. Because each CLI invocation is a separate process,
state written by `init`/`complete`/`approve` is lost and the next `check` reports
BLOCKED. The scenarios that depend on durable, cross-process state therefore FAIL
against today's broken code — that red is correct and expected. Each such test is
annotated `# RED until WP-1.1`; they go green when WP-1.1 restores file persistence
and fixes the exporter shape.

Project-root redirection env var (confirmed in src/brightsmith/config.py:21):
    BRIGHTSMITH_PROJECT_ROOT
Setting it points every config-derived path (pipeline-state dir, Iceberg
warehouses, governance warehouse) at the tmp root, so neither JSON state nor the
Iceberg/governance event writes can leak into the real `data/` directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from brightsmith.infra.pipeline_gate import BRONZE_ZONE_STEPS

ROOT_ENV_VAR = "BRIGHTSMITH_PROJECT_ROOT"


# ---------------------------------------------------------------------------
# Subprocess helpers — the agents' real interface is the CLI, so we exercise it.
# ---------------------------------------------------------------------------


def run_gate(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke the pipeline_gate CLI in a FRESH process rooted at tmp_path.

    Both the JSON state dir and all Iceberg/governance writes are redirected to
    tmp_path via BRIGHTSMITH_PROJECT_ROOT, so the real project warehouse is never
    touched.
    """
    env = {**os.environ, ROOT_ENV_VAR: str(tmp_path)}
    return subprocess.run(
        [sys.executable, "-m", "brightsmith.infra.pipeline_gate", *args],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )


def run_py(tmp_path: Path, code: str) -> subprocess.CompletedProcess:
    """Run a small python program in a fresh process rooted at tmp_path."""
    env = {**os.environ, ROOT_ENV_VAR: str(tmp_path)}
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )


def state_file(tmp_path: Path, spec: str) -> Path:
    return tmp_path / "governance" / "pipeline-state" / f"{spec}-pipeline.json"


def seed_bronze_warehouse(tmp_path: Path) -> None:
    """Seed a minimal populated Iceberg warehouse at the config-derived paths.

    `validate` runs `_validate_warehouse_population`, which requires the catalog
    to exist with a non-empty table in the zone namespace. The catalog/warehouse
    paths must match config exactly (PROJECT_ROOT/data/...), and PROJECT_NAME must
    match the subprocess's (both default to "brightsmith").
    """
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    from brightsmith.infra.iceberg_setup import (
        append_data,
        get_catalog,
        get_or_create_table,
    )

    warehouse = tmp_path / "data" / "bronze" / "iceberg_warehouse"
    catalog_db = tmp_path / "data" / "catalog" / "catalog.db"
    catalog = get_catalog(warehouse, catalog_db)
    schema = Schema(
        NestedField(1, "record_id", StringType(), required=False),
        NestedField(2, "val", StringType(), required=False),
    )
    table = get_or_create_table(catalog, "bronze", "seed_facts", schema)
    append_data(table, [{"record_id": "r1", "val": "a"}, {"record_id": "r2", "val": "b"}])


# ---------------------------------------------------------------------------
# Scenario 1 — init creates durable state discoverable by a fresh process
# ---------------------------------------------------------------------------


def test_init_creates_durable_state_for_fresh_process(tmp_path):
    """`init` must persist state that a SEPARATE later process can read."""
    spec = "durable-init"
    init = run_gate(tmp_path, "init", spec, "--zone", "bronze")
    assert init.returncode == 0, init.stderr

    # The state must be durable on disk — this is the core of A1.
    assert state_file(tmp_path, spec).exists(), (
        "RED until WP-1.1: init must write a durable {spec}-pipeline.json that "
        "survives the process; today _save() is a no-op."
    )

    # ...and a FRESH process must be able to discover it.
    audit = run_gate(tmp_path, "audit", "--format", "json")
    assert "No pipeline state directory" not in audit.stdout, (
        "RED until WP-1.1: a fresh audit process cannot see init's state because "
        "nothing was persisted."
    )
    data = json.loads(audit.stdout)
    specs = [s["spec"] for s in data["specs"]]
    assert spec in specs


# ---------------------------------------------------------------------------
# Scenario 2 — complete a prerequisite, then check the dependent step → CLEAR
# (this is the audit's smoking-gun reproduction)
# ---------------------------------------------------------------------------


def test_complete_then_check_dependent_clears(tmp_path):
    """The audit repro: init → complete(prereq) → check(dependent) must CLEAR."""
    spec = "repro"
    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0
    assert run_gate(tmp_path, "complete", spec, "governance-reviewer-pre").returncode == 0

    check = run_gate(tmp_path, "check", spec, "primary-agent")
    assert check.returncode == 0, (
        "RED until WP-1.1: after completing 'governance-reviewer-pre', checking "
        f"'primary-agent' must CLEAR. Got stderr: {check.stderr!r}"
    )
    assert "CLEAR" in check.stdout


# ---------------------------------------------------------------------------
# Scenario 3 — check a step with unmet prerequisites → exit 1, BLOCKED
# ---------------------------------------------------------------------------


def test_check_unmet_prereqs_blocked(tmp_path):
    """A step whose prerequisites are not met must be BLOCKED (exit 1)."""
    spec = "blocked"
    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0

    check = run_gate(tmp_path, "check", spec, "primary-agent")
    assert check.returncode == 1
    assert "BLOCKED" in check.stderr
    assert "governance-reviewer-pre" in check.stderr


# ---------------------------------------------------------------------------
# Scenario 4 — skip of a non-skippable step → exit 1, refused
# ---------------------------------------------------------------------------


def test_skip_non_skippable_step_refused(tmp_path):
    """Skipping a non-skippable step (e.g. primary-agent) must be refused."""
    spec = "no-skip"
    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0

    skip = run_gate(
        tmp_path, "skip", spec, "primary-agent",
        "--reason", "trying to skip", "--evidence", "governance/whatever.md",
    )
    assert skip.returncode == 1
    assert "skippable" in skip.stderr.lower()


# ---------------------------------------------------------------------------
# Scenario 5 — skip without reason/evidence → exit 1
# ---------------------------------------------------------------------------


def test_skip_without_reason_or_evidence_refused(tmp_path):
    """A skip must carry both a reason and evidence — empty values are refused.

    Uses a silver spec's skippable `cab-review` step so we reach the
    reason/evidence guard (which sits after the skippability check).
    """
    spec = "skip-needs-justification"
    assert run_gate(tmp_path, "init", spec, "--zone", "silver").returncode == 0

    skip = run_gate(
        tmp_path, "skip", spec, "cab-review", "--reason", "", "--evidence", "",
    )
    # Refused regardless of the persistence bug (empty reason -> ValueError -> exit 1).
    assert skip.returncode == 1
    assert skip.stderr.strip(), "a refusal must explain itself on stderr"


# ---------------------------------------------------------------------------
# Scenario 6 — validate with NOT_STARTED steps → exit 1, names them
# ---------------------------------------------------------------------------


def test_validate_with_not_started_steps_fails_and_names_them(tmp_path):
    """A freshly-initialized pipeline (all steps NOT_STARTED) must fail validation."""
    spec = "fresh"
    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0

    val = run_gate(tmp_path, "validate", spec)
    assert val.returncode == 1
    # The first bronze step must be named among the issues.
    assert "governance-reviewer-pre" in val.stdout
    assert "NOT_STARTED" in val.stdout


# ---------------------------------------------------------------------------
# Scenario 7 — validate with all steps complete + warehouse populated → exit 0
# ---------------------------------------------------------------------------


def test_validate_passes_when_all_steps_complete(tmp_path):
    """When every canonical step is COMPLETED and the warehouse is populated,
    validate must PASS (exit 0).
    """
    spec = "complete-pipeline"
    seed_bronze_warehouse(tmp_path)
    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0

    # Complete every bronze step, each in its own process (the accumulation across
    # processes is exactly the behavior the persistence fix must guarantee).
    for step in BRONZE_ZONE_STEPS:
        res = run_gate(tmp_path, "complete", spec, step.name)
        assert res.returncode == 0, res.stderr

    val = run_gate(tmp_path, "validate", spec)
    assert val.returncode == 0, (
        "RED until WP-1.1: with all steps completed across processes and the "
        f"warehouse seeded, validate must PASS. Got: {val.stdout!r}"
    )
    assert "PASS" in val.stdout


# ---------------------------------------------------------------------------
# Scenario 8 — output-hash tamper detection
# ---------------------------------------------------------------------------


def test_output_hash_tamper_is_detected(tmp_path):
    """Modifying a completed step's output file must be flagged by validate."""
    spec = "tamper"
    out_rel = "governance/eda/out.md"
    out_abs = tmp_path / out_rel
    out_abs.parent.mkdir(parents=True, exist_ok=True)
    out_abs.write_text("original content v1\n")

    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0
    res = run_gate(tmp_path, "complete", spec, "governance-reviewer-pre", "--output", out_rel)
    assert res.returncode == 0, res.stderr

    # Tamper with the output after completion.
    out_abs.write_text("tampered content v2 — different bytes\n")

    val = run_gate(tmp_path, "validate", spec)
    assert "modified after completion" in val.stdout, (
        "RED until WP-1.1: validate must detect that the recorded output hash no "
        f"longer matches the on-disk file. Got: {val.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 9 — approve records a decision visible to a fresh process
# ---------------------------------------------------------------------------


def test_approve_visible_to_fresh_process(tmp_path):
    """An approval recorded by one process must be visible to a later process."""
    spec = "approval"
    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0

    appr = run_gate(
        tmp_path, "approve", spec, "business-terms",
        "--decision", "APPROVED", "--by", "human:test",
    )
    assert appr.returncode == 0, appr.stderr

    status = run_gate(tmp_path, "status", spec)
    assert "business-terms" in status.stdout, (
        "RED until WP-1.1: a fresh status process must see the recorded approval. "
        f"Got: {status.stdout!r}"
    )
    assert "APPROVED" in status.stdout


# ---------------------------------------------------------------------------
# Scenario 10 — check-transition consumes exporter-regenerated state
# ---------------------------------------------------------------------------


def test_check_transition_on_exporter_regenerated_state(tmp_path):
    """`check-transition` must consume the JSON shape produced by
    `export_pipeline_state_to_files()` (key `spec`, zone populated, `steps` dict).
    """
    spec = "xfer-spec"
    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0
    assert run_gate(tmp_path, "complete", spec, "governance-reviewer-pre").returncode == 0
    assert run_gate(tmp_path, "complete", spec, "primary-agent").returncode == 0

    # Force reliance on the exporter: drop any gate-written state files first.
    state_dir = tmp_path / "governance" / "pipeline-state"
    if state_dir.exists():
        for f in state_dir.glob("*.json"):
            f.unlink()

    # Regenerate state files purely from the Iceberg event log.
    regen = run_py(
        tmp_path,
        "from brightsmith.infra.governance.exporters import "
        "export_pipeline_state_to_files as f; print(len(f()))",
    )
    assert regen.returncode == 0, regen.stderr
    regenerated = list(state_dir.glob("*-pipeline.json"))
    assert regenerated, "exporter produced no state file to test against"

    trans = run_gate(tmp_path, "check-transition", "bronze", "silver")
    assert "Traceback" not in trans.stderr and "KeyError" not in trans.stderr, (
        "check-transition must not crash on the exporter's regenerated shape. "
        f"stderr: {trans.stderr!r}"
    )
    # The regenerated file must let check-transition actually FIND and validate the
    # spec — today it reports "No specs found" because the zone is not carried over.
    assert "No specs found" not in trans.stdout, (
        "RED until WP-1.1: check-transition cannot match the spec to its zone in "
        f"the exporter output. stdout: {trans.stdout!r}"
    )
    # The spec name appears in the per-spec validation issues, proving the steps
    # dict was consumed (only 2 of 16 steps complete -> NOT READY, named).
    assert spec in trans.stdout, (
        "RED until WP-1.1: expected per-spec validation issues referencing "
        f"'{spec}'. stdout: {trans.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 11 — audit --format json parses and includes steps
# ---------------------------------------------------------------------------


def test_audit_json_parses_and_includes_steps(tmp_path):
    """`audit --format json` must emit parseable JSON whose specs carry a steps dict."""
    spec = "audit-json"
    assert run_gate(tmp_path, "init", spec, "--zone", "bronze").returncode == 0
    assert run_gate(tmp_path, "complete", spec, "governance-reviewer-pre").returncode == 0

    audit = run_gate(tmp_path, "audit", "--format", "json")
    assert "No pipeline state directory" not in audit.stdout, (
        "RED until WP-1.1: audit has no persisted state to report."
    )
    data = json.loads(audit.stdout)
    entry = next(s for s in data["specs"] if s["spec"] == spec)
    assert entry["steps"], "audit must include the per-step status map"
    assert "governance-reviewer-pre" in entry["steps"]


# ---------------------------------------------------------------------------
# Scenario 12 — in-process unit tests for edge cases (no I/O, pure logic)
# ---------------------------------------------------------------------------


def test_get_step_def_unknown_step_raises():
    """_get_step_def must raise ValueError for an unknown step name."""
    from brightsmith.infra.pipeline_gate import _get_step_def

    with pytest.raises(ValueError, match="Unknown step"):
        _get_step_def("bronze", "greenfield", "no-such-step")


def test_check_prerequisites_blocks_then_clears(tmp_path):
    """check_prerequisites raises GateBlockedError until prereqs are COMPLETED."""
    from brightsmith.infra.pipeline_gate import GateBlockedError, PipelineGate

    gate = PipelineGate("inproc", state_dir=tmp_path)
    # Hand-build state so we touch zero Iceberg/file I/O.
    gate._state = {
        "zone": "bronze",
        "mode": "greenfield",
        "steps": {
            "governance-reviewer-pre": {
                "status": "NOT_STARTED",
                "agent": "@governance-reviewer",
                "requires": [],
            },
        },
        "skipped_steps": {},
    }

    with pytest.raises(GateBlockedError) as exc:
        gate.check_prerequisites("primary-agent")
    assert "governance-reviewer-pre" in exc.value.missing

    # Once the prerequisite is COMPLETED, the gate clears (no raise).
    gate._state["steps"]["governance-reviewer-pre"]["status"] = "COMPLETED"
    gate.check_prerequisites("primary-agent")


def test_check_prerequisites_unknown_step_raises(tmp_path):
    """check_prerequisites on an unknown step surfaces the ValueError."""
    from brightsmith.infra.pipeline_gate import PipelineGate

    gate = PipelineGate("inproc", state_dir=tmp_path)
    gate._state = {"zone": "bronze", "mode": "greenfield", "steps": {}, "skipped_steps": {}}
    with pytest.raises(ValueError, match="Unknown step"):
        gate.check_prerequisites("no-such-step")
