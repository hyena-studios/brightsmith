"""DQ scorecard generator — creates markdown scorecards from execution results.

Reads timestamped results from governance/dq-results/ and generates
human-readable scorecards in governance/dq-scorecards/.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from brightsmith.config import DQ_RULES_DIR, DQ_SCORECARDS_DIR


def generate_scorecard(run_result: dict, spec: str) -> Path:
    """Generate a markdown scorecard from run results.

    Args:
        run_result: Dict from dq_runner.run_rules() or loaded from results JSON.
        spec: Spec name to generate scorecard for.

    Returns:
        Path to the written scorecard file.
    """
    results = run_result.get("results", [])
    # Filter to this spec's results if the run was cross-spec
    spec_results = [r for r in results if r.get("spec") == spec] or results

    # Load rule metadata for category/priority info
    rule_meta = _load_rule_metadata(spec)

    # Build scorecard
    lines = []
    passed = sum(1 for r in spec_results if r["passed"])
    total = len(spec_results)
    errored = sum(1 for r in spec_results if r.get("error"))
    executed_at = run_result.get("executed_at", datetime.now(timezone.utc).isoformat())

    lines.append(f"## DQ Scorecard: {spec}")
    lines.append(f"**Spec:** {spec}")
    lines.append(f"**Date:** {executed_at[:10]}")
    lines.append(f"**Agent:** @dq-engineer")
    lines.append(f"**Overall Score:** {passed}/{total} rules passing ({_pct(passed, total)}%)")
    lines.append(f"**Data Source:** Production Data Validation (executed {executed_at})")
    lines.append(f"**Run ID:** {run_result.get('run_id', 'unknown')}")
    lines.append("")

    # Results table
    lines.append("### Execution Results")
    lines.append("")
    lines.append("| Rule ID | Category | Priority | Description | Result | Details |")
    lines.append("|---------|----------|----------|-------------|--------|---------|")

    for r in spec_results:
        rid = r["rule_id"]
        meta = rule_meta.get(rid, {})
        category = meta.get("category", "—")
        priority = meta.get("priority", "—")
        description = meta.get("description", "—")
        if r.get("error"):
            result_str = "ERROR"
            detail = r["error"][:60]
        elif r["passed"]:
            result_str = "PASS"
            detail = r.get("detail", "")[:60]
        else:
            result_str = "FAIL"
            detail = r.get("detail", "")[:60]
        lines.append(f"| {rid} | {category} | {priority} | {description} | {result_str} | {detail} |")

    lines.append("")

    # Summary by category
    cat_stats = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in spec_results:
        meta = rule_meta.get(r["rule_id"], {})
        cat = meta.get("category", "Other")
        cat_stats[cat]["total"] += 1
        if r["passed"]:
            cat_stats[cat]["passed"] += 1

    lines.append("### Summary by Category")
    lines.append("| Category | Rules | Passing | Rate |")
    lines.append("|----------|-------|---------|------|")
    for cat in sorted(cat_stats):
        s = cat_stats[cat]
        lines.append(f"| {cat} | {s['total']} | {s['passed']} | {_pct(s['passed'], s['total'])}% |")
    lines.append("")

    # Failures section
    failures = [r for r in spec_results if not r["passed"]]
    if failures:
        lines.append("### Failures Requiring Action")
        lines.append("")
        for r in failures:
            meta = rule_meta.get(r["rule_id"], {})
            priority = meta.get("priority", "?")
            severity = "BLOCKING" if priority == "P0" else ("WARNING" if priority == "P1" else "INFORMATIONAL")
            lines.append(f"- **{r['rule_id']}** ({priority} — {severity}): {r.get('error') or r.get('detail', '')}")
        lines.append("")

    # Gating summary
    p0_failures = [r for r in spec_results if not r["passed"] and rule_meta.get(r["rule_id"], {}).get("priority") == "P0"]
    p1_failures = [r for r in spec_results if not r["passed"] and rule_meta.get(r["rule_id"], {}).get("priority") == "P1"]

    lines.append("### Gate Status")
    if p0_failures:
        lines.append(f"- **P0 Gate: FAIL** — {len(p0_failures)} critical rule(s) failed. Spec cannot be marked complete.")
    else:
        lines.append("- **P0 Gate: PASS** — All critical rules passed.")
    if p1_failures:
        lines.append(f"- **P1 Warnings:** {len(p1_failures)} warning(s) — human review recommended.")
    lines.append("")

    # Write file
    DQ_SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    path = DQ_SCORECARDS_DIR / f"{spec}-scorecard.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def _load_rule_metadata(spec: str) -> dict[str, dict]:
    """Load rule metadata (category, priority, description) from JSON files."""
    meta = {}
    for path in DQ_RULES_DIR.glob("*.json"):
        data = json.loads(path.read_text())
        if data.get("spec") != spec:
            continue
        for rule in data.get("rules", []):
            meta[rule["rule_id"]] = {
                "category": rule.get("category", ""),
                "priority": rule.get("priority", ""),
                "description": rule.get("description", ""),
            }
    return meta


def _pct(num: int, denom: int) -> str:
    """Return percentage as string, handling zero denominator."""
    if denom == 0:
        return "0"
    return f"{100 * num / denom:.0f}"
