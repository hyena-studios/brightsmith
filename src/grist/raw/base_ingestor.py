"""Abstract base class for raw zone ingestion.

Domain packs extend BaseIngestor and implement fetch() and flatten().
The framework handles everything else: Iceberg table management,
dedup, metadata enrichment, and summary reporting.

Usage:
    class MyIngestor(BaseIngestor):
        def fetch(self, entities, method, **kwargs):
            return {eid: get_data(eid) for eid in entities}

        def flatten(self, raw_data, entity_id):
            return [{"col": val} for val in raw_data]

    manifest = load_manifest()
    source = get_source(manifest, "my_source")
    ingestor = MyIngestor(source, manifest)
    results = ingestor.ingest()
"""

from __future__ import annotations

import datetime
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError
from pyiceberg.schema import Schema

from grist.infra.iceberg_setup import append_data, get_catalog, read_with_duckdb
from grist.domain_loader import DomainManifest, SourceConfig

logger = logging.getLogger(__name__)


class BaseIngestor(ABC):
    """Framework base class for raw zone ingestion.

    Domain packs extend this and implement fetch() and flatten().
    The framework handles: Iceberg table creation, dedup, metadata
    enrichment, and append.
    """

    def __init__(self, source_config: SourceConfig, manifest: DomainManifest):
        self.source = source_config
        self.manifest = manifest

    @abstractmethod
    def fetch(self, entities: dict, method: str, **kwargs) -> dict[Any, Any]:
        """Fetch raw data from the source.

        Args:
            entities: {entity_id: label} dict from source config.
            method: fetch method name (must exist in source_config.fetch).
            **kwargs: additional arguments (cache_dir, user_agent, etc.)

        Returns:
            {entity_id: raw_data} -- raw data per entity, any format.
            The framework passes each value to flatten().
        """

    @abstractmethod
    def flatten(self, raw_data: Any, entity_id: Any) -> list[dict]:
        """Flatten raw data into tabular records.

        Args:
            raw_data: whatever fetch() returned for one entity.
            entity_id: the entity identifier.

        Returns:
            List of flat dicts ready for Iceberg append.
            The framework adds ingested_at, source_url, source_method, load_date.
        """

    @abstractmethod
    def get_schema(self) -> Schema:
        """Return the Iceberg schema for the raw table.

        Each domain source defines its own schema since column
        names and types are domain-specific.
        """

    def get_source_url(self, entity_id: Any, method: str) -> str:
        """Return the source URL for lineage/audit purposes.

        Override this for custom URL formatting. Default uses the
        url_template from the fetch config.
        """
        fetch_config = self.source.fetch.get(method, {})
        url_template = fetch_config.get("url_template", "")
        if url_template:
            return url_template
        return fetch_config.get("url", f"{self.manifest.name}:{self.source.name}")

    def _get_or_create_table(self, warehouse_path: Path, catalog_path: Path):
        """Get or create the Iceberg table for this source."""
        catalog = get_catalog(warehouse_path, catalog_path)

        try:
            catalog.create_namespace(self.source.namespace)
        except NamespaceAlreadyExistsError:
            pass

        identifier = self.source.full_table_name
        try:
            return catalog.create_table(identifier, schema=self.get_schema())
        except TableAlreadyExistsError:
            return catalog.load_table(identifier)

    def _build_existing_grains(self, table) -> set:
        """Build the set of existing dedup grains from the table."""
        grain_fields = self.source.dedup_grain
        if not grain_fields:
            return set()

        try:
            existing = read_with_duckdb(table)
            return {
                tuple(str(r.get(f, "")) for f in grain_fields)
                for r in existing
            }
        except Exception:
            return set()

    def _make_grain(self, row: dict) -> tuple:
        """Extract the dedup grain tuple from a row."""
        return tuple(str(row.get(f, "")) for f in self.source.dedup_grain)

    def ingest(
        self,
        entities: dict | None = None,
        method: str = "api",
        warehouse_path: Path | None = None,
        catalog_path: Path | None = None,
        **kwargs,
    ) -> dict:
        """Generic ingest pipeline: fetch -> flatten -> dedup -> write.

        This method is NOT abstract -- it's the framework's implementation.
        Domain packs do NOT override this.

        Args:
            entities: {entity_id: label} dict. Defaults to source_config.entities.
            method: fetch method name. Defaults to "api".
            warehouse_path: Iceberg warehouse path override.
            catalog_path: Iceberg catalog path override.
            **kwargs: passed through to fetch() (e.g., cache_dir, user_agent).

        Returns:
            {entity_id: {"rows": N, "snapshot_id": X, "skipped": Y}} summary.
        """
        from grist.config import WAREHOUSE_PATH as DEFAULT_WH, CATALOG_PATH as DEFAULT_CAT

        entities = entities or self.source.entities
        warehouse_path = warehouse_path or DEFAULT_WH
        catalog_path = catalog_path or DEFAULT_CAT

        table = self._get_or_create_table(warehouse_path, catalog_path)

        # Build existing grain set for dedup
        existing_grains = self._build_existing_grains(table)

        # Fetch raw data (domain-specific)
        raw_data = self.fetch(entities, method, **kwargs)

        # Flatten and write one snapshot per entity
        results: dict = {}
        for entity_id in entities:
            data = raw_data[entity_id]
            flat_rows = self.flatten(data, entity_id)

            # Add framework metadata
            ingested_at = datetime.datetime.now(tz=datetime.timezone.utc)
            source_url = self.get_source_url(entity_id, method)
            load_date = ingested_at.date()
            for row in flat_rows:
                row["ingested_at"] = ingested_at
                row["source_url"] = source_url
                row["source_method"] = method
                row["load_date"] = load_date

            # Dedup against existing grains
            original_count = len(flat_rows)
            if existing_grains and self.source.dedup_grain:
                flat_rows = [
                    r for r in flat_rows
                    if self._make_grain(r) not in existing_grains
                ]
            skipped = original_count - len(flat_rows)

            if not flat_rows:
                results[entity_id] = {"rows": 0, "skipped": skipped}
                continue

            if skipped:
                logger.info(
                    "Entity %s: %d existing records skipped, %d new records",
                    entity_id, skipped, len(flat_rows),
                )

            snapshot_id = append_data(table, flat_rows)
            results[entity_id] = {
                "rows": len(flat_rows),
                "snapshot_id": snapshot_id,
                "skipped": skipped,
            }

        total_skipped = sum(r.get("skipped", 0) for r in results.values())
        total_new = sum(r.get("rows", 0) for r in results.values())
        if total_skipped:
            print(f"Dedup summary: {total_new} new facts ingested, {total_skipped} duplicates skipped")

        return results
