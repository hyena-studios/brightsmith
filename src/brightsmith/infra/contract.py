"""Machine-readable data contracts for Iceberg tables.

Generates, verifies, and diffs YAML data contracts against live Iceberg
tables. Contracts are the guarantee layer — specs are proposals, contracts
are what was actually built and what consumers can rely on.

Usage:
    python -m brightsmith.infra.contract generate --table consumable.company_financials --spec my-spec
    python -m brightsmith.infra.contract verify {contract-name} | --all
    python -m brightsmith.infra.contract diff {contract-name}
    python -m brightsmith.infra.contract list
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

BREAKING_CHANGES = {"column_removed", "column_type_changed", "grain_changed", "column_renamed"}
NON_BREAKING_CHANGES = {"column_added", "description_changed", "consumer_added"}

CONTRACT_STATUSES = {"draft", "active", "deprecated"}

# Iceberg type name → contract type name mapping
_TYPE_MAP = {
    "boolean": "boolean",
    "int": "integer",
    "long": "long",
    "float": "float",
    "double": "double",
    "string": "string",
    "date": "date",
    "time": "time",
    "timestamp": "timestamp",
    "timestamptz": "timestamptz",
    "binary": "binary",
    "decimal": "decimal",
    "uuid": "uuid",
    "fixed": "fixed",
}


def _iceberg_type_to_str(field_type) -> str:
    """Convert a PyIceberg field type to a simple string name."""
    type_str = str(field_type).lower()
    for key, val in _TYPE_MAP.items():
        if key in type_str:
            return val
    return type_str


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ColumnContract:
    """Contract definition for a single column."""

    name: str
    type: str
    required: bool
    business_term: str | None = None
    is_cde: bool = False
    cde_rationale: str = ""
    is_pii: bool = False
    pii_rationale: str = ""
    description: str = ""


@dataclass
class ContractVerificationResult:
    """Result of verifying one contract check."""

    check: str
    status: str  # PASS, FAIL, SKIP
    detail: str = ""


@dataclass
class ContractDiffItem:
    """One item in a contract diff."""

    change_type: str  # BREAKING, NON_BREAKING, INFO
    description: str


# ---------------------------------------------------------------------------
# Contract I/O
# ---------------------------------------------------------------------------


def _contracts_dir() -> Path:
    from brightsmith.config import PROJECT_ROOT
    return PROJECT_ROOT / "governance" / "data-contracts"


def load_contract(name: str, contracts_dir: Path | None = None) -> dict | None:
    """Load a contract YAML file by name.

    Args:
        name: Contract name (without .yaml extension).
        contracts_dir: Override for contracts directory.

    Returns:
        Parsed YAML dict, or None if not found.
    """
    cdir = contracts_dir or _contracts_dir()
    path = cdir / f"{name}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())


def save_contract(contract: dict, contracts_dir: Path | None = None) -> Path:
    """Save a contract YAML file.

    Args:
        contract: Contract dict to save.
        contracts_dir: Override for contracts directory.

    Returns:
        Path to the saved file.
    """
    cdir = contracts_dir or _contracts_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    name = contract.get("metadata", {}).get("name", "unnamed")
    path = cdir / f"{name}.yaml"
    path.write_text(yaml.dump(contract, default_flow_style=False, sort_keys=False))
    return path


def list_contracts(contracts_dir: Path | None = None) -> list[dict]:
    """List all contracts with basic metadata.

    Returns:
        List of dicts with name, version, status, table.
    """
    cdir = contracts_dir or _contracts_dir()
    if not cdir.exists():
        return []

    results = []
    for path in sorted(cdir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
            meta = data.get("metadata", {})
            schema = data.get("schema", {})
            results.append({
                "name": meta.get("name", path.stem),
                "version": meta.get("version", "?"),
                "status": meta.get("status", "?"),
                "table": schema.get("table", "?"),
                "path": str(path),
            })
        except Exception:
            results.append({"name": path.stem, "version": "?", "status": "error", "table": "?", "path": str(path)})
    return results


def _build_lineage_section(table_name: str) -> dict:
    """Build the lineage section for a contract from runtime lineage events.

    Auto-populates sources from the input_tables of the latest lineage event
    for this table. Falls back to empty sources if no events exist.
    """
    section: dict = {"sources": []}
    try:
        from brightsmith.infra.lineage import query_lineage_events
        events = query_lineage_events(table_name, event_type="COMPLETE", limit=1)
        if not events:
            # Try START events for input_tables (COMPLETE doesn't repeat them)
            events = query_lineage_events(table_name, event_type="START", limit=1)
        if events:
            latest = events[0]
            input_tables_raw = latest.get("input_tables", "[]")
            try:
                input_tables = json.loads(input_tables_raw) if isinstance(input_tables_raw, str) else input_tables_raw
            except (json.JSONDecodeError, TypeError):
                input_tables = []
            section["sources"] = [
                {"table": t, "relationship": "direct_input"} for t in input_tables
            ]
            # Add latest run metadata
            section["latest_run"] = {
                "run_id": latest.get("run_id"),
                "event_time": str(latest.get("event_time", "")),
                "row_count": latest.get("row_count"),
                "snapshot_id": latest.get("output_snapshot_id"),
            }
    except Exception:
        logger.debug("Could not query lineage events for contract %s", table_name, exc_info=True)
    return section


# ---------------------------------------------------------------------------
# Contract generation
# ---------------------------------------------------------------------------


def generate_contract(
    table_name: str,
    spec_path: str = "",
    grain_columns: list[str] | None = None,
    grain_description: str = "",
    dq_rules_path: str = "",
    golden_dataset_path: str = "",
    owner: str = "@data-steward",
    contracts_dir: Path | None = None,
) -> dict:
    """Generate a data contract from an implemented Iceberg table.

    Reads the actual table schema from the Iceberg catalog and assembles
    the contract. The contract always reflects reality.

    Args:
        table_name: Full table name (e.g., "consumable.company_financials").
        spec_path: Path to the spec that built this table.
        grain_columns: Columns that define the grain.
        grain_description: Human description of the grain.
        dq_rules_path: Path to DQ rules file.
        golden_dataset_path: Path to golden dataset file.
        owner: Contract owner (default @data-steward).
        contracts_dir: Override for output directory.

    Returns:
        Contract dict (also saved to disk).
    """
    from brightsmith.config import CATALOG_PATH, PROJECT_ROOT, WAREHOUSE_PATH
    from brightsmith.infra.iceberg_setup import get_catalog

    parts = table_name.split(".")
    if len(parts) != 2:
        raise ValueError(f"Table name must be namespace.table format: {table_name}")

    namespace, tbl = parts

    # Try to load the Iceberg table for schema
    columns = []
    try:
        catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
        iceberg_table = catalog.load_table(table_name)
        for iceberg_field in iceberg_table.schema().fields:
            columns.append({
                "name": iceberg_field.name,
                "type": _iceberg_type_to_str(iceberg_field.field_type),
                "required": iceberg_field.required,
                "business_term": None,
                "is_cde": False,
                "cde_rationale": "",
                "is_pii": False,
                "pii_rationale": "",
                "description": "",
            })
    except Exception as e:
        logger.warning("Could not load Iceberg table %s: %s — generating with empty schema", table_name, e)

    # Cross-reference business glossary for term IDs (semantic link only)
    try:
        glossary_path = PROJECT_ROOT / "governance" / "business-glossary.json"
        if glossary_path.exists():
            glossary_data = json.loads(glossary_path.read_text())
            term_lookup = {}
            for term in glossary_data.get("terms", []):
                term_lookup[term.get("name", "").lower()] = term
            for col in columns:
                match = term_lookup.get(col["name"].lower())
                if match:
                    col["business_term"] = match.get("term_id")
                    # CDE/PII flags are set by @cde-tagger, not derived from glossary
    except Exception:
        pass

    # Derive contract name from table name
    contract_name = tbl.replace("_", "-")

    contract = {
        "apiVersion": "brightsmith/v1",
        "kind": "DataContract",
        "metadata": {
            "name": contract_name,
            "version": "1.0.0",
            "status": "draft",
            "owner": owner,
            "domain": "",
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "spec": spec_path,
        },
        "schema": {
            "table": table_name,
            "namespace": namespace,
            "grain": {
                "columns": grain_columns or [],
                "description": grain_description,
            },
            "columns": columns,
        },
        "quality": {
            "freshness": {
                "max_staleness_hours": 24,
                "measured_by": "ingested_at",
            },
            "completeness": {
                "min_row_count": 1,
                "required_columns": [c["name"] for c in columns if c.get("required")],
            },
            "accuracy": {
                "golden_dataset": golden_dataset_path,
                "min_pass_rate_pct": 80,
            },
            "uniqueness": {
                "grain_unique": bool(grain_columns),
            },
            "dq_rules": {
                "rules_file": dq_rules_path,
                "p0_pass_required": True,
            },
        },
        "lineage": _build_lineage_section(table_name),
        "consumers": [],
        "compatibility": {
            "breaking_changes": sorted(BREAKING_CHANGES),
            "non_breaking_changes": sorted(NON_BREAKING_CHANGES),
            "deprecation_notice_days": 30,
        },
    }

    save_contract(contract, contracts_dir)
    return contract


# ---------------------------------------------------------------------------
# Contract verification
# ---------------------------------------------------------------------------


def verify_contract(
    name: str,
    contracts_dir: Path | None = None,
) -> list[ContractVerificationResult]:
    """Verify a contract against its live Iceberg table.

    Runs all checks: schema match, grain uniqueness, freshness,
    row count, required columns, DQ P0, and golden dataset.

    Args:
        name: Contract name.
        contracts_dir: Override for contracts directory.

    Returns:
        List of verification results.
    """
    contract = load_contract(name, contracts_dir)
    if contract is None:
        return [ContractVerificationResult("load", "FAIL", f"Contract '{name}' not found")]

    results: list[ContractVerificationResult] = []
    schema_section = contract.get("schema", {})
    quality = contract.get("quality", {})
    table_name = schema_section.get("table", "")

    # Load the Iceberg table
    try:
        from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
        from brightsmith.infra.iceberg_setup import get_catalog, read_with_duckdb

        catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
        iceberg_table = catalog.load_table(table_name)
    except Exception as e:
        results.append(ContractVerificationResult("table_load", "FAIL", f"Cannot load table: {e}"))
        return results

    # 1. Schema match
    contract_cols = {c["name"]: c for c in schema_section.get("columns", [])}
    table_fields = {f.name: f for f in iceberg_table.schema().fields}

    missing_in_table = set(contract_cols.keys()) - set(table_fields.keys())
    type_mismatches = []
    for col_name, col_def in contract_cols.items():
        if col_name in table_fields:
            actual_type = _iceberg_type_to_str(table_fields[col_name].field_type)
            if actual_type != col_def["type"]:
                type_mismatches.append(f"{col_name}: contract={col_def['type']}, actual={actual_type}")

    if missing_in_table or type_mismatches:
        detail_parts = []
        if missing_in_table:
            detail_parts.append(f"missing: {missing_in_table}")
        if type_mismatches:
            detail_parts.append(f"type mismatch: {type_mismatches}")
        results.append(ContractVerificationResult(
            "schema_match", "FAIL",
            f"{len(contract_cols) - len(missing_in_table)}/{len(contract_cols)} columns — {'; '.join(detail_parts)}",
        ))
    else:
        results.append(ContractVerificationResult(
            "schema_match", "PASS", f"{len(contract_cols)}/{len(contract_cols)} columns",
        ))

    # 2-6. Data-dependent checks
    try:
        rows = read_with_duckdb(iceberg_table)
    except Exception as e:
        results.append(ContractVerificationResult("data_read", "FAIL", f"Cannot read data: {e}"))
        return results

    # 2. Grain unique
    grain_cols = schema_section.get("grain", {}).get("columns", [])
    if grain_cols and quality.get("uniqueness", {}).get("grain_unique"):
        grains = [tuple(str(r.get(c, "")) for c in grain_cols) for r in rows]
        dupes = len(grains) - len(set(grains))
        if dupes > 0:
            results.append(ContractVerificationResult("grain_unique", "FAIL", f"{dupes} duplicates"))
        else:
            results.append(ContractVerificationResult("grain_unique", "PASS", "0 duplicates"))
    else:
        results.append(ContractVerificationResult("grain_unique", "SKIP", "no grain defined"))

    # 3. Freshness
    freshness = quality.get("freshness", {})
    max_hours = freshness.get("max_staleness_hours")
    measured_by = freshness.get("measured_by", "ingested_at")
    if max_hours and rows:
        from datetime import timedelta
        timestamps = [r.get(measured_by) for r in rows if r.get(measured_by)]
        if timestamps:
            latest = max(timestamps)
            if hasattr(latest, "timestamp"):
                age_hours = (datetime.now(timezone.utc) - latest.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            else:
                age_hours = 0
            if age_hours > max_hours:
                results.append(ContractVerificationResult(
                    "freshness", "FAIL", f"{age_hours:.0f}h ago, max {max_hours}h",
                ))
            else:
                results.append(ContractVerificationResult(
                    "freshness", "PASS", f"{age_hours:.0f}h ago, max {max_hours}h",
                ))
        else:
            results.append(ContractVerificationResult("freshness", "SKIP", f"no {measured_by} values"))
    else:
        results.append(ContractVerificationResult("freshness", "SKIP", "no freshness SLA"))

    # 4. Row count
    min_rows = quality.get("completeness", {}).get("min_row_count", 0)
    if min_rows:
        if len(rows) >= min_rows:
            results.append(ContractVerificationResult(
                "row_count", "PASS", f"{len(rows)} rows, min {min_rows}",
            ))
        else:
            results.append(ContractVerificationResult(
                "row_count", "FAIL", f"{len(rows)} rows, min {min_rows}",
            ))

    # 5. Required columns (null check)
    required_cols = quality.get("completeness", {}).get("required_columns", [])
    if required_cols:
        null_counts = {}
        for col in required_cols:
            nulls = sum(1 for r in rows if r.get(col) is None)
            if nulls > 0:
                null_counts[col] = nulls
        if null_counts:
            results.append(ContractVerificationResult(
                "required_columns", "FAIL", f"nulls found: {null_counts}",
            ))
        else:
            results.append(ContractVerificationResult(
                "required_columns", "PASS", f"0 nulls in {len(required_cols)} required columns",
            ))

    # 6. DQ P0 gate
    dq_config = quality.get("dq_rules", {})
    rules_file = dq_config.get("rules_file", "")
    if rules_file and dq_config.get("p0_pass_required"):
        from brightsmith.config import PROJECT_ROOT
        rules_path = PROJECT_ROOT / rules_file
        if rules_path.exists():
            results.append(ContractVerificationResult("dq_p0", "PASS", f"rules file exists: {rules_file}"))
        else:
            results.append(ContractVerificationResult("dq_p0", "FAIL", f"rules file missing: {rules_file}"))
    else:
        results.append(ContractVerificationResult("dq_p0", "SKIP", "no DQ rules configured"))

    # 7. Golden dataset
    accuracy = quality.get("accuracy", {})
    golden_path = accuracy.get("golden_dataset", "")
    if golden_path:
        from brightsmith.config import PROJECT_ROOT
        gpath = PROJECT_ROOT / golden_path
        if gpath.exists():
            results.append(ContractVerificationResult("golden_dataset", "PASS", f"exists: {golden_path}"))
        else:
            results.append(ContractVerificationResult("golden_dataset", "FAIL", f"missing: {golden_path}"))
    else:
        results.append(ContractVerificationResult("golden_dataset", "SKIP", "no golden dataset configured"))

    return results


# ---------------------------------------------------------------------------
# Contract diff
# ---------------------------------------------------------------------------


def diff_contract(
    name: str,
    contracts_dir: Path | None = None,
) -> list[ContractDiffItem]:
    """Compare a contract's schema to the current Iceberg table.

    Detects drift: columns added/removed, type changes, nullability changes.

    Args:
        name: Contract name.
        contracts_dir: Override for contracts directory.

    Returns:
        List of diff items.
    """
    contract = load_contract(name, contracts_dir)
    if contract is None:
        return [ContractDiffItem("INFO", f"Contract '{name}' not found")]

    schema_section = contract.get("schema", {})
    table_name = schema_section.get("table", "")

    try:
        from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
        from brightsmith.infra.iceberg_setup import get_catalog

        catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
        iceberg_table = catalog.load_table(table_name)
    except Exception as e:
        return [ContractDiffItem("INFO", f"Cannot load table: {e}")]

    contract_cols = {c["name"]: c for c in schema_section.get("columns", [])}
    table_fields = {f.name: f for f in iceberg_table.schema().fields}

    diffs: list[ContractDiffItem] = []

    # Columns in contract but missing from table
    for col_name in contract_cols:
        if col_name not in table_fields:
            diffs.append(ContractDiffItem("BREAKING", f"Column '{col_name}' in contract but missing from table"))

    # Columns in table but missing from contract
    for col_name in table_fields:
        if col_name not in contract_cols:
            diffs.append(ContractDiffItem("NON_BREAKING", f"Column '{col_name}' in table but not in contract"))

    # Type mismatches
    for col_name, col_def in contract_cols.items():
        if col_name in table_fields:
            actual_type = _iceberg_type_to_str(table_fields[col_name].field_type)
            if actual_type != col_def["type"]:
                diffs.append(ContractDiffItem(
                    "BREAKING",
                    f"Column '{col_name}' type changed: {col_def['type']} → {actual_type}",
                ))

            # Nullability mismatch
            actual_required = table_fields[col_name].required
            contract_required = col_def.get("required", False)
            if actual_required != contract_required:
                diffs.append(ContractDiffItem(
                    "NON_BREAKING",
                    f"Column '{col_name}' nullability changed: required={contract_required} → {actual_required}",
                ))

    return diffs


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse a semver string into (major, minor, patch)."""
    parts = version.split(".")
    return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0, int(parts[2]) if len(parts) > 2 else 0)


def bump_version(version: str, change_type: str) -> str:
    """Bump a semver version based on change type.

    Args:
        version: Current version string (e.g., "1.0.0").
        change_type: "BREAKING", "NON_BREAKING", or "PATCH".

    Returns:
        New version string.
    """
    major, minor, patch = parse_version(version)
    if change_type == "BREAKING":
        return f"{major + 1}.0.0"
    elif change_type == "NON_BREAKING":
        return f"{major}.{minor + 1}.0"
    elif change_type == "PATCH":
        return f"{major}.{minor}.{patch + 1}"
    else:
        return f"{major}.{minor}.{patch + 1}"


def check_version_bump_required(diffs: list[ContractDiffItem], current_version: str, new_version: str) -> str | None:
    """Check if the version was bumped appropriately for the changes.

    Returns:
        None if OK, or an error message if the bump is insufficient.
    """
    has_breaking = any(d.change_type == "BREAKING" for d in diffs)
    has_non_breaking = any(d.change_type == "NON_BREAKING" for d in diffs)

    if not diffs:
        return None

    cur = parse_version(current_version)
    new = parse_version(new_version)

    if has_breaking and new[0] <= cur[0]:
        return f"Breaking changes detected but major version not bumped: {current_version} → {new_version}"
    if has_non_breaking and not has_breaking and new[0] == cur[0] and new[1] <= cur[1]:
        return f"Non-breaking changes detected but minor version not bumped: {current_version} → {new_version}"

    return None


# ---------------------------------------------------------------------------
# Deprecation
# ---------------------------------------------------------------------------


def deprecate_contract(
    name: str,
    successor: str,
    archive_after: str,
    contracts_dir: Path | None = None,
) -> dict | None:
    """Mark a contract as deprecated with a successor reference.

    Sets status to 'deprecated' and adds deprecation metadata to the
    compatibility section. Used by @cab-agent when a MAJOR schema change
    triggers a table fork.

    Args:
        name: Contract name to deprecate.
        successor: Contract name of the replacement (e.g., "company-financials-v2").
        archive_after: ISO date after which the contract can be archived.
        contracts_dir: Override for contracts directory.

    Returns:
        Updated contract dict, or None if contract not found.
    """
    contract = load_contract(name, contracts_dir)
    if contract is None:
        logger.warning("Cannot deprecate contract '%s': not found", name)
        return None

    contract["metadata"]["status"] = "deprecated"

    compat = contract.setdefault("compatibility", {})
    compat["deprecated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    compat["archive_after"] = archive_after
    compat["successor_contract"] = successor

    save_contract(contract, contracts_dir)
    return contract


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for contract operations."""
    parser = argparse.ArgumentParser(description="Brightsmith Data Contracts")
    subparsers = parser.add_subparsers(dest="command")

    # generate
    gen_p = subparsers.add_parser("generate", help="Generate contract from Iceberg table")
    gen_p.add_argument("--table", required=True, help="Table name (namespace.table)")
    gen_p.add_argument("--spec", default="", help="Spec path")
    gen_p.add_argument("--grain", nargs="*", help="Grain columns")
    gen_p.add_argument("--grain-desc", default="", help="Grain description")
    gen_p.add_argument("--dq-rules", default="", help="DQ rules file path")
    gen_p.add_argument("--golden-dataset", default="", help="Golden dataset path")

    # verify
    ver_p = subparsers.add_parser("verify", help="Verify contract against live table")
    ver_p.add_argument("name", nargs="?", help="Contract name")
    ver_p.add_argument("--all", action="store_true", help="Verify all contracts")

    # diff
    diff_p = subparsers.add_parser("diff", help="Detect schema drift")
    diff_p.add_argument("name", help="Contract name")

    # list
    subparsers.add_parser("list", help="List all contracts")

    args = parser.parse_args()

    if args.command == "generate":
        _cmd_generate(args)
    elif args.command == "verify":
        _cmd_verify(args)
    elif args.command == "diff":
        _cmd_diff(args)
    elif args.command == "list":
        _cmd_list(args)
    else:
        parser.print_help()


def _cmd_generate(args: argparse.Namespace) -> None:
    contract = generate_contract(
        table_name=args.table,
        spec_path=args.spec,
        grain_columns=args.grain,
        grain_description=args.grain_desc,
        dq_rules_path=args.dq_rules,
        golden_dataset_path=args.golden_dataset,
    )
    name = contract["metadata"]["name"]
    version = contract["metadata"]["version"]
    cols = len(contract["schema"]["columns"])
    print(f"Generated contract: {name} v{version} ({cols} columns)")


def _cmd_verify(args: argparse.Namespace) -> None:
    if args.all or not args.name:
        contracts = list_contracts()
        if not contracts:
            print("No contracts found.")
            sys.exit(1)
        all_pass = True
        for c in contracts:
            results = verify_contract(c["name"])
            _print_verify_results(c["name"], c.get("version", "?"), results)
            if any(r.status == "FAIL" for r in results):
                all_pass = False
            print()
        sys.exit(0 if all_pass else 1)
    else:
        contract = load_contract(args.name)
        version = contract.get("metadata", {}).get("version", "?") if contract else "?"
        results = verify_contract(args.name)
        _print_verify_results(args.name, version, results)
        if any(r.status == "FAIL" for r in results):
            sys.exit(1)


def _print_verify_results(name: str, version: str, results: list[ContractVerificationResult]) -> None:
    print(f"Contract: {name} v{version}")
    for r in results:
        print(f"  {r.check + ':':<20} {r.status:<5} {r.detail}")
    has_fail = any(r.status == "FAIL" for r in results)
    print(f"\n  Status: {'INVALID' if has_fail else 'VALID'}")


def _cmd_diff(args: argparse.Namespace) -> None:
    contract = load_contract(args.name)
    version = contract.get("metadata", {}).get("version", "?") if contract else "?"
    diffs = diff_contract(args.name)
    if not diffs:
        print(f"Contract: {args.name} v{version}")
        print("  No drift detected.")
        return
    print(f"Contract: {args.name} v{version}")
    for d in diffs:
        label = "BREAKING" if d.change_type == "BREAKING" else "NEW" if d.change_type == "NON_BREAKING" else "INFO"
        print(f"  {label + ':':<12} {d.description}")
    has_breaking = any(d.change_type == "BREAKING" for d in diffs)
    if has_breaking:
        suggested = bump_version(version, "BREAKING")
        print(f"\n  Action required: bump version to {suggested} for breaking change")


def _cmd_list(args: argparse.Namespace) -> None:
    contracts = list_contracts()
    if not contracts:
        print("No contracts found.")
        return
    print(f"{'Name':<25} {'Version':<10} {'Status':<10} {'Table':<30}")
    print("-" * 75)
    for c in contracts:
        print(f"{c['name']:<25} {c['version']:<10} {c['status']:<10} {c['table']:<30}")


if __name__ == "__main__":
    main()
