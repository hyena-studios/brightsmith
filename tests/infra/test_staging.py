"""Tests for the human-approval staging gate (src/brightsmith/infra/staging.py).

Covers the `apply_gate` confidence × REQUIRE_HUMAN_APPROVAL matrix (the 4
combinations) and an approve/reject round-trip through the staging file.

Gate logic under test:
  - confidence < floor                          -> needs_review (always)
  - confidence >= floor AND require_approval     -> needs_review
  - confidence >= floor AND not require_approval  -> auto_promote
"""

from __future__ import annotations

from brightsmith.infra.staging import (
    apply_gate,
    approve_proposals,
    get_pending,
    read_staging,
    reject_proposals,
    write_staging,
)

FLOOR = 0.7
HIGH = 0.9  # >= floor
LOW = 0.5   # < floor


def _proposal(mapping_id: str, confidence: float) -> dict:
    return {"mapping_id": mapping_id, "confidence": confidence, "status": "pending"}


# --- apply_gate matrix: confidence × require_human_approval -----------------


def test_apply_gate_high_confidence_approval_required_needs_review():
    """High confidence but approval mandated -> human review, gate stops."""
    result = apply_gate(
        [_proposal("m1", HIGH)], require_human_approval=True, confidence_floor=FLOOR
    )
    assert result["needs_review"] == [_proposal("m1", HIGH)]
    assert result["auto_promote"] == []
    assert result["gate_action"] == "stop"


def test_apply_gate_high_confidence_no_approval_auto_promotes():
    """High confidence and approval not required -> auto-promote, gate proceeds."""
    result = apply_gate(
        [_proposal("m1", HIGH)], require_human_approval=False, confidence_floor=FLOOR
    )
    assert result["auto_promote"] == [_proposal("m1", HIGH)]
    assert result["needs_review"] == []
    assert result["gate_action"] == "auto_promote"


def test_apply_gate_low_confidence_approval_required_needs_review():
    """Low confidence -> human review regardless of toggle."""
    result = apply_gate(
        [_proposal("m1", LOW)], require_human_approval=True, confidence_floor=FLOOR
    )
    assert result["needs_review"] == [_proposal("m1", LOW)]
    assert result["auto_promote"] == []
    assert result["gate_action"] == "stop"


def test_apply_gate_low_confidence_no_approval_still_needs_review():
    """Low confidence overrides the toggle: still needs review even when approval off."""
    result = apply_gate(
        [_proposal("m1", LOW)], require_human_approval=False, confidence_floor=FLOOR
    )
    assert result["needs_review"] == [_proposal("m1", LOW)]
    assert result["auto_promote"] == []
    assert result["gate_action"] == "stop"


# --- approve / reject round-trip --------------------------------------------


def test_approve_reject_round_trip(tmp_path):
    """Proposals can be written, then approved/rejected, with status persisted."""
    staging = tmp_path / "proposed-mappings.json"
    write_staging(
        [_proposal("m1", HIGH), _proposal("m2", HIGH), _proposal("m3", LOW)],
        staging,
    )

    assert {p["mapping_id"] for p in get_pending(staging)} == {"m1", "m2", "m3"}

    approved = approve_proposals(staging, ["m1"], actor="human:test")
    assert [p["mapping_id"] for p in approved] == ["m1"]

    rejected = reject_proposals(
        staging, ["m2"], reason="ambiguous mapping", actor="human:test"
    )
    assert [p["mapping_id"] for p in rejected] == ["m2"]

    persisted = {p["mapping_id"]: p for p in read_staging(staging)}
    assert persisted["m1"]["status"] == "approved"
    assert persisted["m1"]["approved_by"] == "human:test"
    assert persisted["m2"]["status"] == "rejected"
    assert persisted["m2"]["rejection_reason"] == "ambiguous mapping"
    # m3 was never acted on — still pending.
    assert persisted["m3"]["status"] == "pending"
    assert get_pending(staging) == [persisted["m3"]]


def test_approve_all_pending_when_no_ids_given(tmp_path):
    """approve_proposals with mapping_ids=None approves all pending proposals."""
    staging = tmp_path / "proposed-mappings.json"
    write_staging([_proposal("m1", HIGH), _proposal("m2", HIGH)], staging)

    approved = approve_proposals(staging, None, actor="auto")
    assert {p["mapping_id"] for p in approved} == {"m1", "m2"}
    assert get_pending(staging) == []
