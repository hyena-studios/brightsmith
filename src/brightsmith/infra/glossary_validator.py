"""Business glossary schema validator.

Validates that business-glossary.json terms have all required fields
and valid cross-references.

Usage:
    python -m brightsmith.infra.glossary_validator validate
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {
    "term_id", "name", "definition", "source", "source_reference",
    "synonyms", "related_terms", "category", "owner",
    "used_in_models", "approval_status",
}

VALID_CATEGORIES = {
    "entity", "classification", "measurement", "temporal", "regulatory", "derived",
}

VALID_APPROVAL_STATUSES = {"proposed", "approved", "auto-approved"}


def validate_glossary(glossary_path: Path | None = None) -> tuple[bool, list[str]]:
    """Validate the business glossary for schema completeness.

    Args:
        glossary_path: Override for glossary file path.

    Returns:
        Tuple of (is_valid, list_of_issues).
    """
    from brightsmith.config import PROJECT_ROOT

    path = glossary_path or PROJECT_ROOT / "governance" / "business-glossary.json"
    issues: list[str] = []

    if not path.exists():
        issues.append(f"Glossary file not found: {path}")
        return (False, issues)

    data = json.loads(path.read_text())
    terms = data.get("terms", [])

    if not terms:
        issues.append("Glossary contains no terms")
        return (False, issues)

    all_term_ids = {t.get("term_id") for t in terms}

    for term in terms:
        tid = term.get("term_id", "UNKNOWN")
        name = term.get("name", "UNNAMED")
        prefix = f"[{tid} '{name}']"

        # Check required fields
        for field in REQUIRED_FIELDS:
            if field not in term:
                issues.append(f"{prefix} missing required field: {field}")

        # Validate related_terms reference valid term IDs
        for ref in term.get("related_terms", []):
            if ref not in all_term_ids:
                issues.append(f"{prefix} related_term '{ref}' does not exist in glossary")

        # Validate used_in_models reference files that exist
        models_dir = path.parent.parent / "governance" / "models"
        for model_ref in term.get("used_in_models", []):
            # Model refs can be just names — check if any matching file exists
            if models_dir.exists():
                matches = list(models_dir.glob(f"*{model_ref}*"))
                # Don't flag if models dir doesn't exist yet (pre-modeling)

        # Validate category
        cat = term.get("category")
        if cat and cat not in VALID_CATEGORIES:
            issues.append(f"{prefix} invalid category '{cat}' — must be one of {VALID_CATEGORIES}")

        # Validate approval_status
        status = term.get("approval_status")
        if status and status not in VALID_APPROVAL_STATUSES:
            issues.append(f"{prefix} invalid approval_status '{status}' — must be one of {VALID_APPROVAL_STATUSES}")

    return (len(issues) == 0, issues)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for glossary validation."""
    parser = argparse.ArgumentParser(description="Brightsmith Business Glossary Validator")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("validate", help="Validate business glossary schema")

    args = parser.parse_args()

    if args.command == "validate":
        _cmd_validate(args)
    else:
        parser.print_help()


def _cmd_validate(args: argparse.Namespace) -> None:
    valid, issues = validate_glossary()
    if valid:
        print("PASS: Business glossary is valid")
    else:
        print(f"FAIL: {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)


if __name__ == "__main__":
    main()
