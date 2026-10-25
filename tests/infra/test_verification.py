"""Gate-semantics tests for the verification framework (M2.1).

``brightsmith.infra.verification`` is the MCP-zone completion gate: ``/assay``
consumes its exit code and ``@staff-engineer`` requires ``verification run`` to
report a pass rate >= threshold before a spec is marked COMPLETE. It is a thin
wrapper over ``golden_dataset.verify_golden_dataset`` but owns the pass-rate
aggregation, the threshold decision, the MATCH/CLOSE-as-pass bucketing, and the
"no golden datasets -> FAIL" behavior. These tests pin exactly that logic; the
underlying engine is stubbed so the gate semantics are tested in isolation.
"""

from __future__ import annotations

import argparse

import pytest

import brightsmith.infra.verification as verification
from brightsmith.infra.golden_dataset import VerificationResult


def _result(status: str) -> VerificationResult:
    return VerificationResult(
        description=f"val-{status}",
        expected=100.0,
        actual=100.0 if status in ("MATCH", "CLOSE") else 999.0,
        diff_pct=0.0,
        status=status,
        filters={},
        column="value",
    )


# --- run_verification: pass-rate aggregation & bucketing ------------------


def test_match_and_close_count_as_pass(monkeypatch):
    """MATCH and CLOSE are passes; MISMATCH and MISSING are not."""
    monkeypatch.setattr(
        verification, "verify_golden_dataset",
        lambda spec, tolerance_override=None: [
            _result("MATCH"), _result("CLOSE"), _result("MISMATCH"), _result("MISSING"),
        ],
    )
    results, pass_rate = verification.run_verification(spec="s")
    assert len(results) == 4
    assert pass_rate == 50.0  # 2 of 4 (MATCH + CLOSE)


def test_all_pass_is_100(monkeypatch):
    monkeypatch.setattr(
        verification, "verify_golden_dataset",
        lambda spec, tolerance_override=None: [_result("MATCH"), _result("CLOSE")],
    )
    _results, pass_rate = verification.run_verification(spec="s")
    assert pass_rate == 100.0


def test_empty_results_yield_zero_rate(monkeypatch):
    """No golden values -> ([], 0.0), never a divide-by-zero or vacuous pass."""
    monkeypatch.setattr(verification, "verify_golden_dataset", lambda spec, tolerance_override=None: [])
    results, pass_rate = verification.run_verification(spec="s")
    assert results == []
    assert pass_rate == 0.0


def test_all_specs_iterated_when_spec_omitted(monkeypatch):
    """With no spec, every listed golden dataset is verified and aggregated."""
    monkeypatch.setattr(
        verification, "list_golden_datasets",
        lambda: [{"spec": "a"}, {"spec": "b"}],
    )
    seen: list[str] = []

    def fake_verify(spec, tolerance_override=None):
        seen.append(spec)
        return [_result("MATCH")]

    monkeypatch.setattr(verification, "verify_golden_dataset", fake_verify)
    results, pass_rate = verification.run_verification()
    assert seen == ["a", "b"]
    assert len(results) == 2
    assert pass_rate == 100.0


def test_tolerance_override_is_forwarded(monkeypatch):
    """--tolerance must reach the engine unchanged."""
    captured: dict = {}

    def fake_verify(spec, tolerance_override=None):
        captured["tol"] = tolerance_override
        return [_result("MATCH")]

    monkeypatch.setattr(verification, "verify_golden_dataset", fake_verify)
    verification.run_verification(spec="s", tolerance=5.0)
    assert captured["tol"] == 5.0


# --- _cmd_run: the exit-code gate -----------------------------------------


def _run_cmd(monkeypatch, statuses, threshold=80.0):
    # A golden dataset exists for the spec (precondition met) ...
    monkeypatch.setattr(verification, "load_golden_dataset", lambda spec, golden_dir=None: {"values": [1]})
    # ... and it produces these per-value results.
    monkeypatch.setattr(
        verification, "verify_golden_dataset",
        lambda spec, tolerance_override=None: [_result(s) for s in statuses],
    )
    args = argparse.Namespace(spec="s", tolerance=None, threshold=threshold)
    verification._cmd_run(args)


def test_cmd_run_passes_at_or_above_threshold(monkeypatch):
    """Pass rate >= threshold exits 0 (no SystemExit)."""
    _run_cmd(monkeypatch, ["MATCH", "MATCH", "MATCH", "MATCH", "MISMATCH"], threshold=80.0)
    # 4/5 = 80% >= 80% -> returns normally


def test_cmd_run_fails_below_threshold(monkeypatch):
    """Pass rate < threshold exits non-zero."""
    with pytest.raises(SystemExit) as exc:
        _run_cmd(monkeypatch, ["MATCH", "MISMATCH", "MISMATCH", "MISMATCH"], threshold=80.0)
    assert exc.value.code == 1


def test_cmd_run_no_golden_dataset_for_spec_is_skip(monkeypatch):
    """A spec with no golden dataset SKIPS (exit 0) — verification is not
    applicable, so its absence must not block the spec.

    Conditional policy: golden present -> enforce; golden absent -> skip.
    """
    monkeypatch.setattr(verification, "load_golden_dataset", lambda spec, golden_dir=None: None)
    # verify_golden_dataset must not even be consulted when there's no dataset.
    monkeypatch.setattr(
        verification, "verify_golden_dataset",
        lambda spec, tolerance_override=None: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    args = argparse.Namespace(spec="s", tolerance=None, threshold=80.0)
    verification._cmd_run(args)  # returns normally (exit 0) — no SystemExit


def test_cmd_run_all_specs_no_datasets_is_skip(monkeypatch):
    """All-specs mode with zero golden datasets anywhere skips (exit 0)."""
    monkeypatch.setattr(verification, "list_golden_datasets", lambda: [])
    args = argparse.Namespace(spec=None, tolerance=None, threshold=80.0)
    verification._cmd_run(args)  # returns normally — no SystemExit


def test_cmd_run_present_dataset_but_no_values_is_fail(monkeypatch):
    """A golden dataset that EXISTS but yields no checkable values is a FAIL,
    not a skip — the dataset was meant to verify something (e.g. a malformed
    table reference must not pass silently)."""
    monkeypatch.setattr(verification, "load_golden_dataset", lambda spec, golden_dir=None: {"values": [1]})
    monkeypatch.setattr(verification, "verify_golden_dataset", lambda spec, tolerance_override=None: [])
    args = argparse.Namespace(spec="s", tolerance=None, threshold=80.0)
    with pytest.raises(SystemExit) as exc:
        verification._cmd_run(args)
    assert exc.value.code == 1
