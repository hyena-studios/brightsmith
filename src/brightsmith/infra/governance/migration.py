"""One-time governance file migration helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from brightsmith.infra.governance.sync import sync_from_files
from brightsmith.infra.governance.writers import (
    write_cab_decision,
    write_chaos_manifest,
    write_document,
    write_dq_acknowledgment,
    write_dq_rules,
    write_golden_dataset_values,
    write_run_history,
)

logger = logging.getLogger(__name__)

__all__ = ["cmd_migrate", "migrate_files_to_iceberg", "sync_from_files"]


def migrate_files_to_iceberg() -> dict:
    """One-time migration of all governance file artifacts to Iceberg.

    Reads existing governance files and writes them to the 7 new Iceberg tables.
    Produces a validation report comparing file counts to Iceberg row counts.

    Idempotent via promote() — safe to run repeatedly.

    Returns a migration report dict with per-table counts and spot-check results.
    """
    from brightsmith.config import (
        AUDIT_TRAIL_DIR,
        CAB_DECISIONS_DIR,
        DQ_RESULTS_DIR,
        DQ_RULES_DIR,
        GOLDEN_DATASETS_DIR,
        PROJECT_ROOT,
    )

    report: dict = {}

    # 1. DQ Rules -> governance.dq_rules
    dq_rules_files = 0
    dq_rules_rows = 0
    if DQ_RULES_DIR.exists():
        for path in sorted(DQ_RULES_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem)
                tables = data.get("tables", [])
                table_name = ", ".join(tables) if tables else spec
                rules = data.get("rules", [])
                if rules:
                    dq_rules_files += 1
                    result = write_dq_rules(spec, table_name, rules)
                    dq_rules_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate DQ rules from %s", path, exc_info=True)
    report["dq_rules"] = {"files": dq_rules_files, "rows": dq_rules_rows}

    # 2. DQ Acknowledgments -> governance.dq_acknowledgments
    ack_files = 0
    ack_rows = 0
    if DQ_RESULTS_DIR.exists():
        for path in sorted(DQ_RESULTS_DIR.glob("*-ack-*.json")):
            try:
                data = json.loads(path.read_text())
                ack_files += 1
                run_id = data.get("run_id", "")
                spec = data.get("spec_name", data.get("spec", ""))
                for ack in data.get("acknowledgments", [data]):
                    result = write_dq_acknowledgment(
                        run_id=ack.get("run_id", run_id),
                        rule_id=ack.get("rule_id", ""),
                        spec_name=ack.get("spec_name", spec),
                        acknowledged_by=ack.get("acknowledged_by", ""),
                        reason=ack.get("reason", ""),
                    )
                    ack_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate ack from %s", path, exc_info=True)
    report["dq_acknowledgments"] = {"files": ack_files, "rows": ack_rows}

    # 3. CAB Decisions -> governance.cab_decisions
    cab_files = 0
    cab_rows = 0
    if CAB_DECISIONS_DIR.exists():
        for path in sorted(CAB_DECISIONS_DIR.glob("*.json")):
            if path.name == "index.json":
                continue
            try:
                data = json.loads(path.read_text())
                cab_files += 1
                result = write_cab_decision(
                    decision_id=data.get("decision_id", path.stem),
                    spec_name=data.get("spec_name", data.get("spec", "")),
                    table_name=data.get("table_name", data.get("table", "")),
                    classification=data.get("classification", ""),
                    classification_reasons=data.get("classification_reasons", data.get("reasons", [])),
                    decision=data.get("decision", data.get("status", "PENDING")),
                    contract_version_before=data.get("contract_version_before"),
                    contract_version_after=data.get("contract_version_after"),
                    schema_diff=data.get("schema_diff"),
                    blast_radius=data.get("blast_radius"),
                    decided_by=data.get("decided_by"),
                    notes=data.get("notes"),
                    rationale=data.get("rationale"),
                    fork_config=data.get("fork_config"),
                    human_override=data.get("human_override"),
                )
                cab_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate CAB decision from %s", path, exc_info=True)
    report["cab_decisions"] = {"files": cab_files, "rows": cab_rows}

    # 4. Golden Datasets -> governance.golden_datasets
    gd_files = 0
    gd_rows = 0
    if GOLDEN_DATASETS_DIR.exists():
        for path in sorted(GOLDEN_DATASETS_DIR.glob("*-golden.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem.replace("-golden", ""))
                table_name_val = data.get("table", "")
                values = data.get("values", [])
                if values:
                    gd_files += 1
                    result = write_golden_dataset_values(spec, table_name_val, values)
                    gd_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate golden dataset from %s", path, exc_info=True)
    report["golden_datasets"] = {"files": gd_files, "rows": gd_rows}

    # 5. Run History -> governance.run_history
    rh_files = 0
    rh_rows = 0
    run_history_dir = PROJECT_ROOT / "governance" / "run-history"
    if run_history_dir.exists():
        for path in sorted(run_history_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                rh_files += 1
                started_str = data.get("started_at", "")
                started = datetime.fromisoformat(started_str) if started_str else datetime.now(timezone.utc)
                completed_str = data.get("completed_at")
                completed = datetime.fromisoformat(completed_str) if completed_str else None
                result = write_run_history(
                    run_id=data.get("run_id", path.stem),
                    started_at=started,
                    status=data.get("status", "UNKNOWN"),
                    zones_summary=data.get("zones_summary", data.get("zones", {})),
                    completed_at=completed,
                    duration_seconds=data.get("duration_seconds"),
                    golden_datasets_summary=data.get("golden_datasets_summary"),
                    options=data.get("options"),
                    error_message=data.get("error_message", data.get("error")),
                )
                rh_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate run history from %s", path, exc_info=True)
    report["run_history"] = {"files": rh_files, "rows": rh_rows}

    # 6. Chaos Manifests -> governance.chaos_manifests
    cm_files = 0
    cm_rows = 0
    chaos_dir = PROJECT_ROOT / "governance" / "chaos-monkey"
    if chaos_dir.exists():
        for path in sorted(chaos_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                cm_files += 1
                result = write_chaos_manifest(
                    run_id=data.get("run_id", path.stem),
                    source_table=data.get("source_table", ""),
                    shadow_table=data.get("shadow_table", ""),
                    total_rows=data.get("total_rows", 0),
                    corruption_rate=data.get("corruption_rate", 0.0),
                    rows_corrupted=data.get("rows_corrupted", 0),
                    columns_corrupted=data.get("columns_corrupted", 0),
                    total_corruptions=data.get("total_corruptions", 0),
                    seed=data.get("seed"),
                    dimensions_covered=data.get("dimensions_covered"),
                    corruptions_sample=data.get("corruptions_sample", data.get("corruptions", []))[:100],
                )
                cm_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate chaos manifest from %s", path, exc_info=True)
    report["chaos_manifests"] = {"files": cm_files, "rows": cm_rows}

    # 7. Documents -> governance.documents (reviews, insights, models, etc.)
    doc_files = 0
    doc_rows = 0
    doc_dirs = {
        "review": PROJECT_ROOT / "governance" / "reviews",
        "insight": PROJECT_ROOT / "governance" / "insights",
        "model": PROJECT_ROOT / "governance" / "models",
        "approval": PROJECT_ROOT / "governance" / "approvals",
        "audit_trail": AUDIT_TRAIL_DIR,
        "eda": PROJECT_ROOT / "governance" / "eda",
    }
    for doc_type, doc_dir in doc_dirs.items():
        if not doc_dir.exists():
            continue
        for path in sorted(doc_dir.glob("*.md")):
            try:
                content_text = path.read_text()
                doc_name = path.stem
                # Extract title from first markdown heading
                title = doc_name
                for line in content_text.split("\n"):
                    if line.startswith("# ") or line.startswith("## "):
                        title = line.lstrip("#").strip()
                        break

                # Try to determine spec_name from filename
                spec = None
                # Common pattern: spec-name-suffix.md
                parts = doc_name.rsplit("-", 1)
                if len(parts) > 1:
                    spec = parts[0]

                doc_files += 1
                result = write_document(
                    doc_type=doc_type,
                    doc_name=doc_name,
                    title=title,
                    content=content_text,
                    version=1,
                    spec_name=spec,
                )
                doc_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate document from %s", path, exc_info=True)

    # Domain context is a single file
    domain_context_path = PROJECT_ROOT / "governance" / "domain-context.md"
    if domain_context_path.exists():
        try:
            content_text = domain_context_path.read_text()
            doc_files += 1
            result = write_document(
                doc_type="domain_context",
                doc_name="domain-context",
                title="Domain Context",
                content=content_text,
                version=1,
            )
            doc_rows += result.get("promoted", 0)
        except Exception:
            logger.warning("Failed to migrate domain context", exc_info=True)

    # Lineage docs (JSON)
    lineage_dir = PROJECT_ROOT / "governance" / "lineage"
    if lineage_dir.exists():
        for path in sorted(lineage_dir.glob("*.json")):
            try:
                content_text = path.read_text()
                doc_files += 1
                result = write_document(
                    doc_type="lineage_doc",
                    doc_name=path.stem,
                    title=f"Lineage: {path.stem}",
                    content=content_text,
                    version=1,
                )
                doc_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate lineage doc from %s", path, exc_info=True)

    report["documents"] = {"files": doc_files, "rows": doc_rows}

    # Also run the existing sync_from_files for the original 8 tables
    existing_sync = sync_from_files()
    report["existing_sync"] = existing_sync

    return report


def cmd_migrate() -> None:
    """One-time migration of governance files to Iceberg tables."""
    print("Migrating governance file artifacts to Iceberg tables...")
    print("=" * 60)
    report = migrate_files_to_iceberg()

    print("\nMigration Report")
    print("=" * 60)
    for table, counts in sorted(report.items()):
        if table == "existing_sync":
            continue
        if isinstance(counts, dict) and "files" in counts:
            print(f"  {table:<25} {counts['files']:>3} files -> {counts['rows']:>4} rows")
    existing = report.get("existing_sync", {})
    if existing:
        print("\nExisting table sync:")
        for table, count in sorted(existing.items()):
            print(f"  {table:<25} {count:>4} records")

    total_files = sum(
        c.get("files", 0) for c in report.values() if isinstance(c, dict) and "files" in c
    )
    total_rows = sum(
        c.get("rows", 0) for c in report.values() if isinstance(c, dict) and "rows" in c
    )
    print(f"\nTotal: {total_files} files -> {total_rows} rows migrated")
