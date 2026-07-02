"""Project-level configuration.

Global settings that apply across all zones and pipelines.

Domain projects override these by setting environment variables or calling
``brightsmith.config.configure()``. Unlike the previous design, ``configure()``
now takes effect *after* other modules have been imported — consumers read the
live configuration through :func:`get_config` (or the legacy module-level names,
which resolve to the same live snapshot) at call time, not at import time.

Resolution priority (highest first):
    1. ``configure(...)`` arguments / direct attribute assignment
    2. ``BRIGHTSMITH_*`` environment variables
    3. ``GRIST_*`` environment variables (deprecated — emits a DeprecationWarning)
    4. Built-in defaults (project root = current working directory)
"""

from __future__ import annotations

import dataclasses
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    # The UPPER_CASE module-level names below are served dynamically at runtime by
    # ``_ConfigModule.__getattr__`` (see the back-compat shim at the bottom of this
    # file). Declaring them here — annotation-only, so no runtime binding is created
    # and the shim still handles access — lets type checkers resolve
    # ``from brightsmith.config import PROJECT_ROOT`` and friends.
    PROJECT_ROOT: Path
    PROJECT_NAME: str
    REQUIRE_HUMAN_APPROVAL: bool
    CONFIDENCE_FLOOR: float
    DQ_RULES_DIR: Path
    DQ_RESULTS_DIR: Path
    DQ_SCORECARDS_DIR: Path
    DQ_TEMPLATES_DIR: Path
    GOLDEN_DATASETS_DIR: Path
    PIPELINE_STATE_DIR: Path
    APPROVALS_DIR: Path
    AUDIT_TRAIL_DIR: Path
    CAB_DECISIONS_DIR: Path
    WAREHOUSE_PATH: Path
    CATALOG_PATH: Path
    GOVERNANCE_WAREHOUSE: Path


@overload
def _env(name: str, *, grist: str | None = ..., default: str) -> str: ...
@overload
def _env(name: str, *, grist: str | None = ..., default: None = ...) -> str | None: ...
def _env(name: str, *, grist: str | None = None, default: str | None = None) -> str | None:
    """Read an environment variable, honouring the deprecated ``GRIST_*`` fallback.

    Reading a ``GRIST_*`` variable (when the ``BRIGHTSMITH_*`` equivalent is unset)
    emits a :class:`DeprecationWarning` but still returns the value (decision D6).
    A non-``None`` ``default`` guarantees a ``str`` return (see overloads).
    """
    val = os.environ.get(name)
    if val is not None:
        return val
    if grist is not None:
        gval = os.environ.get(grist)
        if gval is not None:
            warnings.warn(
                f"Environment variable {grist} is deprecated; use {name} instead. "
                "GRIST_* variables will be removed in a future release.",
                DeprecationWarning,
                stacklevel=2,
            )
            return gval
    return default


@dataclass(frozen=True)
class BrightsmithConfig:
    """Immutable snapshot of the resolved Brightsmith configuration.

    Obtain the current snapshot via :func:`get_config`. The snapshot is replaced
    wholesale whenever :func:`configure` is called or a legacy module-level name is
    assigned, so a reference held by a caller is always internally consistent.
    """

    project_root: Path
    project_name: str
    require_human_approval: bool
    confidence_floor: float
    # Data quality paths
    dq_rules_dir: Path
    dq_results_dir: Path
    dq_scorecards_dir: Path
    dq_templates_dir: Path
    # Golden datasets
    golden_datasets_dir: Path
    # Governance workflow dirs
    pipeline_state_dir: Path
    approvals_dir: Path
    audit_trail_dir: Path
    cab_decisions_dir: Path
    # Iceberg catalog / warehouses
    warehouse_path: Path
    catalog_path: Path
    governance_warehouse: Path


# Maps the legacy module-level (UPPER_CASE) names to dataclass fields. Used by the
# back-compat shim (module __getattr__/__setattr__) so existing imports such as
# ``from brightsmith.config import PROJECT_ROOT`` keep working for one release.
_FIELD_MAP = {
    "PROJECT_ROOT": "project_root",
    "PROJECT_NAME": "project_name",
    "REQUIRE_HUMAN_APPROVAL": "require_human_approval",
    "CONFIDENCE_FLOOR": "confidence_floor",
    "DQ_RULES_DIR": "dq_rules_dir",
    "DQ_RESULTS_DIR": "dq_results_dir",
    "DQ_SCORECARDS_DIR": "dq_scorecards_dir",
    "DQ_TEMPLATES_DIR": "dq_templates_dir",
    "GOLDEN_DATASETS_DIR": "golden_datasets_dir",
    "PIPELINE_STATE_DIR": "pipeline_state_dir",
    "APPROVALS_DIR": "approvals_dir",
    "AUDIT_TRAIL_DIR": "audit_trail_dir",
    "CAB_DECISIONS_DIR": "cab_decisions_dir",
    "WAREHOUSE_PATH": "warehouse_path",
    "CATALOG_PATH": "catalog_path",
    "GOVERNANCE_WAREHOUSE": "governance_warehouse",
}


def _derive(
    project_root: Path,
    project_name: str,
    require_human_approval: bool,
    confidence_floor: float,
) -> BrightsmithConfig:
    """Build a full config snapshot from the four primary inputs."""
    pr = Path(project_root)
    return BrightsmithConfig(
        project_root=pr,
        project_name=project_name,
        require_human_approval=require_human_approval,
        confidence_floor=confidence_floor,
        dq_rules_dir=pr / "governance" / "dq-rules",
        dq_results_dir=pr / "governance" / "dq-results",
        dq_scorecards_dir=pr / "governance" / "dq-scorecards",
        dq_templates_dir=pr / "governance" / "dq-rule-templates",
        golden_datasets_dir=pr / "governance" / "golden-datasets",
        pipeline_state_dir=pr / "governance" / "pipeline-state",
        approvals_dir=pr / "governance" / "approvals",
        audit_trail_dir=pr / "governance" / "audit-trail",
        cab_decisions_dir=pr / "governance" / "cab-decisions",
        warehouse_path=pr / "data" / "bronze" / "iceberg_warehouse",
        catalog_path=pr / "data" / "catalog" / "catalog.db",
        governance_warehouse=pr / "data" / "governance" / "iceberg_warehouse",
    )


def _build_from_env() -> BrightsmithConfig:
    """Resolve the default configuration from environment variables."""
    env_root = _env("BRIGHTSMITH_PROJECT_ROOT", grist="GRIST_PROJECT_ROOT")
    project_root = Path(env_root).resolve() if env_root else Path.cwd().resolve()
    project_name = _env("BRIGHTSMITH_PROJECT_NAME", grist="GRIST_PROJECT_NAME", default="brightsmith")
    require_human_approval = (
        _env(
            "BRIGHTSMITH_REQUIRE_HUMAN_APPROVAL",
            grist="GRIST_REQUIRE_HUMAN_APPROVAL",
            default="true",
        ).lower()
        == "true"
    )
    confidence_floor = float(
        _env("BRIGHTSMITH_CONFIDENCE_FLOOR", grist="GRIST_CONFIDENCE_FLOOR", default="0.7")
    )
    return _derive(project_root, project_name, require_human_approval, confidence_floor)


# The single source of truth — replaced wholesale by configure()/assignment.
_CONFIG: BrightsmithConfig = _build_from_env()


def get_config() -> BrightsmithConfig:
    """Return the current frozen configuration snapshot.

    Always reflects the latest :func:`configure` call (or legacy attribute
    assignment), even from modules imported before ``configure`` was called.
    """
    return _CONFIG


def configure(
    project_root: Path | str | None = None,
    project_name: str | None = None,
    require_human_approval: bool | None = None,
) -> BrightsmithConfig:
    """Reconfigure brightsmith for a domain project.

    Unlike the previous implementation, this takes effect even for modules that
    were already imported, because consumers read paths at call time via
    :func:`get_config` (or the live legacy names).

    Args:
        project_root: Path to the domain project root directory.
        project_name: Name for this project (used in lineage, catalog naming).
        require_human_approval: Toggle for human-in-the-loop gates.

    Returns:
        The newly-resolved configuration snapshot.
    """
    global _CONFIG
    pr = Path(project_root).resolve() if project_root is not None else _CONFIG.project_root
    pn = project_name if project_name is not None else _CONFIG.project_name
    rha = require_human_approval if require_human_approval is not None else _CONFIG.require_human_approval
    _CONFIG = _derive(pr, pn, rha, _CONFIG.confidence_floor)
    # Drop any catalog cached under the previous configuration so a later
    # get_catalog rebuilds against the new project/warehouse. Imported lazily to
    # avoid a config <-> iceberg_setup import cycle.
    try:
        from brightsmith.infra.iceberg_setup import reset_catalog_cache

        reset_catalog_cache()
    except ImportError:
        pass
    return _CONFIG


# ---------------------------------------------------------------------------
# Back-compat shim
#
# Domain packs and tests still import/patch the module-level UPPER_CASE names
# (e.g. ``from brightsmith.config import PROJECT_ROOT`` or
# ``config.DQ_RULES_DIR = tmp``). We expose them as live views onto _CONFIG by
# swapping this module's class for one that intercepts attribute access:
#   * reads resolve from the current snapshot
#   * writing a PRIMARY input (project_root/name/approval/floor) recomputes the
#     derived paths, exactly like configure() — so ``config.PROJECT_ROOT = x``
#     can never leave WAREHOUSE_PATH/DQ_RULES_DIR/... pointing at the old root
#   * writing a DERIVED path (e.g. DQ_RULES_DIR) overrides just that one field
#     (single-path test-override semantics)
# Because managed names never become real module __dict__ entries, monkeypatch +
# undo never leaves a stale value shadowing the live config.
# ---------------------------------------------------------------------------

# Primary inputs to _derive(); assigning any of these must recompute the derived
# paths so the snapshot stays internally consistent (fixes A1 asymmetry).
_PRIMARY_FIELDS = frozenset({
    "project_root", "project_name", "require_human_approval", "confidence_floor",
})


class _ConfigModule(ModuleType):
    def __getattr__(self, name: str):  # only called when normal lookup fails
        field = _FIELD_MAP.get(name)
        if field is not None:
            return getattr(_CONFIG, field)
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:
        field = _FIELD_MAP.get(name)
        if field is not None:
            global _CONFIG
            if field in _PRIMARY_FIELDS:
                # Recompute derived paths from the new primary inputs, matching
                # configure(). Prevents the A1 asymmetry where assigning
                # project_root left the derived paths stale.
                base = dataclasses.replace(_CONFIG, **{field: value})
                _CONFIG = _derive(
                    base.project_root, base.project_name,
                    base.require_human_approval, base.confidence_floor,
                )
            else:
                # Derived-path override: change only this field.
                _CONFIG = dataclasses.replace(_CONFIG, **{field: value})
        else:
            super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ConfigModule
