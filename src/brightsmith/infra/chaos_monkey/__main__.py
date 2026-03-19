"""CLI for chaos monkey operations.

Usage:
    python -m brightsmith.infra.chaos_monkey inject --table NS.TABLE --rate 0.07 --seed 42
    python -m brightsmith.infra.chaos_monkey reconcile --table NS.TABLE --manifest PATH --dq-results PATH
    python -m brightsmith.infra.chaos_monkey cleanup --table NS.TABLE
    python -m brightsmith.infra.chaos_monkey manifest --latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brightsmith.config import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Brightsmith Chaos Monkey")
    subparsers = parser.add_subparsers(dest="command")

    # inject
    inject_p = subparsers.add_parser("inject", help="Inject corruptions into shadow table")
    inject_p.add_argument("--table", required=True, help="Source table (namespace.table)")
    inject_p.add_argument("--rate", type=float, default=0.07, help="Corruption rate (0.0-1.0)")
    inject_p.add_argument("--seed", type=int, help="Random seed")

    # reconcile
    recon_p = subparsers.add_parser("reconcile", help="Generate After-Action Report")
    recon_p.add_argument("--manifest", required=True, help="Path to manifest JSON")
    recon_p.add_argument("--dq-results", required=True, help="Path to DQ results JSON")
    recon_p.add_argument("--output", help="Output path for report")

    # cleanup
    cleanup_p = subparsers.add_parser("cleanup", help="Remove shadow tables")
    cleanup_p.add_argument("--table", required=True, help="Source table (namespace.table)")

    # manifest
    manifest_p = subparsers.add_parser("manifest", help="View manifests")
    manifest_p.add_argument("--latest", action="store_true", help="Show latest manifest")

    args = parser.parse_args()

    if args.command == "inject":
        _cmd_inject(args)
    elif args.command == "reconcile":
        _cmd_reconcile(args)
    elif args.command == "cleanup":
        _cmd_cleanup(args)
    elif args.command == "manifest":
        _cmd_manifest(args)
    else:
        parser.print_help()


def _cmd_inject(args) -> None:
    from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
    from brightsmith.infra.chaos_monkey.injector import ChaosInjector, InjectionConfig
    from brightsmith.infra.iceberg_setup import get_catalog, read_with_duckdb

    ns, tbl = args.table.split(".")
    catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
    source = catalog.load_table(args.table)
    records = read_with_duckdb(source)

    config = InjectionConfig(rate=args.rate, seed=args.seed)
    injector = ChaosInjector(catalog, config)
    _, manifest = injector.inject(ns, tbl, records)

    manifest_dir = PROJECT_ROOT / "governance" / "chaos-manifests"
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_path = manifest_dir / f"{ns}-{tbl}-{ts}.json"
    manifest.save(manifest_path)

    print(f"Injected {manifest.rows_corrupted} corrupted rows into {manifest.shadow_table}")
    print(f"Manifest: {manifest_path}")


def _cmd_reconcile(args) -> None:
    from brightsmith.infra.chaos_monkey.manifest import ChaosManifest
    from brightsmith.infra.chaos_monkey.reconciler import AfterActionReconciler

    manifest = ChaosManifest.from_file(Path(args.manifest))
    dq_results = json.loads(Path(args.dq_results).read_text())

    reconciler = AfterActionReconciler()

    if args.output:
        output_path = Path(args.output)
    else:
        manifest_dir = PROJECT_ROOT / "governance" / "chaos-manifests"
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        source_name = manifest.source_table.replace(".", "-")
        output_path = manifest_dir / f"{source_name}-after-action-{ts}.md"

    reconciler.generate_report(manifest, dq_results, output_path)
    print(f"After-Action Report: {output_path}")


def _cmd_cleanup(args) -> None:
    from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
    from brightsmith.infra.chaos_monkey.safety import SHADOW_PREFIX
    from brightsmith.infra.iceberg_setup import get_catalog

    ns, tbl = args.table.split(".")
    shadow_ns = f"{SHADOW_PREFIX}{ns}"
    catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)

    try:
        catalog.drop_table(f"{shadow_ns}.{tbl}")
        print(f"Dropped {shadow_ns}.{tbl}")
    except Exception as e:
        print(f"Cleanup failed: {e}")


def _cmd_manifest(args) -> None:
    manifest_dir = PROJECT_ROOT / "governance" / "chaos-manifests"
    files = sorted(manifest_dir.glob("*.json"), reverse=True)
    if not files:
        print("No manifests found.")
        return

    if args.latest:
        data = json.loads(files[0].read_text())
        print(json.dumps(data, indent=2))
    else:
        for f in files:
            print(f.name)


if __name__ == "__main__":
    main()
