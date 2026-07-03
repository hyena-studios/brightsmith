"""Stub BaseIngestor for the wheel-install consumer-journey smoke test
(W2 — docs/technical-audit-2026-07-02.md).

Deliberately has no network dependency: ``fetch`` returns inline data so the
smoke test only exercises the framework's own code (BaseIngestor -> Iceberg
-> BaseMCPServer), not an external API. This module is loaded by
``brightsmith.run`` via the file-path branch of ``_import_step_module`` (the
manifest declares it as ``src/raw/stub_ingestor.py``, resolved against the
scaffolded project's root) — exactly the shape a real multi-source domain
pack uses (H5.2).
"""

from __future__ import annotations

from typing import Any

from pyiceberg.schema import Schema
from pyiceberg.types import DateType, NestedField, StringType, TimestamptzType

from brightsmith.bronze.base_ingestor import BaseIngestor

# Iceberg schema for bronze.stub_facts: the fields StubIngestor.flatten()
# produces, plus the framework metadata BaseIngestor.ingest() always adds
# (ingested_at, source_url, source_method, load_date).
STUB_SCHEMA = Schema(
    NestedField(field_id=1, name="entity_id", field_type=StringType(), required=False),
    NestedField(field_id=2, name="name", field_type=StringType(), required=False),
    NestedField(field_id=3, name="value", field_type=StringType(), required=False),
    NestedField(field_id=4, name="ingested_at", field_type=TimestamptzType(), required=False),
    NestedField(field_id=5, name="source_url", field_type=StringType(), required=False),
    NestedField(field_id=6, name="source_method", field_type=StringType(), required=False),
    NestedField(field_id=7, name="load_date", field_type=DateType(), required=False),
)

# Inline "raw" data keyed by entity id, matching domain/sources/stub_source.yaml's
# `entities:` block. No fetch/HTTP call involved.
_INLINE_DATA: dict[int, list[dict]] = {
    1: [{"entity_id": "1", "name": "Entity One", "value": "10"}],
    2: [{"entity_id": "2", "name": "Entity Two", "value": "20"}],
}


class StubIngestor(BaseIngestor):
    """Ingests a small inline dataset — no network, no filesystem fetch."""

    def fetch(self, entities: dict, method: str, **kwargs) -> dict[Any, Any]:
        return {eid: _INLINE_DATA.get(eid, []) for eid in entities}

    def flatten(self, raw_data: Any, entity_id: Any) -> list[dict]:
        return [dict(row) for row in raw_data]

    def get_schema(self) -> Schema:
        return STUB_SCHEMA


def main() -> dict:
    """Zone-transform entry point registered in domain/manifest.yaml.

    Loads the (scaffolded project's) manifest and source config, runs the
    generic BaseIngestor pipeline, and returns the ``rows_promoted``/
    ``rows_skipped`` dict ``run.py``'s ``_execute_zone_module`` expects.
    """
    from brightsmith.domain_loader import get_source, load_manifest

    manifest = load_manifest()
    source = get_source(manifest, "stub_source")
    ingestor = StubIngestor(source, manifest)
    results = ingestor.ingest()

    return {
        "rows_promoted": sum(r.get("rows", 0) for r in results.values()),
        "rows_skipped": sum(r.get("skipped", 0) for r in results.values()),
    }
