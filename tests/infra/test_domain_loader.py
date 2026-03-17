"""Tests for the domain manifest loader.

Validates: manifest parsing, source config loading, hints parsing,
missing manifest handling, and get_source lookup.
"""

import json
from pathlib import Path

import pytest
import yaml

from grist.domain_loader import (
    DomainHints,
    DomainManifest,
    SourceConfig,
    get_source,
    load_manifest,
)


# --- Fixtures ---


@pytest.fixture
def minimal_manifest(tmp_path):
    """Create a minimal manifest with no hints."""
    source_config = {
        "name": "test_source",
        "namespace": "raw",
        "table": "test_data",
        "fetch": {
            "api": {
                "url_template": "https://example.com/data/{entity_id}.json",
                "rate_limit_seconds": 0.5,
            }
        },
        "entities": {1: "Entity A", 2: "Entity B"},
        "dedup_grain": ["id", "date"],
        "cache_dir": "data/raw/test_cache",
    }

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    with open(sources_dir / "test_source.yaml", "w") as f:
        yaml.dump(source_config, f)

    manifest = {
        "name": "test-domain",
        "version": "1.0",
        "description": "A test domain",
        "sources": [
            {
                "name": "test_source",
                "source_config": "sources/test_source.yaml",
                "fetcher": "sources/fetchers/test_fetcher.py",
                "flattener": "flatten/test_flattener.py",
            }
        ],
    }

    manifest_path = tmp_path / "manifest.yaml"
    with open(manifest_path, "w") as f:
        yaml.dump(manifest, f)

    return manifest_path


@pytest.fixture
def full_manifest(tmp_path):
    """Create a manifest with all hints populated."""
    source_config = {
        "name": "test_source",
        "namespace": "raw",
        "table": "test_data",
        "fetch": {"api": {"url_template": "https://example.com/{id}.json"}},
        "entities": {100: "Company X", 200: "Company Y"},
        "dedup_grain": ["id", "metric", "date"],
        "cache_dir": "data/raw/cache",
    }

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    with open(sources_dir / "test_source.yaml", "w") as f:
        yaml.dump(source_config, f)

    manifest = {
        "name": "test-with-hints",
        "version": "2.0",
        "description": "A test domain with hints",
        "sources": [
            {
                "name": "test_source",
                "source_config": "sources/test_source.yaml",
            }
        ],
        "hints": {
            "entity_id_field": "company_id",
            "time_field": "report_date",
            "glossary": {
                "inherit": ["standard:test-std", "domain:test-dom"],
            },
            "concept_mappings": "concept-mappings/",
            "metrics": "metrics/",
            "grouping_taxonomy": "taxonomy/groups.yaml",
            "anomaly_rules": "anomaly-rules/",
            "chat_context": "chat-context/prompt.md",
        },
    }

    manifest_path = tmp_path / "manifest.yaml"
    with open(manifest_path, "w") as f:
        yaml.dump(manifest, f)

    return manifest_path


# --- Manifest Loading ---


class TestLoadManifest:
    """Tests for loading the domain manifest."""

    def test_load_minimal_manifest(self, minimal_manifest):
        """Load a manifest with no hints block."""
        manifest = load_manifest(minimal_manifest)
        assert manifest.name == "test-domain"
        assert manifest.version == "1.0"
        assert len(manifest.sources) == 1

        # Hints should all be None/empty
        assert manifest.hints.entity_id_field is None
        assert manifest.hints.time_field is None
        assert manifest.hints.glossary_inherit == []
        assert manifest.hints.concept_mappings is None

    def test_load_full_manifest(self, full_manifest):
        """Load a manifest with all hints populated."""
        manifest = load_manifest(full_manifest)
        assert manifest.name == "test-with-hints"
        assert manifest.hints.entity_id_field == "company_id"
        assert manifest.hints.time_field == "report_date"
        assert manifest.hints.glossary_inherit == ["standard:test-std", "domain:test-dom"]
        assert manifest.hints.concept_mappings is not None
        assert manifest.hints.metrics is not None

    def test_missing_manifest_raises(self, tmp_path):
        """Missing manifest file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Domain manifest not found"):
            load_manifest(tmp_path / "nonexistent.yaml")

    def test_missing_source_config_raises(self, tmp_path):
        """Missing source config file raises FileNotFoundError."""
        manifest = {
            "name": "broken",
            "version": "1.0",
            "sources": [
                {"name": "bad", "source_config": "sources/nonexistent.yaml"}
            ],
        }
        manifest_path = tmp_path / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(manifest, f)

        with pytest.raises(FileNotFoundError, match="Source config not found"):
            load_manifest(manifest_path)


# --- Source Config ---


class TestSourceConfig:
    """Tests for source configuration loading."""

    def test_fixture_source_config(self, minimal_manifest):
        """Load source config from fixture manifest."""
        manifest = load_manifest(minimal_manifest)
        source = manifest.sources[0]
        assert source.name == "test_source"
        assert source.namespace == "raw"
        assert source.table == "test_data"
        assert len(source.entities) == 2
        assert source.dedup_grain == ["id", "date"]


# --- Get Source ---


class TestGetSource:
    """Tests for source lookup by name."""

    def test_get_existing_source(self, minimal_manifest):
        """Get source by name from manifest."""
        manifest = load_manifest(minimal_manifest)
        source = get_source(manifest, "test_source")
        assert source.name == "test_source"

    def test_get_nonexistent_source_raises(self, minimal_manifest):
        """Unknown source name raises KeyError."""
        manifest = load_manifest(minimal_manifest)
        with pytest.raises(KeyError, match="not found in manifest"):
            get_source(manifest, "nonexistent_source")


# --- Hints ---


class TestDomainHints:
    """Tests for optional hints parsing."""

    def test_no_hints_all_none(self, minimal_manifest):
        """Manifest without hints block has all-None hints."""
        manifest = load_manifest(minimal_manifest)
        hints = manifest.hints
        assert hints.entity_id_field is None
        assert hints.time_field is None
        assert hints.glossary_inherit == []
        assert hints.concept_mappings is None
        assert hints.metrics is None
        assert hints.grouping_taxonomy is None
        assert hints.anomaly_rules is None
        assert hints.chat_context is None

    def test_hints_paths_resolved(self, full_manifest):
        """Hint paths are resolved relative to project root."""
        manifest = load_manifest(full_manifest)
        assert manifest.hints.concept_mappings is not None
        assert manifest.hints.concept_mappings.is_absolute()
