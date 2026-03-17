"""Tests for the three-tier glossary loader.

Validates: registry loading, standard glossary loading, project glossary
composition, term search, tier filtering, and read-only enforcement.
"""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from grist.infra.glossary_loader import (
    ComposedGlossary,
    GlossaryRegistry,
    GlossaryTerm,
    find_matching_term,
    load_project_glossary,
    load_registry,
    load_standard_glossary,
)


# --- Fixtures ---


@pytest.fixture
def tmp_glossary_dir(tmp_path):
    """Create a temporary glossary registry structure."""
    standards_dir = tmp_path / "standards"
    standards_dir.mkdir()
    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()

    # Write a test standard glossary
    test_standard = {
        "glossary_metadata": {
            "name": "test-standard",
            "tier": 1,
            "authority": "Test Authority",
            "version": "1.0",
            "description": "Test standard glossary",
            "term_count": 3,
        },
        "terms": [
            {
                "term_id": "ST-TEST-001",
                "term": "Widget",
                "definition": "A standard widget.",
                "source_reference": "Widget Spec v1",
                "synonyms": ["Gadget", "Thingamajig"],
                "category": "entity",
                "is_cde": True,
                "is_pii": False,
            },
            {
                "term_id": "ST-TEST-002",
                "term": "Widget ID",
                "definition": "Unique identifier for a widget.",
                "source_reference": "Widget Spec v1",
                "synonyms": ["WID"],
                "category": "identifier",
                "is_cde": True,
                "is_pii": False,
            },
            {
                "term_id": "ST-TEST-003",
                "term": "Widget Category",
                "definition": "Classification of widget type.",
                "source_reference": "Widget Spec v1",
                "synonyms": [],
                "category": "classification",
                "is_cde": False,
                "is_pii": False,
            },
        ],
    }

    with open(standards_dir / "test-standard.json", "w") as f:
        json.dump(test_standard, f)

    # Write a test domain glossary
    test_domain = {
        "glossary_metadata": {
            "name": "test-domain",
            "tier": 2,
            "authority": "Community",
            "version": "1.0",
            "description": "Test domain glossary",
            "term_count": 1,
        },
        "terms": [
            {
                "term_id": "DT-TEST-001",
                "term": "Widget Throughput",
                "definition": "Rate of widget processing per unit time.",
                "source_reference": None,
                "synonyms": ["Processing Rate"],
                "category": "metric",
                "is_cde": False,
                "is_pii": False,
            },
        ],
    }

    with open(domains_dir / "test-domain.json", "w") as f:
        json.dump(test_domain, f)

    # Write registry
    registry = {
        "standards": [
            {
                "name": "test-standard",
                "file": "standards/test-standard.json",
                "authority": "Test Authority",
                "term_count": 3,
                "description": "Test standard glossary",
            },
        ],
        "domains": [
            {
                "name": "test-domain",
                "file": "domains/test-domain.json",
                "term_count": 1,
                "description": "Test domain glossary",
            },
        ],
    }

    with open(tmp_path / "registry.yaml", "w") as f:
        yaml.dump(registry, f)

    return tmp_path


@pytest.fixture
def tmp_project_glossary(tmp_path):
    """Create a temporary project glossary with tier metadata."""
    glossary = {
        "glossary_metadata": {
            "version": "3.0",
            "term_count": 4,
            "inherited_from": [
                {"glossary": "test-standard", "tier": 1, "terms_inherited": 2},
            ],
        },
        "terms": [
            {
                "term_id": "BT-001",
                "term": "Widget",
                "definition": "A standard widget.",
                "source": "test-standard",
                "source_tier": 1,
                "upstream_term_id": "ST-TEST-001",
                "read_only": True,
                "category": "entity",
                "synonyms": ["Gadget"],
                "related_terms": ["BT-002"],
                "is_cde": True,
                "is_pii": False,
                "status": "approved",
            },
            {
                "term_id": "BT-002",
                "term": "Widget ID",
                "definition": "Unique identifier for a widget.",
                "source": "test-standard",
                "source_tier": 1,
                "upstream_term_id": "ST-TEST-002",
                "read_only": True,
                "category": "identifier",
                "synonyms": ["WID"],
                "related_terms": [],
                "is_cde": True,
                "is_pii": False,
                "status": "approved",
            },
            {
                "term_id": "BT-003",
                "term": "Pipeline Run ID",
                "definition": "Unique identifier for a pipeline execution.",
                "source": "project-specific",
                "source_tier": 3,
                "upstream_term_id": None,
                "read_only": False,
                "category": "pipeline",
                "synonyms": ["Run ID"],
                "related_terms": [],
                "is_cde": False,
                "is_pii": False,
                "status": "approved",
            },
            {
                "term_id": "BT-004",
                "term": "Quality Score",
                "definition": "Aggregate DQ pass rate for a dataset.",
                "source": "project-specific",
                "source_tier": 3,
                "upstream_term_id": None,
                "read_only": False,
                "category": "pipeline",
                "synonyms": [],
                "related_terms": [],
                "is_cde": False,
                "is_pii": False,
                "status": "proposed",
            },
        ],
    }

    path = tmp_path / "business-glossary.json"
    with open(path, "w") as f:
        json.dump(glossary, f)

    return path


# --- Registry Tests ---


class TestLoadRegistry:
    """Tests for registry loading."""

    def test_missing_registry_returns_empty(self, tmp_path):
        """Missing registry file returns empty registry, not an error."""
        import grist.infra.glossary_loader as gl

        original = gl.REGISTRY_PATH
        gl.REGISTRY_PATH = tmp_path / "nonexistent.yaml"
        try:
            registry = load_registry()
            assert registry.standards == []
            assert registry.domains == []
        finally:
            gl.REGISTRY_PATH = original


# --- Project Glossary Loading ---


class TestLoadProjectGlossary:
    """Tests for loading the composed project glossary."""

    def test_missing_glossary_returns_empty(self, tmp_path):
        """Missing glossary file returns empty composed glossary."""
        glossary = load_project_glossary(tmp_path / "nonexistent.json")
        assert len(glossary.terms) == 0
        assert glossary.version == "0.0"

    def test_load_from_fixture(self, tmp_project_glossary):
        """Load a fixture-based project glossary."""
        glossary = load_project_glossary(tmp_project_glossary)
        assert len(glossary.terms) == 4
        assert len(glossary.get_by_tier(1)) == 2
        assert len(glossary.get_by_tier(3)) == 2


# --- Search ---


class TestSearch:
    """Tests for term search functionality using fixture data."""

    def test_search_by_name(self, tmp_project_glossary):
        """Search finds terms by name substring."""
        glossary = load_project_glossary(tmp_project_glossary)
        results = glossary.search("Widget")
        assert len(results) >= 1
        assert any(t.term == "Widget" for t in results)

    def test_search_by_synonym(self, tmp_project_glossary):
        """Search finds terms by synonym."""
        glossary = load_project_glossary(tmp_project_glossary)
        results = glossary.search("WID")
        assert len(results) >= 1

    def test_search_case_insensitive(self, tmp_project_glossary):
        """Search is case-insensitive."""
        glossary = load_project_glossary(tmp_project_glossary)
        results_upper = glossary.search("WIDGET")
        results_lower = glossary.search("widget")
        assert len(results_upper) == len(results_lower)

    def test_search_no_results(self, tmp_project_glossary):
        """Search returns empty list for no matches."""
        glossary = load_project_glossary(tmp_project_glossary)
        results = glossary.search("zzzznonexistent")
        assert results == []


# --- Find Matching Term (Link-First API) ---


class TestFindMatchingTerm:
    """Tests for the link-first term lookup using fixture data."""

    def test_find_known_term(self, tmp_project_glossary):
        """Find a term that exists in the project glossary."""
        glossary = load_project_glossary(tmp_project_glossary)
        term = find_matching_term("Widget", glossary=glossary)
        assert term is not None
        assert term.term == "Widget"

    def test_find_returns_none_for_unknown(self, tmp_project_glossary):
        """Return None for a term that doesn't exist anywhere."""
        glossary = load_project_glossary(tmp_project_glossary)
        term = find_matching_term("QuantumFluxCapacitor", glossary=glossary)
        assert term is None
