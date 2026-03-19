"""Domain project scaffolding — creates a complete Grist domain project.

Usage:
    python -m grist.setup init

This is the programmatic equivalent of @setup agent. Creates the full
directory structure, CLAUDE.md, pyproject.toml, ingestor skeleton,
governance directories, and first spec.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Template directory lives alongside this module
_TEMPLATES_DIR = Path(__file__).parent / "_templates"


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
        "data/raw/iceberg_warehouse",
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
    "grist @ git+https://github.com/jcernauske/grist.git",
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
    """Copy DQ rule templates from framework to domain project."""
    src_templates = _TEMPLATES_DIR.parent.parent.parent / "governance" / "dq-rule-templates"
    dst_templates = root / "governance" / "dq-rule-templates"
    if src_templates.exists():
        for f in src_templates.glob("*.json"):
            shutil.copy2(f, dst_templates / f.name)


def init(
    project_name: str = "my-domain-project",
    description: str = "A Grist domain project",
    output_dir: str | Path | None = None,
) -> Path:
    """Scaffold a complete domain project.

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

    print(f"Scaffolded Grist domain project at: {root}")
    print(f"\nNext steps:")
    print(f"  cd {root}")
    print(f"  uv sync")
    print(f"  # Configure domain/manifest.yaml and domain/sources/")
    print(f"  # Write your first spec in docs/specs/")

    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="Grist domain project scaffolding")
    subparsers = parser.add_subparsers(dest="command")

    init_p = subparsers.add_parser("init", help="Scaffold a new domain project")
    init_p.add_argument("--name", default="my-domain-project", help="Project name")
    init_p.add_argument("--description", default="A Grist domain project", help="Description")
    init_p.add_argument("--output", help="Output directory")

    args = parser.parse_args()

    if args.command == "init":
        init(project_name=args.name, description=args.description, output_dir=args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
