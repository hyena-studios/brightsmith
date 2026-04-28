"""Tests for the domain manifest loader.

Validates: manifest parsing, source config loading, hints parsing,
missing manifest handling, and get_source lookup.
"""


import pytest
import yaml

from brightsmith.domain_loader import (
    DomainAssignment,
    assign_domain,
    get_source,
    load_manifest,
    show_domain,
)


# --- Fixtures ---


@pytest.fixture
def minimal_manifest(tmp_path):
    """Create a minimal manifest with no hints."""
    source_config = {
        "name": "test_source",
        "namespace": "bronze",
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
        "namespace": "bronze",
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
        assert source.namespace == "bronze"
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


# --- Domain Assignment ---


@pytest.fixture
def manifest_with_domain(tmp_path):
    """Create a manifest with a domain section."""
    source_config = {
        "name": "test_source",
        "namespace": "bronze",
        "table": "test_data",
        "fetch": {"api": {"url_template": "https://example.com/{id}.json"}},
        "entities": {1: "Entity A"},
        "dedup_grain": ["id"],
        "cache_dir": "data/raw/cache",
    }

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    with open(sources_dir / "test_source.yaml", "w") as f:
        yaml.dump(source_config, f)

    manifest = {
        "name": "test-with-domain",
        "version": "1.0",
        "description": "A test domain with assignment",
        "domain": {
            "name": "Financial Reporting",
            "sub_domain": "SEC XBRL Filings",
            "confidence": "High",
            "assigned_by": "@domain-context",
            "assigned_at": "2026-03-25",
        },
        "sources": [
            {"name": "test_source", "source_config": "sources/test_source.yaml"}
        ],
    }

    manifest_path = tmp_path / "manifest.yaml"
    with open(manifest_path, "w") as f:
        yaml.dump(manifest, f)

    return manifest_path


class TestDomainAssignment:
    """Tests for domain assignment feature."""

    def test_load_manifest_with_domain(self, manifest_with_domain):
        """domain.name is parsed into DomainAssignment."""
        manifest = load_manifest(manifest_with_domain)
        assert manifest.domain is not None
        assert manifest.domain.name == "Financial Reporting"
        assert manifest.domain.sub_domain == "SEC XBRL Filings"
        assert manifest.domain.confidence == "High"
        assert manifest.domain.assigned_by == "@domain-context"

    def test_load_manifest_without_domain(self, minimal_manifest):
        """Missing domain section returns domain=None."""
        manifest = load_manifest(minimal_manifest)
        assert manifest.domain is None

    def test_assign_domain_creates_section(self, minimal_manifest):
        """assign_domain() adds domain section to manifest."""
        assignment = assign_domain("Financial Reporting", manifest_path=minimal_manifest)

        assert assignment.name == "Financial Reporting"
        assert assignment.assigned_at  # Non-empty

        # Verify it was written to disk
        data = yaml.safe_load(minimal_manifest.read_text())
        assert data["domain"]["name"] == "Financial Reporting"

    def test_assign_domain_preserves_existing(self, minimal_manifest):
        """Existing manifest fields (sources, name) are unchanged."""
        data_before = yaml.safe_load(minimal_manifest.read_text())
        original_name = data_before["name"]
        original_sources = data_before["sources"]

        assign_domain("Financial Reporting", manifest_path=minimal_manifest)

        data_after = yaml.safe_load(minimal_manifest.read_text())
        assert data_after["name"] == original_name
        assert data_after["sources"] == original_sources

    def test_assign_domain_updates_existing(self, manifest_with_domain):
        """Re-running assign_domain() updates rather than duplicates."""
        assign_domain("Healthcare Claims", confidence="Low", manifest_path=manifest_with_domain)

        data = yaml.safe_load(manifest_with_domain.read_text())
        assert data["domain"]["name"] == "Healthcare Claims"
        assert data["domain"]["confidence"] == "Low"

    def test_assign_domain_with_sub_domain(self, minimal_manifest):
        """sub_domain field is written when provided."""
        assign_domain("Financial Reporting", sub_domain="SEC XBRL", manifest_path=minimal_manifest)

        data = yaml.safe_load(minimal_manifest.read_text())
        assert data["domain"]["sub_domain"] == "SEC XBRL"

    def test_assign_domain_without_sub_domain(self, minimal_manifest):
        """sub_domain is omitted from YAML when None."""
        assign_domain("Financial Reporting", manifest_path=minimal_manifest)

        data = yaml.safe_load(minimal_manifest.read_text())
        assert "sub_domain" not in data["domain"]

    def test_assign_domain_default_confidence(self, minimal_manifest):
        """Defaults to 'Medium' confidence."""
        assign_domain("Financial Reporting", manifest_path=minimal_manifest)

        data = yaml.safe_load(minimal_manifest.read_text())
        assert data["domain"]["confidence"] == "Medium"

    def test_assign_domain_timestamps(self, minimal_manifest):
        """assigned_at is populated with current date."""
        assignment = assign_domain("Financial Reporting", manifest_path=minimal_manifest)

        assert assignment.assigned_at  # Non-empty
        # Should be a valid date format YYYY-MM-DD
        assert len(assignment.assigned_at) == 10
        assert assignment.assigned_at.count("-") == 2

    def test_show_domain(self, manifest_with_domain):
        """show_domain reads current assignment."""
        assignment = show_domain(manifest_with_domain)
        assert assignment is not None
        assert assignment.name == "Financial Reporting"

    def test_show_domain_none(self, minimal_manifest):
        """show_domain returns None when no domain assigned."""
        assignment = show_domain(minimal_manifest)
        assert assignment is None

    def test_domain_assignment_dataclass(self):
        """DomainAssignment serializes correctly."""
        da = DomainAssignment(
            name="Test Domain",
            sub_domain="Sub",
            confidence="High",
            assigned_by="@test",
            assigned_at="2026-03-25",
        )
        assert da.name == "Test Domain"
        assert da.sub_domain == "Sub"
        assert da.confidence == "High"

    def test_assign_domain_missing_manifest(self, tmp_path):
        """assign_domain raises FileNotFoundError for missing manifest."""
        with pytest.raises(FileNotFoundError):
            assign_domain("Test", manifest_path=tmp_path / "nonexistent.yaml")

    def test_assign_domain_empty_name_raises(self, minimal_manifest):
        """Empty domain name raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            assign_domain("", manifest_path=minimal_manifest)
        with pytest.raises(ValueError, match="non-empty"):
            assign_domain("   ", manifest_path=minimal_manifest)

    def test_assign_domain_invalid_confidence_raises(self, minimal_manifest):
        """Invalid confidence raises ValueError."""
        with pytest.raises(ValueError, match="confidence"):
            assign_domain("Test", confidence="YOLO", manifest_path=minimal_manifest)

    def test_load_manifest_domain_string_ignored(self, tmp_path):
        """domain set to a plain string degrades to None."""
        source_config = {
            "name": "test_source", "namespace": "bronze", "table": "t",
            "fetch": {}, "entities": {}, "dedup_grain": [],
            "cache_dir": "data/raw/cache",
        }
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir()
        (sources_dir / "test_source.yaml").write_text(yaml.dump(source_config))

        manifest = {
            "name": "test", "version": "1.0", "description": "",
            "domain": "just a string",
            "sources": [{"name": "test_source", "source_config": "sources/test_source.yaml"}],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))

        result = load_manifest(path)
        assert result.domain is None

    def test_load_manifest_domain_empty_dict_ignored(self, tmp_path):
        """domain set to empty dict degrades to None."""
        source_config = {
            "name": "test_source", "namespace": "bronze", "table": "t",
            "fetch": {}, "entities": {}, "dedup_grain": [],
            "cache_dir": "data/raw/cache",
        }
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir()
        (sources_dir / "test_source.yaml").write_text(yaml.dump(source_config))

        manifest = {
            "name": "test", "version": "1.0", "description": "",
            "domain": {},
            "sources": [{"name": "test_source", "source_config": "sources/test_source.yaml"}],
        }
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.dump(manifest))

        result = load_manifest(path)
        assert result.domain is None
