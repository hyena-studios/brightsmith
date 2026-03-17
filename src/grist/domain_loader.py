"""Domain manifest loader.

Reads domain/manifest.yaml and source config files to provide typed
configuration objects. The manifest tells the framework HOW to acquire
data — it does NOT define what the data means.

Optional hints accelerate the pipeline by providing domain knowledge
upfront, but the framework works without them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from grist.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "domain" / "manifest.yaml"


@dataclass
class SourceConfig:
    """Configuration for a single data source."""

    name: str
    namespace: str
    table: str
    fetch: dict
    entities: dict[int | str, str]
    dedup_grain: list[str]
    cache_dir: Path
    fetcher_path: str | None = None
    flattener_path: str | None = None

    @property
    def full_table_name(self) -> str:
        return f"{self.namespace}.{self.table}"


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
class DomainManifest:
    """Top-level domain manifest."""

    name: str
    version: str
    description: str
    sources: list[SourceConfig]
    hints: DomainHints


def _resolve_path(path_str: str | None, project_root: Path) -> Path | None:
    """Resolve a path relative to project root, or return None."""
    if path_str is None:
        return None
    p = Path(path_str)
    if p.is_absolute():
        return p
    return project_root / p


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

    # Ensure entity keys are the right type (YAML may parse them as ints or strings)
    entities = {}
    for k, v in data.get("entities", {}).items():
        entities[k] = v

    return SourceConfig(
        name=data["name"],
        namespace=data["namespace"],
        table=data["table"],
        fetch=data.get("fetch", {}),
        entities=entities,
        dedup_grain=data.get("dedup_grain", []),
        cache_dir=_resolve_path(data.get("cache_dir", "data/raw/json_cache"), project_root),
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
    path = manifest_path or DEFAULT_MANIFEST_PATH
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

    manifest = DomainManifest(
        name=data.get("name", "unknown"),
        version=data.get("version", "0.0"),
        description=data.get("description", ""),
        sources=sources,
        hints=hints,
    )

    logger.info(
        "Loaded domain manifest: %s v%s (%d sources, hints: %s)",
        manifest.name,
        manifest.version,
        len(manifest.sources),
        "yes" if hints.entity_id_field else "none",
    )
    return manifest


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
