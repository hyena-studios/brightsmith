"""Change Approval Board (CAB) — schema change governance for Silver/Gold zones.

Classifies schema modifications as PATCH/MINOR/MAJOR using semver semantics,
maps downstream blast radius, and produces structured decision records. MAJOR
changes trigger a fork-and-migrate workflow with deprecation timelines.

Usage:
    python -m brightsmith.infra.cab review --spec <spec> --table <table>
    python -m brightsmith.infra.cab status --decision <id>
    python -m brightsmith.infra.cab approve --decision <id> --by <who> [--fork] [--notes "..."]
    python -m brightsmith.infra.cab deprecations
    python -m brightsmith.infra.cab history --table <table>
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    PATCH = "PATCH"
    MINOR = "MINOR"
    MAJOR = "MAJOR"


class Decision(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    APPROVED_WITH_FORK = "APPROVED_WITH_FORK"
    REJECTED = "REJECTED"


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    TYPE_CHANGED = "type_changed"
    RENAMED = "renamed"
    NULLABLE_CHANGED = "nullable_changed"
    GRAIN_CHANGED = "grain_changed"
    CDE_CHANGED = "cde_changed"
    DESCRIPTION_CHANGED = "description_changed"


# Mapping from (contract diff change_type, parsed detail) → Severity
_SEVERITY_MAP: dict[tuple[str, str], Severity] = {
    ("BREAKING", "removed"): Severity.MAJOR,
    ("BREAKING", "type_changed"): Severity.MAJOR,
    ("BREAKING", "grain_changed"): Severity.MAJOR,
    ("BREAKING", "renamed"): Severity.MAJOR,
    ("NON_BREAKING", "added"): Severity.MINOR,
    ("NON_BREAKING", "nullable_changed"): Severity.MINOR,
    ("NON_BREAKING", "description_changed"): Severity.PATCH,
    ("INFO", "metadata"): Severity.PATCH,
}

# Severity ordering for max()
_SEVERITY_ORDER = {Severity.PATCH: 0, Severity.MINOR: 1, Severity.MAJOR: 2}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SchemaChange:
    """A single field-level schema change with severity classification."""

    column_name: str
    change_type: ChangeType
    old_value: str | None
    new_value: str | None
    classification: Severity
    reason: str


@dataclass
class BlastRadiusItem:
    """A single downstream dependency affected by the schema change."""

    item_type: str  # "table", "contract", "golden_dataset", "mcp_tool", "grounding_doc"
    path: str
    relationship: str  # "direct_consumer", "transitive_consumer"


@dataclass
class ForkDetails:
    """Details of a table fork for MAJOR changes."""

    v1_table: str
    v2_table: str
    migration_spec_path: str
    deprecation_timeline_days: int
    deprecated_at: str  # ISO-8601
    archive_after: str  # ISO-8601


@dataclass
class HumanOverride:
    """Record of a human overriding the CAB decision."""

    action: str  # "reclassified", "timeline_adjusted", "approved_override", "rejected_override"
    original_classification: str
    override_classification: str | None
    overrider: str
    rationale: str
    timestamp: str  # ISO-8601


@dataclass
class CabDecisionRecord:
    """Complete CAB decision record — the primary governance artifact."""

    decision_id: str
    spec: str
    table_name: str
    created_at: str  # ISO-8601
    classification: str  # Severity value
    classification_reasons: list[dict]
    contract_version_before: str
    contract_version_after: str
    schema_diff: dict  # {added_columns, removed_columns, changed_columns, unchanged_columns}
    blast_radius: dict  # {downstream_tables, consumables, mcp_tools, ...}
    decision: str = Decision.PENDING.value
    decided_by: str = ""
    decided_at: str = ""
    notes: str = ""
    rationale: str = ""
    fork: dict | None = None
    human_override: dict | None = None
    spec_reference: str = ""
    agent: str = "@cab-agent"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items()}


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def _cab_dir() -> Path:
    from brightsmith.config import CAB_DECISIONS_DIR
    return CAB_DECISIONS_DIR


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _parse_diff_detail(change_type: str, description: str) -> tuple[str, str | None, str | None, str]:
    """Parse a ContractDiffItem description into (detail_key, column_name, old_val, new_val).

    Returns (detail_key, old_value, new_value, column_name).
    """
    # "Column 'revenue' type changed: double → decimal"
    col_match = re.search(r"Column '([^']+)'", description)
    column_name = col_match.group(1) if col_match else "unknown"

    if "missing from table" in description or "removed" in description.lower():
        return "removed", None, None, column_name

    if "type changed" in description:
        type_match = re.search(r":\s*(\S+)\s*→\s*(\S+)", description)
        old_val = type_match.group(1) if type_match else None
        new_val = type_match.group(2) if type_match else None
        return "type_changed", old_val, new_val, column_name

    if "nullability" in description:
        return "nullable_changed", None, None, column_name

    if "not in contract" in description or "new" in description.lower():
        return "added", None, None, column_name

    if "grain" in description.lower():
        return "grain_changed", None, None, column_name

    # Default: metadata/info change
    if change_type == "NON_BREAKING":
        return "description_changed", None, None, column_name
    return "metadata", None, None, column_name


def classify_schema_changes(
    diffs: list,
) -> tuple[list[SchemaChange], Severity]:
    """Classify contract diff items into PATCH/MINOR/MAJOR schema changes.

    Args:
        diffs: List of ContractDiffItem from contract.diff_contract().

    Returns:
        Tuple of (list of SchemaChange, overall Severity).
    """
    if not diffs:
        return [], Severity.PATCH

    changes: list[SchemaChange] = []
    for diff_item in diffs:
        ct = diff_item.change_type  # BREAKING, NON_BREAKING, INFO
        desc = diff_item.description
        detail_key, old_val, new_val, column_name = _parse_diff_detail(ct, desc)

        severity = _SEVERITY_MAP.get((ct, detail_key), Severity.PATCH)

        # Map detail_key to ChangeType enum
        try:
            change_type = ChangeType(detail_key)
        except ValueError:
            change_type = ChangeType.DESCRIPTION_CHANGED

        changes.append(SchemaChange(
            column_name=column_name,
            change_type=change_type,
            old_value=old_val,
            new_value=new_val,
            classification=severity,
            reason=desc,
        ))

    overall = max(changes, key=lambda c: _SEVERITY_ORDER[c.classification]).classification
    return changes, overall


# ---------------------------------------------------------------------------
# Blast radius
# ---------------------------------------------------------------------------


def compute_blast_radius(
    table_name: str,
    project_root: Path | None = None,
) -> tuple[list[BlastRadiusItem], dict]:
    """Map all downstream dependencies affected by a schema change.

    Walks lineage events, contract consumers, and golden datasets to build
    the full impact surface.

    Args:
        table_name: The modified table (e.g., "consumable.company_financials").
        project_root: Override for project root.

    Returns:
        Tuple of (list of BlastRadiusItem, summary dict for JSON output).
    """
    from brightsmith.config import PROJECT_ROOT
    root = project_root or PROJECT_ROOT

    items: list[BlastRadiusItem] = []
    downstream_tables: list[str] = []
    consumables: list[str] = []
    mcp_tools: list[str] = []
    grounding_docs: list[str] = []
    golden_datasets: list[str] = []

    # 1. Lineage walk: find downstream tables that consume this table as input
    try:
        from brightsmith.infra.lineage import query_downstream_consumers
        events = query_downstream_consumers(table_name, limit=100)
        seen_tables: set[str] = set()
        for event in events:
            output = event.get("output_table", "")
            if output and output != table_name and output not in seen_tables:
                seen_tables.add(output)
                downstream_tables.append(output)
                items.append(BlastRadiusItem("table", output, "direct_consumer"))
    except Exception:
        logger.debug("Could not query lineage for blast radius", exc_info=True)

    # 2. Contract scan: find contracts that reference this table as a source
    contracts_dir = root / "governance" / "data-contracts"
    if contracts_dir.exists():
        for cpath in contracts_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(cpath.read_text())
                sources = data.get("lineage", {}).get("sources", [])
                for src in sources:
                    if src.get("table") == table_name:
                        contract_name = data.get("metadata", {}).get("name", cpath.stem)
                        contract_table = data.get("schema", {}).get("table", "")
                        if contract_table and contract_table != table_name:
                            items.append(BlastRadiusItem("contract", str(cpath.relative_to(root)), "direct_consumer"))
                            if contract_table.startswith("consumable."):
                                consumables.append(contract_name)
                        break
            except Exception:
                continue

    # 3. Golden dataset scan
    golden_dir = root / "governance" / "golden-datasets"
    if golden_dir.exists():
        for gpath in golden_dir.glob("*.json"):
            try:
                data = json.loads(gpath.read_text())
                if data.get("table") == table_name:
                    golden_datasets.append(gpath.stem)
                    items.append(BlastRadiusItem("golden_dataset", str(gpath.relative_to(root)), "direct_consumer"))
            except Exception:
                continue

    # 4. MCP tool scan (from manifest)
    manifest_path = root / "domain" / "manifest.yaml"
    if manifest_path.exists():
        try:
            manifest = yaml.safe_load(manifest_path.read_text())
            mcp_config = manifest.get("pipeline", {}).get("zones", {}).get("mcp", {})
            # Check if any MCP tools reference this table
            tools = mcp_config.get("tools", [])
            for tool in tools:
                tool_sources = tool.get("sources", [])
                if table_name in tool_sources:
                    mcp_tools.append(tool.get("name", "unknown"))
                    items.append(BlastRadiusItem("mcp_tool", tool.get("name", "unknown"), "direct_consumer"))
        except Exception:
            pass

    summary = {
        "downstream_tables": downstream_tables,
        "consumables": consumables,
        "mcp_tools": mcp_tools,
        "grounding_documents": grounding_docs,
        "golden_datasets": golden_datasets,
        "total_affected": len(items),
    }

    return items, summary


# ---------------------------------------------------------------------------
# Decision management
# ---------------------------------------------------------------------------


def _next_decision_id(table_name: str, cab_dir: Path | None = None) -> str:
    """Generate a decision ID from timestamp and table name."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # Normalize table name: consumable.company_financials → company-financials
    table_short = table_name.split(".")[-1].replace("_", "-")
    return f"cab-{timestamp}-{table_short}"


def _load_index(cab_dir: Path) -> dict:
    """Load the decision index, or create empty."""
    index_path = cab_dir / "index.json"
    if index_path.exists():
        return json.loads(index_path.read_text())
    return {"decisions": []}


def _save_index(index: dict, cab_dir: Path) -> None:
    """Save the decision index."""
    cab_dir.mkdir(parents=True, exist_ok=True)
    index_path = cab_dir / "index.json"
    index_path.write_text(json.dumps(index, indent=2) + "\n")


def create_decision(
    spec: str,
    table_name: str,
    changes: list[SchemaChange],
    overall_severity: Severity,
    blast_items: list[BlastRadiusItem],
    blast_summary: dict,
    contract_version_before: str,
    contract_version_after: str,
    schema_diff: dict,
    cab_dir: Path | None = None,
) -> CabDecisionRecord:
    """Create a new CAB decision record.

    Args:
        spec: Spec name that triggered this review.
        table_name: Full table name being modified.
        changes: Classified schema changes.
        overall_severity: Overall classification.
        blast_items: Blast radius items.
        blast_summary: Blast radius summary dict.
        contract_version_before: Current contract version.
        contract_version_after: Proposed new version.
        schema_diff: Schema diff dict.
        cab_dir: Override for CAB decisions directory.

    Returns:
        Created CabDecisionRecord.
    """
    cdir = cab_dir or _cab_dir()
    decision_id = _next_decision_id(table_name, cdir)
    now = datetime.now(timezone.utc).isoformat()

    classification_reasons = [
        {
            "column_name": c.column_name,
            "change_type": c.change_type.value,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "severity": c.classification.value,
            "reason": c.reason,
        }
        for c in changes
    ]

    record = CabDecisionRecord(
        decision_id=decision_id,
        spec=spec,
        table_name=table_name,
        created_at=now,
        classification=overall_severity.value,
        classification_reasons=classification_reasons,
        contract_version_before=contract_version_before,
        contract_version_after=contract_version_after,
        schema_diff=schema_diff,
        blast_radius=blast_summary,
        spec_reference=f"docs/specs/{spec}.md",
    )

    return record


def save_decision(record: CabDecisionRecord, cab_dir: Path | None = None) -> Path:
    """Write decision to disk and append to index.

    Args:
        record: The decision record to save.
        cab_dir: Override for CAB decisions directory.

    Returns:
        Path to the saved decision file.
    """
    cdir = cab_dir or _cab_dir()
    cdir.mkdir(parents=True, exist_ok=True)

    # Save individual decision file
    decision_path = cdir / f"{record.decision_id}.json"
    decision_path.write_text(json.dumps(record.to_dict(), indent=2) + "\n")

    # Append to index
    index = _load_index(cdir)
    index["decisions"].append({
        "decision_id": record.decision_id,
        "timestamp": record.created_at,
        "table": record.table_name,
        "spec": record.spec,
        "classification": record.classification,
        "decision": record.decision,
        "had_human_override": record.human_override is not None,
    })
    _save_index(index, cdir)

    return decision_path


def load_decision(decision_id: str, cab_dir: Path | None = None) -> CabDecisionRecord | None:
    """Load a decision record by ID.

    Args:
        decision_id: The decision ID.
        cab_dir: Override for CAB decisions directory.

    Returns:
        CabDecisionRecord or None if not found.
    """
    cdir = cab_dir or _cab_dir()
    path = cdir / f"{decision_id}.json"
    if not path.exists():
        return None

    data = json.loads(path.read_text())
    return CabDecisionRecord(**{
        k: v for k, v in data.items()
        if k in CabDecisionRecord.__dataclass_fields__
    })


def update_decision(
    decision_id: str,
    decision: Decision,
    decided_by: str,
    notes: str = "",
    rationale: str = "",
    fork: ForkDetails | None = None,
    override: HumanOverride | None = None,
    cab_dir: Path | None = None,
) -> CabDecisionRecord | None:
    """Update a pending decision with the human's choice.

    Args:
        decision_id: The decision ID to update.
        decision: The final decision.
        decided_by: Who decided.
        notes: Optional notes.
        rationale: Agent rationale text.
        fork: Fork details if APPROVED_WITH_FORK.
        override: Human override details if reclassified.
        cab_dir: Override for CAB decisions directory.

    Returns:
        Updated CabDecisionRecord, or None if not found.
    """
    cdir = cab_dir or _cab_dir()
    record = load_decision(decision_id, cdir)
    if record is None:
        return None

    # Guard: do not allow overwriting a finalized decision
    terminal_states = {Decision.APPROVED.value, Decision.APPROVED_WITH_FORK.value, Decision.REJECTED.value}
    if record.decision in terminal_states:
        logger.warning(
            "Cannot update decision %s — already finalized as %s by %s at %s",
            decision_id, record.decision, record.decided_by, record.decided_at,
        )
        return None

    record.decision = decision.value
    record.decided_by = decided_by
    record.decided_at = datetime.now(timezone.utc).isoformat()
    record.notes = notes
    if rationale:
        record.rationale = rationale
    if fork:
        record.fork = asdict(fork)
    if override:
        record.human_override = asdict(override)

    # Overwrite decision file
    decision_path = cdir / f"{decision_id}.json"
    decision_path.write_text(json.dumps(record.to_dict(), indent=2) + "\n")

    # Update index entry
    index = _load_index(cdir)
    for entry in index["decisions"]:
        if entry["decision_id"] == decision_id:
            entry["decision"] = decision.value
            entry["had_human_override"] = override is not None
            break
    _save_index(index, cdir)

    return record


# ---------------------------------------------------------------------------
# Fork proposal
# ---------------------------------------------------------------------------


def propose_fork(
    record: CabDecisionRecord,
    deprecation_days: int = 90,
) -> ForkDetails:
    """Generate fork details for a MAJOR change.

    Args:
        record: The CAB decision record.
        deprecation_days: Days until v1 is archived (default 90).

    Returns:
        ForkDetails with v2 naming, migration spec path, and timeline.
    """
    now = datetime.now(timezone.utc)
    deprecated_at = now.strftime("%Y-%m-%d")
    archive_after = (now + timedelta(days=deprecation_days)).strftime("%Y-%m-%d")

    v1_table = record.table_name
    # Generate v2 table name, handling iterative forks (_v2 → _v3 → ...)
    parts = v1_table.split(".")
    base_name = parts[-1] if parts else record.table_name
    namespace = parts[0] if len(parts) == 2 else ""

    version_match = re.search(r"_v(\d+)$", base_name)
    if version_match:
        current_version = int(version_match.group(1))
        base_without_version = base_name[:version_match.start()]
        v2_name = f"{base_without_version}_v{current_version + 1}"
    else:
        v2_name = f"{base_name}_v2"

    v2_table = f"{namespace}.{v2_name}" if namespace else v2_name

    # Migration spec path — use base table name, not v2 name
    base_table = base_name.replace("_", "-") if base_name else record.table_name
    v2_short = v2_name.replace("_", "-")
    migration_spec = f"docs/specs/{base_table}-to-{v2_short}-migration.md"

    return ForkDetails(
        v1_table=v1_table,
        v2_table=v2_table,
        migration_spec_path=migration_spec,
        deprecation_timeline_days=deprecation_days,
        deprecated_at=deprecated_at,
        archive_after=archive_after,
    )


# ---------------------------------------------------------------------------
# Deprecation registry
# ---------------------------------------------------------------------------


def register_deprecation(
    table_name: str,
    successor: str,
    deprecated_at: str,
    archive_after: str,
    decision_id: str,
    cab_dir: Path | None = None,
) -> None:
    """Add or update an entry in the deprecation registry.

    Args:
        table_name: The table being deprecated.
        successor: The successor table name.
        deprecated_at: ISO date of deprecation.
        archive_after: ISO date after which to archive.
        decision_id: CAB decision ID that triggered this.
        cab_dir: Override for CAB decisions directory.
    """
    cdir = cab_dir or _cab_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    registry_path = cdir / "deprecations.json"

    if registry_path.exists():
        registry = json.loads(registry_path.read_text())
    else:
        registry = {"active_deprecations": []}

    # Check if entry already exists
    for entry in registry["active_deprecations"]:
        if entry["table"] == table_name:
            entry["successor"] = successor
            entry["deprecated_at"] = deprecated_at
            entry["archive_after"] = archive_after
            entry["cab_decision_id"] = decision_id
            entry["status"] = "DEPRECATED"
            break
    else:
        registry["active_deprecations"].append({
            "table": table_name,
            "successor": successor,
            "deprecated_at": deprecated_at,
            "archive_after": archive_after,
            "status": "DEPRECATED",
            "cab_decision_id": decision_id,
            "consumers_migrated": 0,
            "consumers_remaining": 0,
        })

    registry_path.write_text(json.dumps(registry, indent=2) + "\n")


def load_deprecations(cab_dir: Path | None = None) -> list[dict]:
    """Load active deprecations with computed days_remaining.

    Returns:
        List of deprecation entries with days_remaining computed.
    """
    cdir = cab_dir or _cab_dir()
    registry_path = cdir / "deprecations.json"
    if not registry_path.exists():
        return []

    registry = json.loads(registry_path.read_text())
    today = datetime.now(timezone.utc).date()

    for entry in registry.get("active_deprecations", []):
        try:
            archive_date = datetime.strptime(entry["archive_after"], "%Y-%m-%d").date()
            entry["days_remaining"] = max(0, (archive_date - today).days)
            if entry["days_remaining"] == 0:
                entry["status"] = "ARCHIVED"
        except (KeyError, ValueError):
            entry["days_remaining"] = -1

    return registry.get("active_deprecations", [])


# ---------------------------------------------------------------------------
# Schema diff builder
# ---------------------------------------------------------------------------


def build_schema_diff(changes: list[SchemaChange]) -> dict:
    """Build the schema_diff dict from classified changes.

    Args:
        changes: List of SchemaChange from classification.

    Returns:
        Dict with added_columns, removed_columns, changed_columns, unchanged_columns.
    """
    added = []
    removed = []
    changed = []

    for c in changes:
        if c.change_type == ChangeType.ADDED:
            added.append({"name": c.column_name, "type": c.new_value, "nullable": True})
        elif c.change_type == ChangeType.REMOVED:
            removed.append({"name": c.column_name, "type": c.old_value, "nullable": True})
        elif c.change_type == ChangeType.TYPE_CHANGED:
            changed.append({
                "name": c.column_name,
                "from_type": c.old_value,
                "to_type": c.new_value,
            })
        elif c.change_type == ChangeType.NULLABLE_CHANGED:
            changed.append({
                "name": c.column_name,
                "change": "nullable_changed",
            })

    return {
        "added_columns": added,
        "removed_columns": removed,
        "changed_columns": changed,
        "unchanged_columns": [],  # Populated by caller from contract schema
    }


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------


def detect_schema_modification(table_name: str, contracts_dir: Path | None = None) -> bool:
    """Check whether a table has an existing active data contract.

    This is the trigger condition for the CAB agent. If no active contract
    exists, the table is new and the CAB step should be skipped.

    Args:
        table_name: Full table name (e.g., "consumable.company_financials").
        contracts_dir: Override for contracts directory.

    Returns:
        True if an active contract exists for this table.
    """
    from brightsmith.infra.contract import list_contracts

    contracts = list_contracts(contracts_dir)
    for c in contracts:
        if c.get("table") == table_name and c.get("status") == "active":
            return True
    return False


# ---------------------------------------------------------------------------
# Full review workflow
# ---------------------------------------------------------------------------


def review(
    spec: str,
    table_name: str,
    contracts_dir: Path | None = None,
    cab_dir: Path | None = None,
    project_root: Path | None = None,
) -> CabDecisionRecord | None:
    """Run a full CAB review for a schema modification.

    1. Diffs the contract against the live table
    2. Classifies changes
    3. Computes blast radius
    4. Creates and saves a decision record

    Args:
        spec: Spec name.
        table_name: Full table name.
        contracts_dir: Override for contracts directory.
        cab_dir: Override for CAB decisions directory.
        project_root: Override for project root.

    Returns:
        CabDecisionRecord, or None if no changes detected.
    """
    from brightsmith.infra.contract import bump_version, diff_contract, load_contract

    # Derive contract name from table name
    contract_name = table_name.split(".")[-1].replace("_", "-")

    # Get current contract
    contract = load_contract(contract_name, contracts_dir)
    if contract is None:
        logger.info("No contract found for %s — skipping CAB review", table_name)
        return None

    current_version = contract.get("metadata", {}).get("version", "1.0.0")

    # Diff against live table
    diffs = diff_contract(contract_name, contracts_dir)
    if not diffs:
        logger.info("No schema changes detected for %s", table_name)
        return None

    # Filter out INFO-only diffs (non-actionable)
    actionable = [d for d in diffs if d.change_type != "INFO"]
    if not actionable:
        return None

    # Classify
    changes, overall = classify_schema_changes(actionable)

    # Compute blast radius
    blast_items, blast_summary = compute_blast_radius(table_name, project_root)

    # Determine version bump
    if overall == Severity.MAJOR:
        bump_type = "BREAKING"
    elif overall == Severity.MINOR:
        bump_type = "NON_BREAKING"
    else:
        bump_type = "PATCH"
    new_version = bump_version(current_version, bump_type)

    # Build schema diff
    schema_diff = build_schema_diff(changes)

    # Create decision
    record = create_decision(
        spec=spec,
        table_name=table_name,
        changes=changes,
        overall_severity=overall,
        blast_items=blast_items,
        blast_summary=blast_summary,
        contract_version_before=current_version,
        contract_version_after=new_version,
        schema_diff=schema_diff,
        cab_dir=cab_dir,
    )

    # Auto-approve PATCH
    from brightsmith.config import REQUIRE_HUMAN_APPROVAL
    if overall == Severity.PATCH:
        record.decision = Decision.APPROVED.value
        record.decided_by = "auto:cab-agent"
        record.decided_at = datetime.now(timezone.utc).isoformat()
        record.rationale = "PATCH change (metadata only) — auto-approved."
    elif overall == Severity.MINOR and not REQUIRE_HUMAN_APPROVAL:
        record.decision = Decision.APPROVED.value
        record.decided_by = "auto:cab-agent"
        record.decided_at = datetime.now(timezone.utc).isoformat()
        record.rationale = "MINOR change — auto-approved (REQUIRE_HUMAN_APPROVAL=False)."
    # MAJOR and MINOR+REQUIRE_HUMAN stay PENDING

    # Save
    save_decision(record, cab_dir)

    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for CAB operations."""
    parser = argparse.ArgumentParser(description="Brightsmith Change Approval Board (CAB)")
    subparsers = parser.add_subparsers(dest="command")

    # review
    rev_p = subparsers.add_parser("review", help="Run CAB review for a schema change")
    rev_p.add_argument("--spec", required=True, help="Spec name")
    rev_p.add_argument("--table", required=True, help="Full table name (namespace.table)")

    # status
    stat_p = subparsers.add_parser("status", help="Check a decision status")
    stat_p.add_argument("--decision", required=True, help="Decision ID")

    # approve
    appr_p = subparsers.add_parser("approve", help="Record approval decision")
    appr_p.add_argument("--decision", required=True, help="Decision ID")
    appr_p.add_argument("--by", required=True, help="Who decided")
    appr_p.add_argument("--fork", action="store_true", help="Approve with fork")
    appr_p.add_argument("--notes", default="", help="Optional notes")

    # deprecations
    subparsers.add_parser("deprecations", help="List active deprecations")

    # history
    hist_p = subparsers.add_parser("history", help="List decisions for a table")
    hist_p.add_argument("--table", required=True, help="Full table name")

    args = parser.parse_args()

    if args.command == "review":
        _cmd_review(args)
    elif args.command == "status":
        _cmd_status(args)
    elif args.command == "approve":
        _cmd_approve(args)
    elif args.command == "deprecations":
        _cmd_deprecations()
    elif args.command == "history":
        _cmd_history(args)
    else:
        parser.print_help()


def _cmd_review(args: argparse.Namespace) -> None:
    record = review(args.spec, args.table)
    if record is None:
        print("No schema changes detected — CAB review not required.")
        return
    print(f"CAB Decision: {record.decision_id}")
    print(f"Classification: {record.classification}")
    print(f"Decision: {record.decision}")
    print(f"Blast radius: {record.blast_radius.get('total_affected', 0)} affected")
    if record.decision == Decision.PENDING.value:
        print("Action required: human approval needed.")


def _cmd_status(args: argparse.Namespace) -> None:
    record = load_decision(args.decision)
    if record is None:
        print(f"Decision not found: {args.decision}", file=sys.stderr)
        sys.exit(1)
    print(f"Decision: {record.decision_id}")
    print(f"Table: {record.table_name}")
    print(f"Classification: {record.classification}")
    print(f"Decision: {record.decision}")
    if record.decided_by:
        print(f"Decided by: {record.decided_by} at {record.decided_at}")


def _cmd_approve(args: argparse.Namespace) -> None:
    decision_type = Decision.APPROVED_WITH_FORK if args.fork else Decision.APPROVED
    record = load_decision(args.decision)
    if record is None:
        print(f"Decision not found: {args.decision}", file=sys.stderr)
        sys.exit(1)

    fork_details = None
    if args.fork:
        fork_details = propose_fork(record)
        register_deprecation(
            table_name=record.table_name,
            successor=fork_details.v2_table,
            deprecated_at=fork_details.deprecated_at,
            archive_after=fork_details.archive_after,
            decision_id=record.decision_id,
        )

    updated = update_decision(
        decision_id=args.decision,
        decision=decision_type,
        decided_by=getattr(args, "by"),
        notes=args.notes,
        fork=fork_details,
    )

    if updated:
        print(f"Updated: {updated.decision_id} → {updated.decision}")
        if fork_details:
            print(f"Fork: {fork_details.v1_table} → {fork_details.v2_table}")
            print(f"Migration spec: {fork_details.migration_spec_path}")
            print(f"Archive after: {fork_details.archive_after}")


def _cmd_deprecations() -> None:
    deps = load_deprecations()
    if not deps:
        print("No active deprecations.")
        return
    print(f"{'Table':<40} {'Successor':<40} {'Days Left':<10} {'Status':<12}")
    print("-" * 102)
    for d in deps:
        print(f"{d['table']:<40} {d['successor']:<40} {d.get('days_remaining', '?'):<10} {d['status']:<12}")


def _cmd_history(args: argparse.Namespace) -> None:
    cdir = _cab_dir()
    index = _load_index(cdir)
    matches = [d for d in index["decisions"] if d["table"] == args.table]
    if not matches:
        print(f"No CAB decisions found for {args.table}")
        return
    print(f"{'ID':<45} {'Classification':<15} {'Decision':<20} {'Date':<25}")
    print("-" * 105)
    for d in matches:
        print(f"{d['decision_id']:<45} {d['classification']:<15} {d['decision']:<20} {d['timestamp']:<25}")


if __name__ == "__main__":
    main()
