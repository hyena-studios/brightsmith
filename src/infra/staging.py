"""Staging file management and human approval gate logic.

Writes proposed mappings/resolutions to JSON for human review.
Implements the gate logic:
- REQUIRE_HUMAN_APPROVAL=True → always stop for review
- REQUIRE_HUMAN_APPROVAL=False AND confidence >= CONFIDENCE_FLOOR → auto-promote
- confidence < CONFIDENCE_FLOOR → always stop regardless of toggle
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def write_staging(
    proposals: list[dict],
    staging_path: str | Path,
) -> Path:
    """Write proposed mappings to staging JSON file."""
    staging_path = Path(staging_path)
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.write_text(json.dumps(proposals, indent=2, default=str))
    return staging_path


def read_staging(staging_path: str | Path) -> list[dict]:
    """Read proposed mappings from staging JSON file."""
    staging_path = Path(staging_path)
    if not staging_path.exists():
        return []
    return json.loads(staging_path.read_text())


def archive_staging(staging_path: str | Path, archive_dir: str | Path) -> Path | None:
    """Move staging file to archive with timestamp. Returns archive path or None."""
    staging_path = Path(staging_path)
    if not staging_path.exists():
        return None

    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_dir / f"proposed-mappings-{ts}.json"
    shutil.move(str(staging_path), str(archive_path))
    return archive_path


def apply_gate(
    proposals: list[dict],
    *,
    require_human_approval: bool,
    confidence_floor: float,
) -> dict:
    """Apply the human approval gate logic.

    Returns a dict with:
      - "auto_promote": list of proposals that can be auto-promoted
      - "needs_review": list of proposals that need human review
      - "gate_action": "stop" if any proposals need review, "auto_promote" if all can proceed
    """
    auto_promote = []
    needs_review = []

    for proposal in proposals:
        confidence = proposal.get("confidence", 0.0)

        if confidence < confidence_floor:
            needs_review.append(proposal)
        elif require_human_approval:
            needs_review.append(proposal)
        else:
            auto_promote.append(proposal)

    gate_action = "stop" if needs_review else "auto_promote"
    return {
        "auto_promote": auto_promote,
        "needs_review": needs_review,
        "gate_action": gate_action,
    }


def approve_proposals(
    staging_path: str | Path,
    mapping_ids: list[str] | None = None,
    *,
    actor: str = "human",
) -> list[dict]:
    """Mark proposals as approved in the staging file.

    If mapping_ids is None, approves all pending proposals.
    Returns the list of approved proposals.
    """
    proposals = read_staging(staging_path)
    approved = []

    now = datetime.now(timezone.utc).isoformat()

    for p in proposals:
        if p["status"] != "pending":
            continue
        if mapping_ids is not None and p["mapping_id"] not in mapping_ids:
            continue
        p["status"] = "approved"
        p["approved_by"] = actor
        p["approved_at"] = now
        approved.append(p)

    write_staging(proposals, staging_path)
    return approved


def reject_proposals(
    staging_path: str | Path,
    mapping_ids: list[str],
    *,
    reason: str,
    actor: str = "human",
) -> list[dict]:
    """Mark proposals as rejected in the staging file."""
    proposals = read_staging(staging_path)
    rejected = []

    now = datetime.now(timezone.utc).isoformat()

    for p in proposals:
        if p["status"] != "pending":
            continue
        if p["mapping_id"] not in mapping_ids:
            continue
        p["status"] = "rejected"
        p["approved_by"] = actor
        p["approved_at"] = now
        p["rejection_reason"] = reason
        rejected.append(p)

    write_staging(proposals, staging_path)
    return rejected


def get_pending(staging_path: str | Path) -> list[dict]:
    """Get all pending proposals from staging."""
    proposals = read_staging(staging_path)
    return [p for p in proposals if p.get("status") == "pending"]
