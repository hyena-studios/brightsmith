"""Domain project scaffolding — creates the directory structure and config
files for a new Brightsmith domain project.

Usage:
    python -m brightsmith.setup init

This is a lightweight, non-agentic scaffolder — the programmatic equivalent
of the directory/config-file portion of the @setup agent, NOT a full
replacement for it. ``init`` creates:

    - The governance/data/docs/tests directory tree (data/bronze/iceberg_warehouse,
      matching config.py's default warehouse path)
    - pyproject.toml (with a brightsmith dependency pinned to the canonical repo)
    - .gitignore
    - A copy of the framework's mandatory DQ rule templates

It does NOT generate ``CLAUDE.md``, a ``domain/manifest.yaml``, an ingestor
skeleton, or a first spec (audit finding H4c) — those require domain
knowledge only the ``@setup``/``@domain-context`` agents (or a human) can
supply. Run the ``@setup`` agent, or write ``domain/manifest.yaml`` and your
first spec by hand, after ``init`` to get a working pipeline.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# Template directory lives alongside this module — this is what ships inside
# the pip-installable wheel (pyproject.toml packages only src/brightsmith).
_TEMPLATES_DIR = Path(__file__).parent / "_templates"
_DQ_TEMPLATES_DIR = _TEMPLATES_DIR / "dq-rule-templates"


def _scaffold_directories(root: Path, project_name: str) -> None:
    """Create the full directory structure for a domain project."""
    dirs = [
        "src/raw",
        "domain/sources",
        "governance/dq-rules",
        "governance/dq-results",
        "governance/dq-scorecards",
        "governance/dq-rule-templates",
        "governance/golden-datasets",
        "governance/models",
        "governance/eda",
        "governance/insights",
        "governance/lineage",
        "governance/policies",
        "governance/pii-scans",
        "governance/reviews",
        "governance/audit-trail",
        "governance/data-contracts",
        "governance/chaos-manifests",
        "glossaries/standards",
        "glossaries/domains",
        "data/bronze/iceberg_warehouse",
        "data/catalog",
        "docs/specs",
        "docs/sessions",
        "tests/raw",
        "tests/infra",
        "tests/integration",
        ".claude/agents",
    ]
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)

    # Create __init__.py files
    for init_dir in ["src/raw", "tests/raw", "tests/infra", "tests/integration"]:
        init_file = root / init_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")


def _write_pyproject(root: Path, project_name: str, description: str) -> None:
    """Generate pyproject.toml."""
    content = f'''[project]
name = "{project_name}"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.11"
dependencies = [
    "brightsmith @ git+https://github.com/hyena-studios/brightsmith.git",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.6",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
markers = [
    "network: tests that require network access",
]
addopts = "-m \\'not network\\'"
'''
    (root / "pyproject.toml").write_text(content)


def _write_gitignore(root: Path) -> None:
    """Generate .gitignore."""
    content = """data/
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
build/
.venv/
"""
    (root / ".gitignore").write_text(content)


def _copy_dq_templates(root: Path) -> None:
    """Copy the framework's mandatory DQ rule templates into the new project.

    Reads from the PACKAGED copy (``src/brightsmith/_templates/dq-rule-templates/``),
    not the repo-root ``governance/`` directory — the latter only exists in a
    source checkout of the framework and is invisible to a pip-installed
    wheel, which packages only ``src/brightsmith`` (audit finding H4a: this
    used to resolve via ``_TEMPLATES_DIR.parent.parent.parent``, silently
    no-op'd for every non-source-checkout consumer).

    Fails loudly if the packaged templates are missing or empty rather than
    silently scaffolding an empty directory — "mandatory patterns for gold
    zone" that never arrive is a governance gap, not a soft degrade.

    Raises:
        FileNotFoundError: The packaged templates directory is missing or
            contains no ``.json`` files.
    """
    dst_templates = root / "governance" / "dq-rule-templates"
    dst_templates.mkdir(parents=True, exist_ok=True)

    if not _DQ_TEMPLATES_DIR.exists():
        raise FileNotFoundError(
            f"DQ rule templates not found at {_DQ_TEMPLATES_DIR}. This is a "
            f"packaging defect in the installed brightsmith package (they should "
            f"ship inside src/brightsmith/_templates/dq-rule-templates/), not "
            f"something the consumer project can fix."
        )

    template_files = sorted(_DQ_TEMPLATES_DIR.glob("*.json"))
    if not template_files:
        raise FileNotFoundError(
            f"DQ rule templates directory exists but contains no .json files: "
            f"{_DQ_TEMPLATES_DIR}"
        )

    for f in template_files:
        shutil.copy2(f, dst_templates / f.name)


def init(
    project_name: str = "my-domain-project",
    description: str = "A Brightsmith domain project",
    output_dir: str | Path | None = None,
) -> Path:
    """Scaffold a domain project's directory structure and config files.

    See the module docstring for exactly what this does (and does not)
    generate.

    Args:
        project_name: Name for the project.
        description: Short description.
        output_dir: Where to create the project. Defaults to cwd/project_name.

    Returns:
        Path to the created project root.
    """
    root = Path(output_dir) if output_dir else Path.cwd() / project_name
    root.mkdir(parents=True, exist_ok=True)

    _scaffold_directories(root, project_name)
    _write_pyproject(root, project_name, description)
    _write_gitignore(root)
    _copy_dq_templates(root)

    print(f"Scaffolded Brightsmith domain project at: {root}")
    print("\nNext steps:")
    print(f"  cd {root}")
    print("  uv sync")
    print("  # Configure domain/manifest.yaml and domain/sources/")
    print("  # Write your first spec in docs/specs/")

    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="Brightsmith domain project scaffolding")
    subparsers = parser.add_subparsers(dest="command")

    init_p = subparsers.add_parser("init", help="Scaffold a new domain project")
    init_p.add_argument("--name", default="my-domain-project", help="Project name")
    init_p.add_argument("--description", default="A Brightsmith domain project", help="Description")
    init_p.add_argument("--output", help="Output directory")

    args = parser.parse_args()

    if args.command == "init":
        init(project_name=args.name, description=args.description, output_dir=args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
