"""Project-level configuration.

Global settings that apply across all zones and pipelines.
"""

from pathlib import Path

# Project root (parent of src/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Project name (used in catalog naming, lineage namespaces, etc.)
PROJECT_NAME = "grist"

# Human approval gate toggle (global)
# When True: proposals pause for human review before proceeding
# When False: auto-promote if confidence >= module-level CONFIDENCE_FLOOR
#
# This controls ALL human-in-the-loop gates:
#   - Entity resolution: proposed ID mappings
#   - Concept normalization: proposed concept → business term mappings
#   - Data modeling: conceptual → logical → physical model progression
#   - DQ rule lifecycle: proposed → approved progression
REQUIRE_HUMAN_APPROVAL = True

# Data quality paths
DQ_RULES_DIR = PROJECT_ROOT / "governance" / "dq-rules"
DQ_RESULTS_DIR = PROJECT_ROOT / "governance" / "dq-results"
DQ_SCORECARDS_DIR = PROJECT_ROOT / "governance" / "dq-scorecards"

# Iceberg catalog paths (shared catalog, per-zone warehouses)
WAREHOUSE_PATH = PROJECT_ROOT / "data" / "raw" / "iceberg_warehouse"
CATALOG_PATH = PROJECT_ROOT / "data" / "catalog" / "catalog.db"
