"""CLI entry point for governance administration."""

from __future__ import annotations

import argparse
import sys

from brightsmith.infra.governance.migration import cmd_migrate
from brightsmith.infra.governance.queries import (
    _TABLE_CONFIGS,
    _get_governance_table,
    get_governance_summary,
)
from brightsmith.infra.governance.sync import sync_from_files

__all__ = ["main"]


def cmd_status() -> None:
    """Print governance database summary."""
    summary = get_governance_summary()

    specs = summary.get("specs", [])
    dq = summary.get("dq_overall", {})
    gc = summary.get("governance_completeness", {})

    print("Governance Database Status")
    print("=" * 60)

    # DQ summary
    print(f"\nDQ Score: {dq.get('score_pct', 0):.1f}%")
    print(f"  Rules: {dq.get('rules_passing', 0)}/{dq.get('rules_total', 0)} passing")
    print(f"  P0 Gate: {'PASS' if dq.get('p0_passed', True) else 'FAIL'}")

    # Governance completeness
    total = gc.get("total_specs", 0)
    print(f"\nGovernance Completeness ({total} specs):")
    if total > 0:
        print(f"  DQ rules:       {gc.get('with_dq', 0)}/{total} ({gc.get('with_dq', 0)/total*100:.0f}%)")
        print(f"  Contracts:      {gc.get('with_contract', 0)}/{total} ({gc.get('with_contract', 0)/total*100:.0f}%)")
        print(f"  Lineage:        {gc.get('with_lineage', 0)}/{total} ({gc.get('with_lineage', 0)/total*100:.0f}%)")
        print(f"  Golden datasets:{gc.get('with_golden_dataset', 0)}/{total} ({gc.get('with_golden_dataset', 0)/total*100:.0f}%)")

    # Per-spec table
    if specs:
        print(f"\n{'Spec':<35} {'Zone':<8} {'Status':<12} {'DQ%':>6} {'Steps':>8}")
        print("-" * 75)
        for s in specs:
            dq_pct = f"{s.get('dq_score_pct', 0):.0f}%" if s.get("dq_rules_total") else "N/A"
            steps_done = s.get("pipeline_steps_completed") or 0
            steps_total = s.get("pipeline_steps_total") or 0
            steps_str = f"{steps_done}/{steps_total}" if steps_total else "N/A"
            print(f"{s.get('spec_name', '?'):<35} {s.get('zone', '?'):<8} {s.get('status', '?'):<12} {dq_pct:>6} {steps_str:>8}")

    # Open blockers
    blockers = summary.get("open_blockers", [])
    if blockers:
        print(f"\nOpen Blockers ({len(blockers)}):")
        for b in blockers[:5]:
            print(f"  [{b.get('agent_id')}] {b.get('spec_name')}: {b.get('summary', '')[:60]}")

    # Zone summary
    zones = summary.get("zones", {})
    if zones:
        print(f"\n{'Zone':<12} {'Specs':>6} {'Complete':>10}")
        print("-" * 30)
        for zone, data in sorted(zones.items()):
            print(f"{zone:<12} {data.get('specs', 0):>6} {data.get('complete', 0):>10}")


def cmd_sync() -> None:
    """Backfill governance tables from existing file artifacts."""
    print("Syncing governance tables from file artifacts...")
    counts = sync_from_files()
    print("\nSync complete:")
    for table, count in sorted(counts.items()):
        print(f"  {table}: {count} records synced")
    total = sum(counts.values())
    print(f"\nTotal: {total} records")


def cmd_export() -> None:
    """Regenerate file artifacts from governance tables."""
    from brightsmith.infra.governance.exporters import export_to_files

    print("Exporting file artifacts from governance tables...")
    counts = export_to_files()
    print("\nExport complete:")
    for artifact_type, count in sorted(counts.items()):
        print(f"  {artifact_type}: {count} generated")


def cmd_query(table_name: str) -> None:
    """Run an ad-hoc query against a governance table."""
    import duckdb

    if table_name not in _TABLE_CONFIGS:
        print(f"Unknown table: {table_name}")
        print(f"Available: {', '.join(sorted(_TABLE_CONFIGS.keys()))}")
        sys.exit(1)

    try:
        table = _get_governance_table(table_name)
        arrow_table = table.scan().to_arrow()
        if arrow_table.num_rows == 0:
            print(f"governance.{table_name}: 0 rows")
            return

        con = duckdb.connect()
        con.sql("SELECT * FROM arrow_table ORDER BY 1 DESC LIMIT 20").show()
        print(f"\n({arrow_table.num_rows} total rows)")
    except Exception as e:
        print(f"Error querying governance.{table_name}: {e}")
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="governance_db",
        description="Brightsmith Governance Admin Database",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Governance database summary")
    subparsers.add_parser("sync", help="Backfill from existing file artifacts")
    subparsers.add_parser("migrate", help="One-time migration of files to Iceberg")
    subparsers.add_parser("export", help="Regenerate file artifacts from tables")

    query_parser = subparsers.add_parser("query", help="Query a governance table")
    query_parser.add_argument("table", help=f"Table name: {', '.join(sorted(_TABLE_CONFIGS.keys()))}")

    args = parser.parse_args()

    commands = {
        "status": cmd_status,
        "sync": cmd_sync,
        "migrate": cmd_migrate,
        "export": cmd_export,
        "query": lambda: cmd_query(args.table),
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
