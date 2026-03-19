"""After-Action Report — reconciles chaos manifest with DQ results.

Takes a manifest (what was corrupted) and DQ results (what was caught),
produces a report showing caught vs missed corruptions. This is the
feedback loop that drives DQ rule improvement.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from brightsmith.infra.chaos_monkey.manifest import ChaosManifest


class AfterActionReconciler:
    """Reconciles chaos monkey manifest with DQ execution results."""

    def reconcile(
        self,
        manifest: ChaosManifest,
        dq_results: dict,
    ) -> dict:
        """Compare manifest corruptions with DQ rule results.

        Args:
            manifest: The chaos manifest documenting what was corrupted.
            dq_results: DQ runner output (the dict from run_rules()).

        Returns:
            Reconciliation report dict.
        """
        # Analyze DQ results
        failed_rules = [r for r in dq_results.get("results", []) if not r["passed"]]
        passed_rules = [r for r in dq_results.get("results", []) if r["passed"]]

        # Map DQ dimensions to whether they were caught
        corrupted_dimensions = manifest.dimensions_covered
        caught_dimensions = set()
        for rule in failed_rules:
            cat = rule.get("category", "")
            if cat in corrupted_dimensions:
                caught_dimensions.add(cat)

        missed_dimensions = corrupted_dimensions - caught_dimensions

        return {
            "source_table": manifest.source_table,
            "shadow_table": manifest.shadow_table,
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
            "manifest_summary": {
                "total_rows": manifest.total_rows,
                "rows_corrupted": manifest.rows_corrupted,
                "total_corruptions": len(manifest.corruptions),
                "dimensions_injected": sorted(corrupted_dimensions),
            },
            "dq_summary": {
                "rules_total": dq_results.get("rules_total", 0),
                "rules_failed": dq_results.get("rules_failed", 0),
                "rules_passed": dq_results.get("rules_passed", 0),
            },
            "coverage": {
                "dimensions_caught": sorted(caught_dimensions),
                "dimensions_missed": sorted(missed_dimensions),
                "catch_rate": (
                    len(caught_dimensions) / len(corrupted_dimensions)
                    if corrupted_dimensions
                    else 1.0
                ),
            },
            "failed_rules": [
                {"rule_id": r["rule_id"], "category": r.get("category"), "detail": r.get("detail")}
                for r in failed_rules
            ],
            "gaps": [
                {
                    "dimension": dim,
                    "corruptions": [
                        {"row": c.row_index, "column": c.column, "strategy": c.strategy}
                        for c in manifest.corruptions
                        if c.dimension == dim
                    ],
                    "recommendation": f"Add DQ rule covering {dim} dimension",
                }
                for dim in sorted(missed_dimensions)
            ],
        }

    def generate_report(
        self,
        manifest: ChaosManifest,
        dq_results: dict,
        output_path: Path,
    ) -> Path:
        """Generate a markdown After-Action Report.

        Args:
            manifest: Chaos manifest.
            dq_results: DQ runner output.
            output_path: Where to write the markdown report.

        Returns:
            Path to the written report.
        """
        report = self.reconcile(manifest, dq_results)
        catch_rate = report["coverage"]["catch_rate"]
        catch_pct = f"{catch_rate:.0%}"

        lines = [
            f"# After-Action Report: {manifest.source_table}",
            f"",
            f"**Date:** {report['reconciled_at']}",
            f"**Agent:** @chaos-monkey",
            f"**Shadow Table:** {manifest.shadow_table}",
            f"**Catch Rate:** {catch_pct}",
            f"",
            f"## Injection Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total rows | {manifest.total_rows} |",
            f"| Rows corrupted | {manifest.rows_corrupted} |",
            f"| Total corruptions | {len(manifest.corruptions)} |",
            f"| Corruption rate | {manifest.corruption_rate:.0%} |",
            f"| Dimensions injected | {', '.join(sorted(manifest.dimensions_covered))} |",
            f"",
            f"## DQ Rule Performance",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Rules executed | {report['dq_summary']['rules_total']} |",
            f"| Rules failed (caught corruption) | {report['dq_summary']['rules_failed']} |",
            f"| Rules passed (missed corruption) | {report['dq_summary']['rules_passed']} |",
            f"",
            f"## Coverage Analysis",
            f"",
            f"| Dimension | Status |",
            f"|-----------|--------|",
        ]

        for dim in sorted(manifest.dimensions_covered):
            status = "CAUGHT" if dim in report["coverage"]["dimensions_caught"] else "MISSED"
            lines.append(f"| {dim} | {status} |")

        if report["gaps"]:
            lines.extend([
                f"",
                f"## Gaps Found",
                f"",
            ])
            for gap in report["gaps"]:
                lines.append(f"### {gap['dimension']}")
                lines.append(f"")
                lines.append(f"**Recommendation:** {gap['recommendation']}")
                lines.append(f"")
                lines.append(f"| Row | Column | Strategy |")
                lines.append(f"|-----|--------|----------|")
                for c in gap["corruptions"]:
                    lines.append(f"| {c['row']} | {c['column']} | {c['strategy']} |")
                lines.append(f"")

        if not report["gaps"]:
            lines.extend([
                f"",
                f"## Result",
                f"",
                f"All injected corruption dimensions were caught by existing DQ rules.",
            ])

        content = "\n".join(lines) + "\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        return output_path
