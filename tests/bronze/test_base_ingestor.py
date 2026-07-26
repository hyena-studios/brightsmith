"""Tests for the BaseIngestor ABC.

Validates: ABC contract enforcement, generic ingest pipeline,
dedup behavior, and metadata enrichment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pyiceberg.schema import Schema
from pyiceberg.types import DateType, IntegerType, NestedField, StringType, TimestamptzType

import brightsmith.config as config
from brightsmith.bronze.base_ingestor import BaseIngestor
from brightsmith.domain_loader import DomainHints, DomainManifest, SourceConfig
from brightsmith.infra.iceberg_setup import get_catalog, read_with_duckdb
from brightsmith.infra.lineage import query_lineage_events

# --- Test Ingestor (minimal concrete implementation) ---


SIMPLE_SCHEMA = Schema(
    NestedField(field_id=1, name="id", field_type=IntegerType(), required=True),
    NestedField(field_id=2, name="name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="value", field_type=StringType(), required=False),
    NestedField(field_id=4, name="ingested_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=5, name="source_url", field_type=StringType(), required=True),
    NestedField(field_id=6, name="source_method", field_type=StringType(), required=True),
    NestedField(field_id=7, name="load_date", field_type=DateType(), required=True),
)


class SimpleIngestor(BaseIngestor):
    """Minimal concrete ingestor for testing the framework."""

    def __init__(self, source_config, manifest, test_data=None):
        super().__init__(source_config, manifest)
        self.test_data = test_data or {}

    def get_schema(self) -> Schema:
        return SIMPLE_SCHEMA

    def fetch(self, entities: dict, method: str, **kwargs) -> dict[Any, Any]:
        return {eid: self.test_data.get(eid, []) for eid in entities}

    def flatten(self, raw_data: Any, entity_id: Any) -> list[dict]:
        return raw_data  # test data is already flat


def _make_source_config(**overrides) -> SourceConfig:
    defaults = {
        "name": "test_source",
        "namespace": "bronze",
        "table": "test_data",
        "fetch": {"api": {"url_template": "https://example.com/{id}"}},
        "entities": {1: "Entity A", 2: "Entity B"},
        "dedup_grain": ["id", "name"],
        "cache_dir": Path("/tmp/test_cache"),
    }
    defaults.update(overrides)
    return SourceConfig(**defaults)


def _make_manifest(source: SourceConfig) -> DomainManifest:
    return DomainManifest(
        name="test-domain",
        version="1.0",
        description="Test domain",
        sources=[source],
        hints=DomainHints(),
    )


# --- Fixtures ---


@pytest.fixture
def tmp_workspace(tmp_path):
    return {
        "warehouse": tmp_path / "warehouse",
        "catalog": tmp_path / "catalog.db",
    }


# --- ABC Contract ---


class TestABCContract:
    """Tests that the ABC enforces the contract."""

    def test_cannot_instantiate_abc(self):
        """BaseIngestor cannot be instantiated directly."""
        source = _make_source_config()
        manifest = _make_manifest(source)
        with pytest.raises(TypeError, match="abstract method"):
            BaseIngestor(source, manifest)

    def test_must_implement_fetch(self):
        """Subclass missing fetch() cannot be instantiated."""

        class NoFetch(BaseIngestor):
            def flatten(self, raw_data, entity_id):
                return []

            def get_schema(self):
                return SIMPLE_SCHEMA

        source = _make_source_config()
        manifest = _make_manifest(source)
        with pytest.raises(TypeError, match="abstract method"):
            NoFetch(source, manifest)

    def test_must_implement_flatten(self):
        """Subclass missing flatten() cannot be instantiated."""

        class NoFlatten(BaseIngestor):
            def fetch(self, entities, method, **kwargs):
                return {}

            def get_schema(self):
                return SIMPLE_SCHEMA

        source = _make_source_config()
        manifest = _make_manifest(source)
        with pytest.raises(TypeError, match="abstract method"):
            NoFlatten(source, manifest)

    def test_must_implement_get_schema(self):
        """Subclass missing get_schema() cannot be instantiated."""

        class NoSchema(BaseIngestor):
            def fetch(self, entities, method, **kwargs):
                return {}

            def flatten(self, raw_data, entity_id):
                return []

        source = _make_source_config()
        manifest = _make_manifest(source)
        with pytest.raises(TypeError, match="abstract method"):
            NoSchema(source, manifest)

    def test_concrete_implementation_works(self):
        """Complete implementation can be instantiated."""
        source = _make_source_config()
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest)
        assert ingestor.source == source
        assert ingestor.manifest == manifest


# --- Ingest Pipeline ---


class TestIngestPipeline:
    """Tests for the generic ingest pipeline."""

    def test_ingest_writes_to_iceberg(self, tmp_workspace):
        """Ingest creates table and writes data."""
        test_data = {
            1: [{"id": 1, "name": "alpha", "value": "100"}],
            2: [{"id": 2, "name": "beta", "value": "200"}],
        }
        source = _make_source_config()
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest, test_data)

        results = ingestor.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )

        assert results[1]["rows"] == 1
        assert results[2]["rows"] == 1

        catalog = get_catalog(tmp_workspace["warehouse"], tmp_workspace["catalog"])
        table = catalog.load_table("bronze.test_data")
        rows = read_with_duckdb(table)
        assert len(rows) == 2

    def test_ingest_adds_metadata(self, tmp_workspace):
        """Framework adds ingested_at, source_url, source_method, load_date."""
        test_data = {1: [{"id": 1, "name": "alpha", "value": "100"}]}
        source = _make_source_config(entities={1: "Entity A"})
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest, test_data)

        ingestor.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )

        catalog = get_catalog(tmp_workspace["warehouse"], tmp_workspace["catalog"])
        table = catalog.load_table("bronze.test_data")
        rows = read_with_duckdb(table)
        row = rows[0]
        assert row["ingested_at"] is not None
        assert row["source_url"] is not None
        assert row["source_method"] == "api"
        assert row["load_date"] is not None

    def test_ingest_uses_custom_entities(self, tmp_workspace):
        """Can pass custom entity list instead of source config default."""
        test_data = {99: [{"id": 99, "name": "custom", "value": "999"}]}
        source = _make_source_config()
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest, test_data)

        results = ingestor.ingest(
            entities={99: "Custom Entity"},
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )

        assert 99 in results
        assert results[99]["rows"] == 1

    def test_ingest_empty_entity_skips(self, tmp_workspace):
        """Entity with no data returns rows=0."""
        test_data = {1: []}
        source = _make_source_config(entities={1: "Entity A"})
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest, test_data)

        results = ingestor.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )

        assert results[1]["rows"] == 0


# --- Lineage attribution ---


@pytest.fixture
def gov_warehouse(tmp_path, monkeypatch):
    """Point the governance warehouse/catalog at tmp (matches
    test_lineage_roundtrip.py's convention) so lineage events land somewhere
    isolated and queryable."""
    monkeypatch.setattr(config, "GOVERNANCE_WAREHOUSE", tmp_path / "gov-wh")
    monkeypatch.setattr(config, "CATALOG_PATH", tmp_path / "catalog.db")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return tmp_path


class TestLineageAttribution:
    """Tests that ingest() threads spec_reference/agent_id into the lineage event."""

    def test_ingest_threads_spec_reference_and_agent_id(self, tmp_workspace, gov_warehouse):
        test_data = {1: [{"id": 1, "name": "alpha", "value": "100"}]}
        source = _make_source_config(entities={1: "Entity A"})
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest, test_data)

        ingestor.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
            spec_reference="docs/specs/raw-ingest-statements.md",
            agent_id="@document-extractor",
        )

        events = query_lineage_events("bronze.test_data", event_type="START")
        assert len(events) == 1
        assert events[0]["spec_reference"] == "docs/specs/raw-ingest-statements.md"
        assert events[0]["agent_id"] == "@document-extractor"

    def test_ingest_without_attribution_defaults_to_none(self, tmp_workspace, gov_warehouse):
        """Existing callers that don't pass spec_reference/agent_id keep the
        current NULL behavior."""
        test_data = {1: [{"id": 1, "name": "alpha", "value": "100"}]}
        source = _make_source_config(entities={1: "Entity A"})
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest, test_data)

        ingestor.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )

        events = query_lineage_events("bronze.test_data", event_type="START")
        assert len(events) == 1
        assert events[0]["spec_reference"] is None
        assert events[0]["agent_id"] is None


# --- Dedup ---


class TestDedup:
    """Tests for the dedup behavior."""

    def test_dedup_skips_existing_grains(self, tmp_workspace):
        """Second ingest with same data skips duplicates."""
        test_data = {1: [{"id": 1, "name": "alpha", "value": "100"}]}
        source = _make_source_config(entities={1: "Entity A"})
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest, test_data)

        # First ingest
        results1 = ingestor.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )
        assert results1[1]["rows"] == 1

        # Second ingest -- same data, should be skipped
        results2 = ingestor.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )
        assert results2[1]["rows"] == 0
        assert results2[1]["skipped"] == 1

    def test_dedup_allows_new_grains(self, tmp_workspace):
        """New data with different grain passes dedup."""
        source = _make_source_config(entities={1: "Entity A"})
        manifest = _make_manifest(source)

        # First ingest
        ingestor1 = SimpleIngestor(
            source, manifest,
            {1: [{"id": 1, "name": "alpha", "value": "100"}]},
        )
        ingestor1.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )

        # Second ingest with different grain
        ingestor2 = SimpleIngestor(
            source, manifest,
            {1: [{"id": 1, "name": "beta", "value": "200"}]},
        )
        results2 = ingestor2.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )
        assert results2[1]["rows"] == 1

        # Verify total is 2
        catalog = get_catalog(tmp_workspace["warehouse"], tmp_workspace["catalog"])
        table = catalog.load_table("bronze.test_data")
        rows = read_with_duckdb(table)
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# W3b — _build_existing_grains reads only the grain columns (docs/technical-
# audit-2026-07-02.md M1: "reads all columns of all rows when it needs only
# the grain fields").
# ---------------------------------------------------------------------------


class TestBuildExistingGrainsColumnPruning:
    def test_reads_only_grain_columns_not_the_whole_table(self, tmp_workspace, monkeypatch):
        """Behavioral spy: read_with_duckdb (called internally by
        _build_existing_grains) must be invoked with columns= set to exactly
        the dedup_grain fields — proving column pruning actually happens,
        not just that the resulting set is correct."""
        import brightsmith.bronze.base_ingestor as base_ingestor_mod

        source = _make_source_config(dedup_grain=["id", "name"])
        manifest = _make_manifest(source)
        ingestor = SimpleIngestor(source, manifest, {1: [{"id": 1, "name": "alpha", "value": "100"}]})

        # First ingest creates the table with one row so the second call has
        # something to read grains from.
        ingestor.ingest(
            warehouse_path=tmp_workspace["warehouse"],
            catalog_path=tmp_workspace["catalog"],
        )

        calls = []
        original = base_ingestor_mod.read_with_duckdb

        def spy(table, *args, **kwargs):
            calls.append(kwargs)
            return original(table, *args, **kwargs)

        monkeypatch.setattr(base_ingestor_mod, "read_with_duckdb", spy)

        catalog = get_catalog(tmp_workspace["warehouse"], tmp_workspace["catalog"])
        table = catalog.load_table("bronze.test_data")
        grains = ingestor._build_existing_grains(table)

        assert len(calls) == 1
        assert calls[0].get("columns") == ["id", "name"]
        assert grains == {("1", "alpha")}
