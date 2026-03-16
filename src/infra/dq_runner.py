"""DQ execution engine — runs governance rules against real Iceberg data.

Reads JSON rules from governance/dq-rules/, executes SQL against Iceberg tables
via PyIceberg scan → Arrow → DuckDB, evaluates thresholds, and stores results.

Usage:
    python -m src.infra.dq_runner status [--spec NAME]
    python -m src.infra.dq_runner approve RULE_ID [RULE_ID ...]
    python -m src.infra.dq_runner run [--spec NAME] [--priority P0]
    python -m src.infra.dq_runner results [--spec NAME]
    python -m src.infra.dq_runner scorecard [--spec NAME]
    python -m src.infra.dq_runner acknowledge --spec NAME --run RUN_ID --reason "..."
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.config import (
    CATALOG_PATH,
    DQ_RESULTS_DIR,
    DQ_RULES_DIR,
    REQUIRE_HUMAN_APPROVAL,
    WAREHOUSE_PATH,
)
from src.infra.iceberg_setup import get_catalog


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


def load_rules(spec: str | None = None) -> list[dict]:
    """Load DQ rules from JSON files in governance/dq-rules/.

    Args:
        spec: If provided, only load rules for this spec.

    Returns:
        List of rule dicts, each augmented with 'spec' and 'tables' from the file.
    """
    rules = []
    for path in sorted(DQ_RULES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        file_spec = data.get("spec", path.stem)
        if spec and file_spec != spec:
            continue
        tables = data.get("tables", [data.get("table", "")])
        for rule in data.get("rules", []):
            rule.setdefault("spec", file_spec)
            rule.setdefault("tables", tables)
            rule.setdefault("status", "active")
            rules.append(rule)
    return rules


def _save_rules_file(path: Path, data: dict) -> None:
    """Write a DQ rules JSON file with consistent formatting."""
    path.write_text(json.dumps(data, indent=2) + "\n")


def _load_rules_file(path: Path) -> dict:
    """Load a single DQ rules JSON file."""
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------

# Supported threshold expressions:
#   "result = 0"         → scalar result equals 0
#   "result_count = 0"   → row count of result equals 0
#   "result >= 25.0"     → scalar result >= 25.0

_THRESHOLD_RE = re.compile(
    r"^(result|result_count)\s*(=|==|!=|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$"
)


def evaluate_threshold(raw_result: object, threshold_expr: str) -> tuple[bool, str]:
    """Evaluate a threshold expression against a query result.

    Args:
        raw_result: For 'result' thresholds, a scalar value.
                    For 'result_count' thresholds, the row count.
        threshold_expr: Expression like "result = 0" or "result >= 25.0".

    Returns:
        (passed, detail_string)
    """
    # Strip non-numeric suffixes like "100% — zero violations"
    clean = threshold_expr.strip()
    # Try to extract the comparison part
    for candidate in [clean, clean.split("—")[0].strip(), clean.split("–")[0].strip()]:
        match = _THRESHOLD_RE.match(candidate.strip())
        if match:
            break
    else:
        return False, f"unparseable threshold: {threshold_expr}"

    target_type, operator, value_str = match.groups()
    threshold_value = float(value_str)

    if target_type == "result_count":
        actual = int(raw_result) if raw_result is not None else 0
    else:
        actual = float(raw_result) if raw_result is not None else 0.0

    ops = {
        "=": lambda a, b: a == b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
    }
    passed = ops[operator](actual, threshold_value)
    detail = f"actual={actual}, threshold={target_type} {operator} {threshold_value}"
    return passed, detail


# ---------------------------------------------------------------------------
# SQL execution
# ---------------------------------------------------------------------------

# Known Iceberg namespaces in the project (zones)
_KNOWN_NAMESPACES = {"raw", "base", "consumable", "ai_ready"}

# Matches namespace.table references in SQL (e.g., base.financial_facts)
_TABLE_REF_RE = re.compile(r"\b([a-z_]+)\.([a-z_]+)\b")


def _extract_table_refs(sql: str) -> list[tuple[str, str]]:
    """Extract (namespace, table) pairs from SQL text.

    Only matches references where the namespace is a known Iceberg namespace
    (raw, base, consumable, ai_ready). This avoids false positives from
    alias.column references like r.cik or m.status.
    """
    refs = []
    seen = set()
    for ns, tbl in _TABLE_REF_RE.findall(sql):
        if ns not in _KNOWN_NAMESPACES:
            continue
        key = (ns, tbl)
        if key not in seen:
            seen.add(key)
            refs.append(key)
    return refs


def _rewrite_sql(sql: str, table_refs: list[tuple[str, str]]) -> str:
    """Rewrite SQL to replace namespace.table with namespace_table view names."""
    rewritten = sql
    for ns, tbl in table_refs:
        view_name = f"{ns}_{tbl}"
        rewritten = rewritten.replace(f"{ns}.{tbl}", view_name)
    return rewritten


def execute_sql_rule(rule: dict, con: duckdb.DuckDBPyConnection) -> dict:
    """Execute a single SQL-based DQ rule.

    Args:
        rule: Rule dict with 'sql' and 'threshold' keys.
        con: DuckDB connection with Iceberg tables registered as views.

    Returns:
        Result dict with rule_id, passed, raw_value, violations, etc.
    """
    rule_id = rule["rule_id"]
    sql = rule["sql"]
    threshold = rule["threshold"]
    start = time.monotonic()

    try:
        result = con.execute(sql).fetchall()

        # Determine raw_value based on threshold type
        if "result_count" in threshold:
            raw_value = len(result)
        else:
            raw_value = result[0][0] if result else 0

        passed, detail = evaluate_threshold(raw_value, threshold)
        elapsed = int((time.monotonic() - start) * 1000)

        return {
            "rule_id": rule_id,
            "spec": rule.get("spec"),
            "category": rule.get("category"),
            "passed": passed,
            "raw_value": raw_value,
            "threshold": threshold,
            "detail": detail,
            "violations": 0 if passed else (raw_value if isinstance(raw_value, int) else None),
            "execution_time_ms": elapsed,
            "error": None,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "rule_id": rule_id,
            "spec": rule.get("spec"),
            "category": rule.get("category"),
            "passed": False,
            "raw_value": None,
            "threshold": threshold,
            "detail": None,
            "violations": None,
            "execution_time_ms": elapsed,
            "error": str(e),
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }


def _register_iceberg_views(
    con: duckdb.DuckDBPyConnection,
    table_refs: list[tuple[str, str]],
    catalog,
) -> list[str]:
    """Register Iceberg tables as DuckDB views using iceberg_scan().

    Uses DuckDB's native iceberg_scan() with the table's metadata_location
    so DuckDB reads directly from Iceberg data files. This enables predicate
    pushdown and column pruning — no full-table materialization into memory.

    Returns list of view names created.
    """
    views = []
    for ns, tbl in table_refs:
        view_name = f"{ns}_{tbl}"
        try:
            iceberg_table = catalog.load_table(f"{ns}.{tbl}")
            metadata_path = iceberg_table.metadata_location
            con.execute(
                f"CREATE VIEW {view_name} AS "
                f"SELECT * FROM iceberg_scan('{metadata_path}')"
            )
            views.append(view_name)
        except Exception as e:
            raise RuntimeError(f"Failed to load Iceberg table {ns}.{tbl}: {e}") from e
    return views


# ---------------------------------------------------------------------------
# Post-write validation
# ---------------------------------------------------------------------------


class DQValidationError(Exception):
    """Raised when P0 DQ rules fail after a data write."""

    def __init__(self, failures: list[dict], run_result: dict):
        self.failures = failures
        self.run_result = run_result
        rule_ids = ", ".join(f["rule_id"] for f in failures)
        super().__init__(f"P0 DQ gate FAILED: {rule_ids}")


def validate_after_write(
    spec: str,
    catalog=None,
) -> dict:
    """Run DQ rules for a spec after data is written. Raises on P0 failure.

    Call this after any promote/ingest operation to enforce DQ gates
    automatically. This is the lakehouse equivalent of database constraints.

    Args:
        spec: The spec whose rules to execute.
        catalog: PyIceberg catalog. If None, loads from default paths.

    Returns:
        Run result dict.

    Raises:
        DQValidationError: If any P0 rule fails.
    """
    result = run_rules(spec=spec, catalog=catalog)

    if not result["p0_passed"]:
        p0_failures = []
        rules = load_rules(spec=spec)
        rule_priorities = {r["rule_id"]: r.get("priority", "P3") for r in rules}
        for r in result["results"]:
            if not r["passed"] and not r.get("error") and rule_priorities.get(r["rule_id"]) == "P0":
                # Only real failures, not errors from missing tables
                p0_failures.append(r)
        if p0_failures:
            raise DQValidationError(p0_failures, result)

    return result


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_rules(
    spec: str | None = None,
    priority: str | None = None,
    catalog=None,
) -> dict:
    """Execute DQ rules against real Iceberg data.

    Args:
        spec: Filter to rules for this spec only.
        priority: Filter to this priority level only (e.g., "P0").
        catalog: PyIceberg catalog. If None, loads from default paths.

    Returns:
        Run result dict with run_id, summary stats, and per-rule results.
    """
    if catalog is None:
        catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)

    rules = load_rules(spec=spec)

    # Filter by status — only APPROVED and ACTIVE rules execute
    rules = [r for r in rules if r.get("status", "active").lower() in ("approved", "active")]

    # Filter by priority if specified
    if priority:
        rules = [r for r in rules if r.get("priority", "").upper() == priority.upper()]

    # Filter to SQL-based rules only (skip implementation-only rules)
    sql_rules = [r for r in rules if "sql" in r]

    # Collect all table references across all rules
    all_table_refs = []
    for rule in sql_rules:
        refs = _extract_table_refs(rule["sql"])
        for ref in refs:
            if ref not in all_table_refs:
                all_table_refs.append(ref)

    # Create DuckDB connection with iceberg extension for iceberg_scan()
    # Tables that can't be loaded (e.g., in test environments) are skipped —
    # rules referencing them will get individual SQL errors instead of crashing
    con = duckdb.connect()
    con.install_extension("iceberg")
    con.load_extension("iceberg")
    for ref in all_table_refs:
        try:
            _register_iceberg_views(con, [ref], catalog)
        except RuntimeError:
            pass  # Rule will fail with a SQL error when it references this table

    # Execute each rule
    results = []
    for rule in sql_rules:
        # Rewrite SQL to use view names
        table_refs = _extract_table_refs(rule["sql"])
        rewritten_sql = _rewrite_sql(rule["sql"], table_refs)
        rule_copy = {**rule, "sql": rewritten_sql}
        result = execute_sql_rule(rule_copy, con)
        results.append(result)

        # Update rule status to ACTIVE on first successful execution
        if result["passed"] and result["error"] is None:
            if rule.get("status", "").lower() == "approved":
                _set_rule_status(rule["rule_id"], "active")

    con.close()

    # Build run summary
    run_id = str(uuid.uuid4())[:8]
    p0_results = [r for r in results if _get_rule_priority(r["rule_id"], sql_rules) == "P0"]

    run_result = {
        "run_id": run_id,
        "spec": spec,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "rules_total": len(results),
        "rules_passed": sum(1 for r in results if r["passed"]),
        "rules_failed": sum(1 for r in results if not r["passed"]),
        "rules_errored": sum(1 for r in results if r["error"]),
        "p0_passed": all(r["passed"] for r in p0_results) if p0_results else True,
        "results": results,
    }

    # Save results
    _save_results(run_result)

    return run_result


def _get_rule_priority(rule_id: str, rules: list[dict]) -> str:
    """Look up a rule's priority from the rules list."""
    for r in rules:
        if r["rule_id"] == rule_id:
            return r.get("priority", "P3")
    return "P3"


def _save_results(run_result: dict) -> Path:
    """Save run results to governance/dq-results/."""
    DQ_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    spec_part = run_result["spec"] or "all"
    filename = f"{spec_part}-{timestamp}.json"
    path = DQ_RESULTS_DIR / filename
    path.write_text(json.dumps(run_result, indent=2, default=str) + "\n")
    return path


def _set_rule_status(rule_id: str, new_status: str) -> None:
    """Update a rule's status in its JSON file."""
    for path in DQ_RULES_DIR.glob("*.json"):
        data = _load_rules_file(path)
        modified = False
        for rule in data.get("rules", []):
            if rule["rule_id"] == rule_id:
                rule["status"] = new_status
                modified = True
                break
        if modified:
            _save_rules_file(path, data)
            break


# ---------------------------------------------------------------------------
# Rule approval
# ---------------------------------------------------------------------------


def approve_rules(rule_ids: list[str]) -> list[dict]:
    """Approve one or more proposed rules.

    Returns list of dicts with rule_id and new status.
    """
    results = []
    for rule_id in rule_ids:
        found = False
        for path in DQ_RULES_DIR.glob("*.json"):
            data = _load_rules_file(path)
            for rule in data.get("rules", []):
                if rule["rule_id"] == rule_id:
                    found = True
                    old_status = rule.get("status", "unknown")
                    if old_status == "proposed":
                        rule["status"] = "approved"
                        rule["approved_by"] = "human"
                        rule["approved_at"] = datetime.now(timezone.utc).isoformat()
                        _save_rules_file(path, data)
                        results.append({"rule_id": rule_id, "status": "approved", "previous": old_status})
                    else:
                        results.append({"rule_id": rule_id, "status": old_status, "message": f"not proposed (was {old_status})"})
                    break
            if found:
                break
        if not found:
            results.append({"rule_id": rule_id, "status": "not_found", "message": "rule not found"})
    return results


# ---------------------------------------------------------------------------
# Results lookup
# ---------------------------------------------------------------------------


def get_latest_results(spec: str | None = None) -> dict | None:
    """Get the most recent results file for a spec (or all specs).

    If spec is given, first looks for spec-specific results files,
    then falls back to 'all-*' results files (filtering to that spec's rules).
    """
    if not DQ_RESULTS_DIR.exists():
        return None

    if spec:
        # Try spec-specific results first
        files = sorted(DQ_RESULTS_DIR.glob(f"{spec}-*.json"), reverse=True)
        # Exclude acknowledgment files
        files = [f for f in files if "-ack-" not in f.name]
        if files:
            return json.loads(files[0].read_text())
        # Fall back to all-specs results, filtering to this spec
        files = sorted(DQ_RESULTS_DIR.glob("all-*.json"), reverse=True)
        if files:
            data = json.loads(files[0].read_text())
            data["results"] = [r for r in data.get("results", []) if r.get("spec") == spec]
            return data
        return None

    # Prefer all-specs results when no spec filter
    files = sorted(DQ_RESULTS_DIR.glob("all-*.json"), reverse=True)
    if not files:
        # Fall back to any results file
        files = sorted(DQ_RESULTS_DIR.glob("*.json"), reverse=True)
        files = [f for f in files if "-ack-" not in f.name]
    if not files:
        return None
    return json.loads(files[0].read_text())


# ---------------------------------------------------------------------------
# Acknowledgment
# ---------------------------------------------------------------------------


def acknowledge_failures(spec: str, run_id: str, reason: str) -> dict:
    """Acknowledge failures in a specific run with a reason.

    Writes an acknowledgment record alongside the results.
    """
    DQ_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ack = {
        "spec": spec,
        "run_id": run_id,
        "acknowledged_by": "human",
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }
    path = DQ_RESULTS_DIR / f"{spec}-ack-{run_id}.json"
    path.write_text(json.dumps(ack, indent=2) + "\n")
    return ack


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_status(spec: str | None = None) -> None:
    """Print rule statuses."""
    rules = load_rules(spec=spec)
    if not rules:
        print("No rules found.")
        return

    counts = {"proposed": 0, "approved": 0, "active": 0}
    print(f"{'Rule ID':<15} {'Status':<10} {'Priority':<5} {'Spec':<35} {'Description'}")
    print("-" * 110)
    for rule in rules:
        status = rule.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        print(f"{rule['rule_id']:<15} {status:<10} {rule.get('priority', '?'):<5} {rule.get('spec', '?'):<35} {rule.get('description', '')[:45]}")

    print(f"\nSummary: {len(rules)} rules — "
          f"{counts.get('active', 0)} active, "
          f"{counts.get('approved', 0)} approved, "
          f"{counts.get('proposed', 0)} proposed")


def _print_results(spec: str | None = None) -> None:
    """Print latest run results."""
    results = get_latest_results(spec)
    if not results:
        print("No results found.")
        return

    print(f"Run: {results['run_id']} at {results['executed_at']}")
    print(f"Total: {results['rules_total']} | Passed: {results['rules_passed']} | Failed: {results['rules_failed']}")
    print(f"P0 gate: {'PASS' if results['p0_passed'] else 'FAIL'}")
    print()
    print(f"{'Rule ID':<15} {'Result':<8} {'Value':<10} {'Time (ms)':<10} {'Detail'}")
    print("-" * 80)
    for r in results.get("results", []):
        status = "PASS" if r["passed"] else ("ERROR" if r.get("error") else "FAIL")
        value = str(r.get("raw_value", ""))[:10]
        detail = r.get("error") or r.get("detail", "")
        print(f"{r['rule_id']:<15} {status:<8} {value:<10} {r.get('execution_time_ms', 0):<10} {detail[:40]}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Grist DQ Runner")
    subparsers = parser.add_subparsers(dest="command")

    # status
    status_parser = subparsers.add_parser("status", help="Show rule statuses")
    status_parser.add_argument("--spec", help="Filter by spec name")

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve proposed rules")
    approve_parser.add_argument("rule_ids", nargs="+", help="Rule IDs to approve")

    # run
    run_parser = subparsers.add_parser("run", help="Execute rules against real data")
    run_parser.add_argument("--spec", help="Filter by spec name")
    run_parser.add_argument("--priority", help="Filter by priority (e.g., P0)")

    # results
    results_parser = subparsers.add_parser("results", help="Show latest results")
    results_parser.add_argument("--spec", help="Filter by spec name")

    # scorecard
    scorecard_parser = subparsers.add_parser("scorecard", help="Generate scorecard")
    scorecard_parser.add_argument("--spec", help="Filter by spec name")

    # acknowledge
    ack_parser = subparsers.add_parser("acknowledge", help="Acknowledge failures")
    ack_parser.add_argument("--spec", required=True, help="Spec name")
    ack_parser.add_argument("--run", required=True, dest="run_id", help="Run ID")
    ack_parser.add_argument("--reason", required=True, help="Acknowledgment reason")

    # badge
    subparsers.add_parser("badge", help="Update README.md badges from latest results")

    args = parser.parse_args()

    if args.command == "status":
        _print_status(args.spec)
    elif args.command == "approve":
        results = approve_rules(args.rule_ids)
        for r in results:
            msg = r.get("message", f"→ {r['status']}")
            print(f"{r['rule_id']}: {msg}")
    elif args.command == "run":
        print("Executing DQ rules against Iceberg data...")
        result = run_rules(spec=args.spec, priority=args.priority)
        print(f"\nRun {result['run_id']} complete:")
        print(f"  Total: {result['rules_total']} | Passed: {result['rules_passed']} | Failed: {result['rules_failed']}")
        print(f"  P0 gate: {'PASS' if result['p0_passed'] else 'FAIL'}")
        if not result["p0_passed"]:
            print("\n  P0 FAILURES (blocking):")
            for r in result["results"]:
                if not r["passed"]:
                    print(f"    {r['rule_id']}: {r.get('error') or r.get('detail', '')}")
    elif args.command == "results":
        _print_results(args.spec)
    elif args.command == "scorecard":
        from src.infra.dq_scorecard import generate_scorecard
        results = get_latest_results(args.spec)
        if not results:
            print("No results found. Run `dq_runner run` first.")
            sys.exit(1)
        specs = [args.spec] if args.spec else _get_all_specs()
        for s in specs:
            spec_results = get_latest_results(s)
            if spec_results:
                path = generate_scorecard(spec_results, s)
                print(f"Scorecard written: {path}")
    elif args.command == "acknowledge":
        ack = acknowledge_failures(args.spec, args.run_id, args.reason)
        print(f"Acknowledged failures for run {args.run_id}: {args.reason}")
    elif args.command == "badge":
        _update_readme_badges()
    else:
        parser.print_help()


def _get_all_specs() -> list[str]:
    """Get all spec names from DQ rules files."""
    specs = set()
    for path in DQ_RULES_DIR.glob("*.json"):
        data = json.loads(path.read_text())
        specs.add(data.get("spec", path.stem))
    return sorted(specs)


def _update_readme_badges() -> None:
    """Update shields.io badges in README.md from latest DQ results."""
    from src.config import PROJECT_ROOT

    readme_path = PROJECT_ROOT / "README.md"
    if not readme_path.exists():
        print("README.md not found.")
        return

    results = get_latest_results()
    if not results:
        print("No DQ results found. Run `dq_runner run` first.")
        return

    passed = results["rules_passed"]
    total = results["rules_total"]
    p0_passed = results["p0_passed"]

    # Badge colors
    if passed == total:
        dq_color = "brightgreen"
    elif passed / total >= 0.9:
        dq_color = "yellow"
    else:
        dq_color = "red"

    p0_color = "brightgreen" if p0_passed else "red"
    p0_text = "PASS" if p0_passed else "FAIL"

    # URL-encode values for shields.io
    dq_badge = f"![DQ Rules](https://img.shields.io/badge/DQ%20rules-{passed}%2F{total}%20passing-{dq_color})"
    p0_badge = f"![P0 Gate](https://img.shields.io/badge/P0%20gate-{p0_text}-{p0_color})"

    content = readme_path.read_text()

    # Replace existing badges using regex
    content = re.sub(
        r"!\[DQ Rules\]\(https://img\.shields\.io/badge/DQ%20rules-[^)]+\)",
        dq_badge,
        content,
    )
    content = re.sub(
        r"!\[P0 Gate\]\(https://img\.shields\.io/badge/P0%20gate-[^)]+\)",
        p0_badge,
        content,
    )

    readme_path.write_text(content)
    print(f"Updated README badges: DQ {passed}/{total}, P0 gate {'PASS' if p0_passed else 'FAIL'}")


if __name__ == "__main__":
    main()
