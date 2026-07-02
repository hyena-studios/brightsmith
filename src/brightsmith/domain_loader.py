"""Domain manifest loader.

Reads domain/manifest.yaml and source config files to provide typed
configuration objects. The manifest tells the framework HOW to acquire
data — it does NOT define what the data means.

Optional hints accelerate the pipeline by providing domain knowledge
upfront, but the framework works without them.

Usage:
    python -m brightsmith.domain_loader assign-domain --name "Financial Reporting" [--sub-domain "SEC XBRL"] [--confidence High]
    python -m brightsmith.domain_loader show-domain
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from brightsmith import config

logger = logging.getLogger(__name__)


def _default_manifest_path() -> Path:
    """Resolve the default manifest path from the live config (call-time)."""
    return config.PROJECT_ROOT / "domain" / "manifest.yaml"


def __getattr__(name):
    # Back-compat: ``DEFAULT_MANIFEST_PATH`` and ``PROJECT_ROOT`` were module
    # constants; expose them as live views so configure() takes effect.
    if name == "DEFAULT_MANIFEST_PATH":
        return _default_manifest_path()
    if name == "PROJECT_ROOT":
        return config.PROJECT_ROOT
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass
class SourceConfig:
    """Configuration for a single data source.

    Most sources declare one table (``table:``). Multi-table sources (e.g.
    O*NET's 7-table bulk extract, field evidence: futureproof-data's
    ``onet.yaml``) declare ``tables:`` instead — a list of names, or a
    mapping keyed by table name carrying per-table metadata (per-table
    metadata itself is not consumed by this loader). ``table`` is always
    populated (the first entry of ``tables`` for back-compat with existing
    single-table callers); ``tables`` always holds the full list, and
    defaults to ``[table]`` when not passed explicitly (back-compat for
    callers constructing SourceConfig directly, e.g. tests).
    """

    name: str
    namespace: str
    table: str
    fetch: dict
    entities: dict[int | str, str]
    dedup_grain: list[str]
    cache_dir: Path
    tables: list[str] = field(default_factory=list)
    fetcher_path: str | None = None
    flattener_path: str | None = None

    def __post_init__(self) -> None:
        if not self.tables:
            self.tables = [self.table]

    @property
    def full_table_name(self) -> str:
        return f"{self.namespace}.{self.table}"

    @property
    def full_table_names(self) -> list[str]:
        return [f"{self.namespace}.{t}" for t in self.tables]


@dataclass
class DomainHints:
    """Optional hints that accelerate the pipeline.

    All fields are None by default — the pipeline discovers
    these from EDA if not provided.
    """

    entity_id_field: str | None = None
    time_field: str | None = None
    glossary_inherit: list[str] = field(default_factory=list)
    concept_mappings: Path | None = None
    metrics: Path | None = None
    grouping_taxonomy: Path | None = None
    anomaly_rules: Path | None = None
    chat_context: Path | None = None


@dataclass
class DomainAssignment:
    """Agent-assigned business domain classification.

    Written to manifest.yaml by @domain-context after synthesizing
    domain knowledge. Read by Brightforge for sidebar display.
    """

    name: str
    sub_domain: str | None = None
    confidence: str = "Medium"  # High, Medium, Low
    assigned_by: str = "@domain-context"
    assigned_at: str = ""


@dataclass
class DomainManifest:
    """Top-level domain manifest."""

    name: str
    version: str
    description: str
    sources: list[SourceConfig]
    hints: DomainHints
    domain: DomainAssignment | None = None
    pipeline: dict | None = None
    # Top-level `mcp: {module, class}` block (H1) — names the domain-specific
    # MCP server class serve.py should load instead of the generic
    # BaseMCPServer. Distinct from a `pipeline.zones.mcp` entry (the shape
    # field manifests use), which serve.py also checks as a fallback.
    mcp: dict | None = None


def _resolve_path(path_str: str | None, project_root: Path) -> Path | None:
    """Resolve a path relative to project root, or return None."""
    if path_str is None:
        return None
    p = Path(path_str)
    if p.is_absolute():
        return p
    return project_root / p


def _resolve_table_names(data: dict, source_config_path: Path) -> list[str]:
    """Resolve the table name(s) declared by a source config (H5.1).

    Supports the singular ``table:`` key (one table per source — unchanged
    behavior) and the plural ``tables:`` key for multi-table sources, which
    may be either a YAML list of names or a mapping keyed by table name
    carrying per-table metadata (e.g. ``source_file``/``dedup_grain`` — field
    evidence: futureproof-data's O*NET source declares 5 tables this way).
    Per-table metadata under a mapping form is not consumed here; only the
    table names are extracted.

    Raises:
        KeyError: Neither ``table:`` nor ``tables:`` is present.
        ValueError: ``tables:`` is present but not a list/mapping, or is empty.
    """
    if "table" in data:
        return [data["table"]]

    tables = data.get("tables")
    if tables is None:
        raise KeyError(
            f"Source config {source_config_path} has neither 'table:' (singular) "
            f"nor 'tables:' (plural) — one is required."
        )
    if isinstance(tables, dict):
        names = list(tables.keys())
    elif isinstance(tables, list):
        names = list(tables)
    else:
        raise ValueError(
            f"Source config {source_config_path}: 'tables:' must be a list or "
            f"mapping of table names, got {type(tables).__name__}"
        )
    if not names:
        raise ValueError(f"Source config {source_config_path}: 'tables:' is empty")
    return names


def _load_source_config(source_entry: dict, project_root: Path) -> SourceConfig:
    """Load a source config from its YAML file."""
    source_config_path = _resolve_path(source_entry.get("source_config"), project_root)

    if source_config_path is None or not source_config_path.exists():
        raise FileNotFoundError(
            f"Source config not found: {source_entry.get('source_config')}. "
            f"Expected at: {source_config_path}"
        )

    with open(source_config_path) as f:
        data = yaml.safe_load(f)

    # YAML may parse entity keys as ints or strings depending on quoting;
    # SourceConfig.entities accepts both, so the raw dict is used as-is.
    entities = data.get("entities", {})

    table_names = _resolve_table_names(data, source_config_path)

    return SourceConfig(
        name=data["name"],
        namespace=data["namespace"],
        table=table_names[0],
        tables=table_names,
        fetch=data.get("fetch", {}),
        entities=entities,
        dedup_grain=data.get("dedup_grain", []),
        cache_dir=_resolve_path(data.get("cache_dir", "data/raw/json_cache"), project_root)
        or project_root / "data/raw/json_cache",
        fetcher_path=source_entry.get("fetcher"),
        flattener_path=source_entry.get("flattener"),
    )


def _load_hints(hints_data: dict | None, project_root: Path) -> DomainHints:
    """Parse the optional hints block."""
    if hints_data is None:
        return DomainHints()

    glossary_block = hints_data.get("glossary", {})
    glossary_inherit = glossary_block.get("inherit", []) if glossary_block else []

    return DomainHints(
        entity_id_field=hints_data.get("entity_id_field"),
        time_field=hints_data.get("time_field"),
        glossary_inherit=glossary_inherit,
        concept_mappings=_resolve_path(hints_data.get("concept_mappings"), project_root),
        metrics=_resolve_path(hints_data.get("metrics"), project_root),
        grouping_taxonomy=_resolve_path(hints_data.get("grouping_taxonomy"), project_root),
        anomaly_rules=_resolve_path(hints_data.get("anomaly_rules"), project_root),
        chat_context=_resolve_path(hints_data.get("chat_context"), project_root),
    )


def load_manifest(manifest_path: Path | None = None) -> DomainManifest:
    """Load domain manifest from YAML.

    Args:
        manifest_path: Path to manifest.yaml. Defaults to PROJECT_ROOT/domain/manifest.yaml.

    Returns:
        DomainManifest with typed source configs and hints.

    Raises:
        FileNotFoundError: If the manifest file doesn't exist.
    """
    path = manifest_path or _default_manifest_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Domain manifest not found at {path}. "
            f"Create domain/manifest.yaml to configure your data source."
        )

    project_root = path.parent.parent if manifest_path is None else path.parent

    with open(path) as f:
        data = yaml.safe_load(f)

    sources = [
        _load_source_config(entry, project_root)
        for entry in data.get("sources", [])
    ]

    hints = _load_hints(data.get("hints"), project_root)

    domain = _load_domain(data.get("domain"))

    manifest = DomainManifest(
        name=data.get("name", "unknown"),
        version=data.get("version", "0.0"),
        description=data.get("description", ""),
        sources=sources,
        hints=hints,
        domain=domain,
        pipeline=data.get("pipeline"),
        mcp=data.get("mcp"),
    )

    logger.info(
        "Loaded domain manifest: %s v%s (%d sources, hints: %s)",
        manifest.name,
        manifest.version,
        len(manifest.sources),
        "yes" if hints.entity_id_field else "none",
    )
    return manifest


def _load_domain(domain_data: dict | None) -> DomainAssignment | None:
    """Parse the optional domain block."""
    if domain_data is None or not isinstance(domain_data, dict):
        return None
    name = domain_data.get("name")
    if not name:
        return None
    return DomainAssignment(
        name=name,
        sub_domain=domain_data.get("sub_domain"),
        confidence=domain_data.get("confidence", "Medium"),
        assigned_by=domain_data.get("assigned_by", "@domain-context"),
        assigned_at=domain_data.get("assigned_at", ""),
    )


def get_source(manifest: DomainManifest, source_name: str) -> SourceConfig:
    """Get a specific source config by name.

    Raises:
        KeyError: If the source name is not found in the manifest.
    """
    for source in manifest.sources:
        if source.name == source_name:
            return source
    available = [s.name for s in manifest.sources]
    raise KeyError(
        f"Source '{source_name}' not found in manifest. Available: {available}"
    )


# ---------------------------------------------------------------------------
# Domain assignment
# ---------------------------------------------------------------------------


VALID_CONFIDENCE = {"High", "Medium", "Low"}


def assign_domain(
    domain_name: str,
    sub_domain: str | None = None,
    confidence: str = "Medium",
    manifest_path: Path | None = None,
) -> DomainAssignment:
    """Write the domain assignment to manifest.yaml.

    Reads the existing manifest, adds/updates the domain section,
    and writes back preserving all other fields. The write is atomic
    (temp file + rename) to prevent data loss on crash.

    Note: YAML comments in the manifest will not be preserved through
    the round-trip, as PyYAML discards them on load.

    Args:
        domain_name: The identified business domain (e.g., "Financial Reporting").
        sub_domain: Optional more specific classification.
        confidence: Agent's confidence in the assignment (High/Medium/Low).
        manifest_path: Override for manifest path.

    Returns:
        The DomainAssignment that was written.

    Raises:
        FileNotFoundError: If manifest.yaml doesn't exist.
        ValueError: If domain_name is empty or confidence is invalid.
    """
    if not domain_name or not domain_name.strip():
        raise ValueError("domain_name must be a non-empty string")
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(f"confidence must be one of {VALID_CONFIDENCE}, got '{confidence}'")

    path = manifest_path or _default_manifest_path()
    if not path.exists():
        raise FileNotFoundError(f"Domain manifest not found at {path}")

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    now = datetime.now(UTC).strftime("%Y-%m-%d")

    data["domain"] = {
        "name": domain_name,
        "sub_domain": sub_domain,
        "confidence": confidence,
        "assigned_by": "@domain-context",
        "assigned_at": now,
    }

    # Remove None values for cleaner YAML
    data["domain"] = {k: v for k, v in data["domain"].items() if v is not None}

    # Atomic write: temp file + rename to prevent data loss on crash
    logger.info("Writing domain assignment to %s (YAML comments will not be preserved)", path)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise

    assignment = DomainAssignment(
        name=domain_name,
        sub_domain=sub_domain,
        confidence=confidence,
        assigned_by="@domain-context",
        assigned_at=now,
    )

    logger.info("Domain assigned: %s (confidence: %s)", domain_name, confidence)
    return assignment


def show_domain(manifest_path: Path | None = None) -> DomainAssignment | None:
    """Read the current domain assignment from manifest.yaml.

    Args:
        manifest_path: Override for manifest path.

    Returns:
        DomainAssignment or None if not assigned.
    """
    path = manifest_path or _default_manifest_path()
    if not path.exists():
        return None

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    return _load_domain(data.get("domain"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for domain loader operations."""
    parser = argparse.ArgumentParser(description="Brightsmith Domain Loader")
    subparsers = parser.add_subparsers(dest="command")

    # assign-domain
    assign_p = subparsers.add_parser("assign-domain", help="Write domain assignment to manifest")
    assign_p.add_argument("--name", required=True, help="Business domain name")
    assign_p.add_argument("--sub-domain", default=None, help="More specific sub-domain")
    assign_p.add_argument("--confidence", default="Medium", choices=["High", "Medium", "Low"])

    # show-domain
    subparsers.add_parser("show-domain", help="Show current domain assignment")

    args = parser.parse_args()

    if args.command == "assign-domain":
        _cmd_assign_domain(args)
    elif args.command == "show-domain":
        _cmd_show_domain()
    else:
        parser.print_help()


def _cmd_assign_domain(args: argparse.Namespace) -> None:
    try:
        assignment = assign_domain(
            domain_name=args.name,
            sub_domain=getattr(args, "sub_domain", None),
            confidence=args.confidence,
        )
        print(f"Domain assigned: {assignment.name}")
        if assignment.sub_domain:
            print(f"Sub-domain: {assignment.sub_domain}")
        print(f"Confidence: {assignment.confidence}")
        print(f"Assigned at: {assignment.assigned_at}")
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_show_domain() -> None:
    assignment = show_domain()
    if assignment is None:
        print("No domain assigned yet.")
        print("Run: python -m brightsmith.domain_loader assign-domain --name 'Your Domain'")
        return
    print(f"Domain: {assignment.name}")
    if assignment.sub_domain:
        print(f"Sub-domain: {assignment.sub_domain}")
    print(f"Confidence: {assignment.confidence}")
    print(f"Assigned by: {assignment.assigned_by}")
    print(f"Assigned at: {assignment.assigned_at}")


if __name__ == "__main__":
    main()
