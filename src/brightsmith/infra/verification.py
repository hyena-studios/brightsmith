"""Verification framework — validates correctness, not just structure.

DQ rules validate structural properties (not null, unique, in range).
Verification validates correctness: "is this number actually right?"
This wraps golden dataset verification with reporting and tolerance.

Usage:
    python -m grist.infra.verification run [--spec SPEC] [--tolerance 1.0]
"""

from __future__ import annotations

import argparse
import sys

from brightsmith.infra.golden_dataset import (
    VerificationResult,
    list_golden_datasets,
    verify_golden_dataset,
)


def run_verification(
    spec: str | None = None,
    tolerance: float | None = None,
) -> tuple[list[VerificationResult], float]:
    """Run verification for one or all specs.

    Args:
        spec: Spec name (None = all specs).
        tolerance: Override tolerance for all values.

    Returns:
        Tuple of (all_results, pass_rate_pct).
    """
    all_results: list[VerificationResult] = []

    if spec:
        results = verify_golden_dataset(spec, tolerance_override=tolerance)
        all_results.extend(results)
    else:
        for ds in list_golden_datasets():
            results = verify_golden_dataset(ds["spec"], tolerance_override=tolerance)
            all_results.extend(results)

    if not all_results:
        return ([], 0.0)

    passes = sum(1 for r in all_results if r.status in ("MATCH", "CLOSE"))
    pass_rate = passes / len(all_results) * 100.0
    return (all_results, pass_rate)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for verification."""
    parser = argparse.ArgumentParser(description="Grist Verification Framework")
    subparsers = parser.add_subparsers(dest="command")

    run_p = subparsers.add_parser("run", help="Run verification checks")
    run_p.add_argument("--spec", help="Spec name (omit for all)")
    run_p.add_argument("--tolerance", type=float, help="Override tolerance (percentage)")
    run_p.add_argument("--threshold", type=float, default=80.0, help="Pass rate threshold (default: 80%%)")

    args = parser.parse_args()

    if args.command == "run":
        _cmd_run(args)
    else:
        parser.print_help()


def _cmd_run(args: argparse.Namespace) -> None:
    results, pass_rate = run_verification(spec=args.spec, tolerance=args.tolerance)

    if not results:
        print("No verification results — no golden datasets found.")
        sys.exit(1)

    for r in results:
        if r.actual is not None:
            print(f"[{r.status:<8}] {r.description}: {r.actual} vs {r.expected} ({r.diff_pct:.2f}% diff)")
        else:
            print(f"[{r.status:<8}] {r.description}: MISSING (expected {r.expected})")

    passes = sum(1 for r in results if r.status in ("MATCH", "CLOSE"))
    close = sum(1 for r in results if r.status == "CLOSE")
    mismatches = sum(1 for r in results if r.status == "MISMATCH")
    missing = sum(1 for r in results if r.status == "MISSING")
    total = len(results)

    print(f"\nResults: {passes} pass ({close} close), {mismatches} mismatch, {missing} missing")
    print(f"Pass rate: {pass_rate:.1f}% (threshold: {args.threshold}%)")

    if pass_rate >= args.threshold:
        print("PASS")
    else:
        print("FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
