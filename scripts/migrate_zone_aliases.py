#!/usr/bin/env python3
"""One-shot migration: rewrite legacy zone-alias names to medallion names.

Brightsmith removed the zone-alias layer (``raw``/``base``/``consumable``/
``ai_ready``). The framework now resolves table namespaces directly to the
physical medallion names — a DQ rule, data contract, or source YAML that still
declares a legacy alias namespace no longer matches its zone.

This helper rewrites a *domain project's* governance inputs from the alias
names to the medallion names:

    raw        -> bronze
    base       -> silver
    consumable -> gold
    ai_ready   -> mcp

It changes only zone-semantic occurrences — namespace-qualified table
references (``raw.college_scorecard`` -> ``bronze.college_scorecard``) and
``namespace:`` / ``zone:`` config values. It deliberately does NOT touch
generic English ("raw data"), cache directories (``data/raw/json_cache``), or
any word that is not a zone identifier.

Usage (run from anywhere; pass the domain project's root):

    python scripts/migrate_zone_aliases.py /path/to/my-domain-project        # dry run
    python scripts/migrate_zone_aliases.py /path/to/my-domain-project --apply

Dry run (the default) prints every file and line that would change. Review the
output, then re-run with ``--apply`` to write the changes. This is a one-time
migration; commit the result in the domain project's own repo.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ALIAS_TO_MEDALLION = {
    "raw": "bronze",
    "base": "silver",
    "consumable": "gold",
    "ai_ready": "mcp",
}

# Governance/config locations that hold live pipeline inputs (relative to the
# project root). Historical output artifacts (dq-results, dq-scorecards,
# lineage events) are intentionally left as-is — they record what happened.
_TARGET_GLOBS = (
    "domain/manifest.yaml",
    "domain/sources/*.yaml",
    "domain/sources/*.yml",
    "domain/concept-mappings/*.yaml",
    "governance/dq-rules/*.json",
    "governance/data-contracts/*.yaml",
    "governance/data-contracts/*.yml",
    "governance/models/**/*.yaml",
)

_ALIASES = "|".join(ALIAS_TO_MEDALLION)

# A namespace-qualified table reference: an alias immediately followed by a dot
# and an identifier char (e.g. raw.college_scorecard, consumable.metrics). The
# lookahead keeps ``data/raw/json_cache`` (slash, not dot) and bare "raw" prose
# untouched.
_TABLE_REF_RE = re.compile(rf"\b({_ALIASES})\.(?=[A-Za-z_])")

# A bare alias used as a config value on a zone/namespace key.
_ZONE_VALUE_RE = re.compile(
    rf"""(?P<key>(?:namespace|zone|target_zone|source_zone|backing_zone)\s*:\s*['"]?)"""
    rf"(?P<alias>{_ALIASES})"
    rf"""(?P<tail>['"]?\s*(?:\#.*)?)$""",
    re.MULTILINE,
)


def _apply_replacements(text: str) -> tuple[str, int]:
    """Return (new_text, num_replacements)."""
    count = 0

    def _table_sub(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return ALIAS_TO_MEDALLION[m.group(1)] + "."

    def _zone_sub(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return m.group("key") + ALIAS_TO_MEDALLION[m.group("alias")] + m.group("tail")

    text = _TABLE_REF_RE.sub(_table_sub, text)
    text = _ZONE_VALUE_RE.sub(_zone_sub, text)
    return text, count


def _iter_target_files(root: Path):
    seen: set[Path] = set()
    for pattern in _TARGET_GLOBS:
        for path in root.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def _diff_lines(before: str, after: str) -> list[str]:
    """Compact per-line report of what changed (old -> new)."""
    changes = []
    for old, new in zip(before.splitlines(), after.splitlines()):
        if old != new:
            changes.append(f"    - {old.strip()}\n    + {new.strip()}")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_dir", help="Path to the domain project root to migrate")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = parser.parse_args()

    root = Path(args.project_dir).expanduser().resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    total_files = 0
    total_repls = 0
    for path in sorted(_iter_target_files(root)):
        original = path.read_text()
        migrated, n = _apply_replacements(original)
        if n == 0:
            continue
        total_files += 1
        total_repls += n
        rel = path.relative_to(root)
        print(f"{rel}  ({n} change{'s' if n != 1 else ''})")
        for line in _diff_lines(original, migrated):
            print(line)
        if args.apply:
            path.write_text(migrated)

    if total_files == 0:
        print("No zone-alias references found — nothing to migrate.")
        return 0

    verb = "Applied" if args.apply else "Would apply"
    print(f"\n{verb} {total_repls} replacement(s) across {total_files} file(s).")
    if not args.apply:
        print("Re-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
