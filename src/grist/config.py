"""Project-level configuration.

Global settings that apply across all zones and pipelines.

Domain projects override these by setting environment variables or calling
grist.config.configure() before any other grist imports.
"""

import os
from pathlib import Path


def _resolve_project_root() -> Path:
    """Determine the project root.

    Priority:
    1. GRIST_PROJECT_ROOT env var (explicit override)
    2. Current working directory (domain project runs from its own root)
    """
    env_root = os.environ.get("GRIST_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


# Project root — domain projects set GRIST_PROJECT_ROOT or run from their root
PROJECT_ROOT = _resolve_project_root()

# Project name (used in catalog naming, lineage namespaces, etc.)
# Override via GRIST_PROJECT_NAME env var for domain projects
PROJECT_NAME = os.environ.get("GRIST_PROJECT_NAME", "grist")

# Human approval gate toggle (global)
# When True: proposals pause for human review before proceeding
# When False: auto-promote if confidence >= module-level CONFIDENCE_FLOOR
#
# This controls ALL human-in-the-loop gates:
#   - Entity resolution: proposed ID mappings
#   - Concept normalization: proposed concept → business term mappings
#   - Data modeling: conceptual → logical → physical model progression
#   - DQ rule lifecycle: proposed → approved progression
REQUIRE_HUMAN_APPROVAL = os.environ.get("GRIST_REQUIRE_HUMAN_APPROVAL", "true").lower() == "true"

# Data quality paths
DQ_RULES_DIR = PROJECT_ROOT / "governance" / "dq-rules"
DQ_RESULTS_DIR = PROJECT_ROOT / "governance" / "dq-results"
DQ_SCORECARDS_DIR = PROJECT_ROOT / "governance" / "dq-scorecards"

# Iceberg catalog paths (shared catalog, per-zone warehouses)
WAREHOUSE_PATH = PROJECT_ROOT / "data" / "raw" / "iceberg_warehouse"
CATALOG_PATH = PROJECT_ROOT / "data" / "catalog" / "catalog.db"


def configure(
    project_root: Path | str | None = None,
    project_name: str | None = None,
    require_human_approval: bool | None = None,
):
    """Reconfigure grist for a domain project.

    Call this before any other grist imports if you need to override defaults.

    Args:
        project_root: Path to the domain project root directory.
        project_name: Name for this project (used in lineage, catalog naming).
        require_human_approval: Toggle for human-in-the-loop gates.
    """
    global PROJECT_ROOT, PROJECT_NAME, REQUIRE_HUMAN_APPROVAL
    global DQ_RULES_DIR, DQ_RESULTS_DIR, DQ_SCORECARDS_DIR
    global WAREHOUSE_PATH, CATALOG_PATH

    if project_root is not None:
        PROJECT_ROOT = Path(project_root).resolve()
    if project_name is not None:
        PROJECT_NAME = project_name
    if require_human_approval is not None:
        REQUIRE_HUMAN_APPROVAL = require_human_approval

    # Rebuild derived paths
    DQ_RULES_DIR = PROJECT_ROOT / "governance" / "dq-rules"
    DQ_RESULTS_DIR = PROJECT_ROOT / "governance" / "dq-results"
    DQ_SCORECARDS_DIR = PROJECT_ROOT / "governance" / "dq-scorecards"
    WAREHOUSE_PATH = PROJECT_ROOT / "data" / "raw" / "iceberg_warehouse"
    CATALOG_PATH = PROJECT_ROOT / "data" / "catalog" / "catalog.db"
