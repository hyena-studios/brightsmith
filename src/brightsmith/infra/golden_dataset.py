"""Golden dataset verification tooling.

Loads known-correct reference values from governance/golden-datasets/
and verifies them against live Iceberg table data. Each golden dataset
file defines filter conditions, expected values, and tolerances.

Usage:
    python -m grist.infra.golden_dataset verify --spec my-spec
    python -m grist.infra.golden_dataset list
    python -m grist.infra.golden_dataset summary
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of verifying one golden dataset value."""

    description: str
    expected: float
    actual: float | None
    diff_pct: float | None
    status: str  # MATCH, CLOSE, MISMATCH, MISSING
    filters: dict
    column: str


def load_golden_dataset(spec: str, golden_dir: Path | None = None) -> dict | None:
    """Load a golden dataset file for a spec.

    Args:
        spec: Spec name (maps to {spec}-golden.json).
        golden_dir: Override for golden datasets directory.

    Returns:
        Parsed golden dataset dict, or None if not found.
    """
    from brightsmith.config import GOLDEN_DATASETS_DIR

    gdir = golden_dir or GOLDEN_DATASETS_DIR
    path = gdir / f"{spec}-golden.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def verify_golden_dataset(
    spec: str,
    golden_dir: Path | None = None,
    tolerance_override: float | None = None,
) -> list[VerificationResult]:
    """Verify a golden dataset against live Iceberg data.

    Reads the golden dataset file, queries the Iceberg table for each
    expected value, and compares with tolerance.

    Args:
        spec: Spec name.
        golden_dir: Override for golden datasets directory.
        tolerance_override: Override tolerance for all values (fraction, e.g., 0.01 = 1%).

    Returns:
        List of VerificationResult for each golden value.
    """
    from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
    from brightsmith.infra.iceberg_setup import get_catalog

    dataset = load_golden_dataset(spec, golden_dir)
    if dataset is None:
        return []

    table_name = dataset.get("table", "")
    values = dataset.get("values", dataset.get("records", []))
    results: list[VerificationResult] = []

    # Try to load the Iceberg table
    try:
        catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
        parts = table_name.split(".")
        if len(parts) == 2:
            iceberg_table = catalog.load_table(f"{parts[0]}.{parts[1]}")
        else:
            logger.warning("Invalid table name format: %s", table_name)
            return []
    except Exception as e:
        logger.warning("Could not load table %s: %s", table_name, e)
        # Return MISSING results for all values
        for val in values:
            results.append(VerificationResult(
                description=val.get("description", val.get("metric", "?")),
                expected=val.get("expected_value", 0),
                actual=None,
                diff_pct=None,
                status="MISSING",
                filters=val.get("filters", {}),
                column=val.get("column", val.get("metric", "")),
            ))
        return results

    # Query and verify each value
    from brightsmith.infra.iceberg_setup import read_with_duckdb

    all_rows = read_with_duckdb(iceberg_table)

    for val in values:
        description = val.get("description", val.get("metric", "?"))
        expected = val.get("expected_value", 0)
        filters = val.get("filters", {})
        column = val.get("column", val.get("metric", ""))
        tol = tolerance_override or val.get("tolerance_pct", val.get("tolerance", 0.01))
        tol_type = val.get("tolerance_type", "relative")

        # Apply filters
        matching = all_rows
        for fk, fv in filters.items():
            matching = [r for r in matching if str(r.get(fk, "")) == str(fv)]

        if not matching:
            results.append(VerificationResult(
                description=description, expected=expected, actual=None,
                diff_pct=None, status="MISSING", filters=filters, column=column,
            ))
            continue

        actual = matching[0].get(column)
        if actual is None:
            results.append(VerificationResult(
                description=description, expected=expected, actual=None,
                diff_pct=None, status="MISSING", filters=filters, column=column,
            ))
            continue

        actual = float(actual)
        if expected == 0:
            diff_pct = 0.0 if actual == 0 else 100.0
        else:
            diff_pct = abs(actual - expected) / abs(expected) * 100.0

        if tol_type == "absolute":
            is_within = abs(actual - expected) <= tol
        else:
            is_within = diff_pct <= (tol * 100.0 if tol < 1.0 else tol)

        if is_within:
            status = "MATCH"
        elif diff_pct <= 5.0:
            status = "CLOSE"
        else:
            status = "MISMATCH"

        results.append(VerificationResult(
            description=description, expected=expected, actual=actual,
            diff_pct=diff_pct, status=status, filters=filters, column=column,
        ))

    return results


def list_golden_datasets(golden_dir: Path | None = None) -> list[dict]:
    """List all golden datasets with basic metadata.

    Returns:
        List of dicts with spec, table, value_count, path.
    """
    from brightsmith.config import GOLDEN_DATASETS_DIR

    gdir = golden_dir or GOLDEN_DATASETS_DIR
    if not gdir.exists():
        return []

    datasets = []
    for path in sorted(gdir.glob("*-golden.json")):
        try:
            data = json.loads(path.read_text())
            values = data.get("values", data.get("records", []))
            datasets.append({
                "spec": data.get("spec", path.stem.replace("-golden", "")),
                "table": data.get("table", "?"),
                "value_count": len(values),
                "path": str(path),
            })
        except Exception:
            datasets.append({"spec": path.stem, "table": "?", "value_count": 0, "path": str(path)})
    return datasets


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for golden dataset operations."""
    parser = argparse.ArgumentParser(description="Grist Golden Dataset Verification")
    subparsers = parser.add_subparsers(dest="command")

    verify_p = subparsers.add_parser("verify", help="Verify golden dataset against live data")
    verify_p.add_argument("--spec", required=True, help="Spec name")
    verify_p.add_argument("--tolerance", type=float, help="Override tolerance (fraction)")

    subparsers.add_parser("list", help="List all golden datasets")
    subparsers.add_parser("summary", help="Pass/fail summary of all golden datasets")

    args = parser.parse_args()

    if args.command == "verify":
        _cmd_verify(args)
    elif args.command == "list":
        _cmd_list(args)
    elif args.command == "summary":
        _cmd_summary(args)
    else:
        parser.print_help()


def _cmd_verify(args: argparse.Namespace) -> None:
    results = verify_golden_dataset(args.spec, tolerance_override=args.tolerance)
    if not results:
        print(f"No golden dataset found for spec '{args.spec}'")
        sys.exit(1)

    for r in results:
        if r.actual is not None:
            print(f"[{r.status:<8}] {r.description}: {r.actual} vs {r.expected} ({r.diff_pct:.2f}% diff)")
        else:
            print(f"[{r.status:<8}] {r.description}: MISSING (expected {r.expected})")

    passes = sum(1 for r in results if r.status in ("MATCH", "CLOSE"))
    total = len(results)
    pct = (passes / total * 100.0) if total > 0 else 0
    print(f"\nResults: {passes} pass, {total - passes} fail")
    print(f"Pass rate: {pct:.1f}%")

    if pct < 80.0:
        print("FAIL")
        sys.exit(1)
    else:
        print("PASS")


def _cmd_list(args: argparse.Namespace) -> None:
    datasets = list_golden_datasets()
    if not datasets:
        print("No golden datasets found.")
        return
    print(f"{'Spec':<30} {'Table':<30} {'Values':<8}")
    print("-" * 68)
    for ds in datasets:
        print(f"{ds['spec']:<30} {ds['table']:<30} {ds['value_count']:<8}")


def _cmd_summary(args: argparse.Namespace) -> None:
    datasets = list_golden_datasets()
    if not datasets:
        print("No golden datasets found.")
        return

    for ds in datasets:
        results = verify_golden_dataset(ds["spec"])
        if not results:
            print(f"[EMPTY   ] {ds['spec']}")
            continue
        passes = sum(1 for r in results if r.status in ("MATCH", "CLOSE"))
        total = len(results)
        pct = (passes / total * 100.0) if total > 0 else 0
        status = "PASS" if pct >= 80.0 else "FAIL"
        print(f"[{status:<8}] {ds['spec']}: {passes}/{total} ({pct:.0f}%)")


if __name__ == "__main__":
    main()
