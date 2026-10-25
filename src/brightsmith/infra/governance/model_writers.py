"""Semantic model and policy writer functions for governance tables."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from brightsmith.infra.governance.queries import _write_records
from brightsmith.infra.governance.serializers import normalize_table_name, normalize_zone

logger = logging.getLogger(__name__)

__all__ = [
    "write_model_columns",
    "write_model_entity",
    "write_model_relationships",
    "write_policy",
]


def write_model_entity(
    entity_id: str,
    entity_group: str,
    table_name: str,
    zone: str,
    display_name: str,
    level: str,
) -> dict:
    """Write a model entity record (one entity per model level)."""
    record = {
        "entity_id": entity_id,
        "entity_group": entity_group,
        "table_name": normalize_table_name(table_name),
        "zone": normalize_zone(zone),
        "display_name": display_name,
        "level": level,
        "updated_at": datetime.now(UTC),
    }
    return _write_records("model_entities", [record])


def write_model_columns(
    entity_id: str,
    level: str,
    columns: list[dict],
) -> dict:
    """Write model column records for an entity.

    Each column dict should have: column_name, data_type, and optionally
    is_pk, is_fk, nullable, description, source_mapping, ordinal_position.
    """
    now = datetime.now(UTC)
    records = []
    for i, col in enumerate(columns):
        records.append({
            "entity_id": entity_id,
            "column_name": col.get("column_name", col.get("name", "")),
            "data_type": col.get("data_type", col.get("type", "")),
            "is_pk": col.get("is_pk", False),
            "is_fk": col.get("is_fk", False),
            "nullable": col.get("nullable"),
            "description": col.get("description"),
            "source_mapping": col.get("source_mapping"),
            "ordinal_position": col.get("ordinal_position", i),
            "level": level,
            "updated_at": now,
        })
    return _write_records("model_columns", records)


def write_model_relationships(
    entity_group: str,
    level: str,
    relationships: list[dict],
) -> dict:
    """Write model relationship records.

    Each relationship dict should have: relationship_id, source_entity, target_entity,
    and optionally source_column, target_column, source_cardinality, target_cardinality, label.
    """
    now = datetime.now(UTC)
    records = []
    for rel in relationships:
        records.append({
            "relationship_id": rel.get("relationship_id", ""),
            "entity_group": entity_group,
            "source_entity": rel.get("source_entity", ""),
            "target_entity": rel.get("target_entity", ""),
            "source_column": rel.get("source_column"),
            "target_column": rel.get("target_column"),
            "source_cardinality": rel.get("source_cardinality"),
            "target_cardinality": rel.get("target_cardinality"),
            "label": rel.get("label"),
            "level": level,
            "updated_at": now,
        })
    return _write_records("model_relationships", records)


def write_policy(
    policy_id: str,
    policy_name: str,
    policy_type: str,
    enforcement: str,
    *,
    target_table: str | None = None,
    target_zone: str | None = None,
    description: str | None = None,
    config: dict | None = None,
    created_by: str | None = None,
    created_at: datetime | None = None,
) -> dict:
    """Write a policy record."""
    now = datetime.now(UTC)
    record = {
        "policy_id": policy_id,
        "policy_name": policy_name,
        "policy_type": policy_type,
        "target_table": normalize_table_name(target_table),
        "target_zone": normalize_zone(target_zone),
        "description": description,
        "enforcement": enforcement,
        "config": json.dumps(config) if config else None,
        "created_by": created_by,
        "created_at": created_at or now,
        "updated_at": now,
    }
    return _write_records("policies", [record])
