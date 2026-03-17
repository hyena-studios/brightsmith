"""Three-tier glossary loader.

Loads business terms from a composed hierarchy:
  Tier 1: Standards (read-only, always auto-approved)
  Tier 2: Domains  (shared, auto-approved)
  Tier 3: Project  (local, human approval required)

Projects declare which shared glossaries to inherit via the
`inherited_from` list in their glossary metadata. The loader
merges inherited terms with project-local terms into a single
unified view.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from grist.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

GLOSSARIES_DIR = PROJECT_ROOT / "glossaries"
REGISTRY_PATH = GLOSSARIES_DIR / "registry.yaml"
PROJECT_GLOSSARY_PATH = PROJECT_ROOT / "governance" / "business-glossary.json"


@dataclass
class GlossaryTerm:
    """A single business term from any tier."""

    term_id: str
    term: str
    definition: str
    source: str
    source_tier: int
    upstream_term_id: str | None
    read_only: bool
    category: str | None = None
    synonyms: list[str] = field(default_factory=list)
    related_terms: list[str] = field(default_factory=list)
    is_cde: bool = False
    is_pii: bool = False
    status: str = "approved"


@dataclass
class GlossaryRegistry:
    """Index of available standard and domain glossaries."""

    standards: list[dict]
    domains: list[dict]

    def get_glossary_path(self, name: str) -> Path | None:
        """Get the file path for a named glossary."""
        for entry in self.standards + self.domains:
            if entry["name"] == name:
                return GLOSSARIES_DIR / entry["file"]
        return None

    def list_available(self) -> list[str]:
        """List all available glossary names."""
        return [e["name"] for e in self.standards + self.domains]


@dataclass
class ComposedGlossary:
    """A project glossary composed from inherited tiers + local terms."""

    terms: dict[str, GlossaryTerm]  # keyed by term_id
    inherited_from: list[dict]
    version: str

    def get_term(self, term_id: str) -> GlossaryTerm | None:
        """Look up a term by ID."""
        return self.terms.get(term_id)

    def search(self, query: str) -> list[GlossaryTerm]:
        """Search terms by name or synonym (case-insensitive)."""
        q = query.lower()
        results = []
        for term in self.terms.values():
            if q in term.term.lower():
                results.append(term)
            elif any(q in s.lower() for s in term.synonyms):
                results.append(term)
        return results

    def get_by_tier(self, tier: int) -> list[GlossaryTerm]:
        """Get all terms from a specific tier."""
        return [t for t in self.terms.values() if t.source_tier == tier]

    def get_read_only(self) -> list[GlossaryTerm]:
        """Get all read-only (inherited) terms."""
        return [t for t in self.terms.values() if t.read_only]

    def get_project_terms(self) -> list[GlossaryTerm]:
        """Get all project-specific (Tier 3) terms."""
        return self.get_by_tier(3)


def load_registry() -> GlossaryRegistry:
    """Load the glossary registry index.

    Returns a GlossaryRegistry even if the registry file doesn't exist
    (empty registry — no shared glossaries available).
    """
    if not REGISTRY_PATH.exists():
        logger.info("No glossary registry found at %s — no shared glossaries available", REGISTRY_PATH)
        return GlossaryRegistry(standards=[], domains=[])

    with open(REGISTRY_PATH) as f:
        data = yaml.safe_load(f)

    return GlossaryRegistry(
        standards=data.get("standards", []),
        domains=data.get("domains") or [],
    )


def load_standard_glossary(name: str, registry: GlossaryRegistry | None = None) -> list[GlossaryTerm]:
    """Load a single standard or domain glossary by name.

    Returns an empty list if the glossary is not found.
    """
    if registry is None:
        registry = load_registry()

    glossary_path = registry.get_glossary_path(name)
    if glossary_path is None or not glossary_path.exists():
        logger.warning("Glossary '%s' not found in registry", name)
        return []

    with open(glossary_path) as f:
        data = json.load(f)

    metadata = data.get("glossary_metadata", {})
    tier = metadata.get("tier", 1)

    terms = []
    for t in data.get("terms", []):
        terms.append(GlossaryTerm(
            term_id=t["term_id"],
            term=t["term"],
            definition=t["definition"],
            source=name,
            source_tier=tier,
            upstream_term_id=None,  # these ARE the upstream
            read_only=True,
            category=t.get("category"),
            synonyms=t.get("synonyms", []),
            is_cde=t.get("is_cde", False),
            is_pii=t.get("is_pii", False),
        ))

    logger.info("Loaded %d terms from glossary '%s' (tier %d)", len(terms), name, tier)
    return terms


def load_project_glossary(
    glossary_path: Path | None = None,
) -> ComposedGlossary:
    """Load the project glossary with all tier metadata.

    This loads governance/business-glossary.json and returns a
    ComposedGlossary with terms from all tiers. Terms that were
    inherited from shared glossaries are marked read_only=True.
    """
    path = glossary_path or PROJECT_GLOSSARY_PATH
    if not path.exists():
        logger.info("No project glossary found at %s — starting empty", path)
        return ComposedGlossary(terms={}, inherited_from=[], version="0.0")

    with open(path) as f:
        data = json.load(f)

    metadata = data.get("glossary_metadata", {})
    inherited_from = metadata.get("inherited_from", [])
    version = metadata.get("version", "0.0")

    terms = {}
    for t in data.get("terms", []):
        terms[t["term_id"]] = GlossaryTerm(
            term_id=t["term_id"],
            term=t["term"],
            definition=t["definition"],
            source=t.get("source", "project-specific"),
            source_tier=t.get("source_tier", 3),
            upstream_term_id=t.get("upstream_term_id"),
            read_only=t.get("read_only", False),
            category=t.get("category"),
            synonyms=t.get("synonyms", []),
            related_terms=t.get("related_terms", []),
            is_cde=t.get("is_cde", False),
            is_pii=t.get("is_pii", False),
            status=t.get("status", "approved"),
        )

    logger.info(
        "Loaded project glossary v%s: %d terms (%d inherited, %d project-specific)",
        version,
        len(terms),
        sum(1 for t in terms.values() if t.read_only),
        sum(1 for t in terms.values() if not t.read_only),
    )
    return ComposedGlossary(terms=terms, inherited_from=inherited_from, version=version)


def find_matching_term(
    query: str,
    glossary: ComposedGlossary | None = None,
    registry: GlossaryRegistry | None = None,
) -> GlossaryTerm | None:
    """Search for a matching term across all tiers.

    First checks the project glossary (all tiers already composed),
    then falls back to searching shared glossaries directly.
    This is the primary API for @data-steward's "link first" behavior.
    """
    if glossary is None:
        glossary = load_project_glossary()

    # Search project glossary first (includes inherited terms)
    matches = glossary.search(query)
    if matches:
        return matches[0]

    # Fall back to shared glossaries not yet inherited
    if registry is None:
        registry = load_registry()

    for name in registry.list_available():
        terms = load_standard_glossary(name, registry)
        for term in terms:
            if query.lower() in term.term.lower():
                return term
            if any(query.lower() in s.lower() for s in term.synonyms):
                return term

    return None
