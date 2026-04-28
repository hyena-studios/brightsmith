"""Reusable enterprise governance standards stored in Iceberg."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pyiceberg.schema import Schema
from pyiceberg.types import BooleanType, IntegerType, NestedField, StringType, TimestamptzType

from brightsmith.infra.grain import compute_grain_id


def _standard_schema(id_name: str, name_name: str = "name") -> Schema:
    return Schema(
        NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
        NestedField(field_id=2, name=id_name, field_type=StringType(), required=True),
        NestedField(field_id=3, name=name_name, field_type=StringType(), required=True),
        NestedField(field_id=4, name="description", field_type=StringType(), required=False),
        NestedField(field_id=5, name="version", field_type=IntegerType(), required=True),
        NestedField(field_id=6, name="status", field_type=StringType(), required=True),
        NestedField(field_id=7, name="metadata", field_type=StringType(), required=False),
        NestedField(field_id=8, name="updated_at", field_type=TimestamptzType(), required=True),
    )


BUSINESS_TERMS_SCHEMA = _standard_schema("term_id", "term")
CDE_CRITERIA_SCHEMA = _standard_schema("criteria_id", "criteria_name")
CRITICALITY_CLASSIFICATIONS_SCHEMA = _standard_schema("classification_id", "classification_name")
PII_CLASSIFICATIONS_SCHEMA = _standard_schema("classification_id", "classification_name")
POLICIES_SCHEMA = _standard_schema("policy_id", "policy_name")
DQ_RULE_TEMPLATES_SCHEMA = _standard_schema("template_id", "template_name")
ALLOWED_VALUES_SCHEMA = _standard_schema("value_set_id", "value_set_name")
DATA_DOMAINS_SCHEMA = _standard_schema("domain_id", "domain_name")
STEWARDS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="steward_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="steward_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="email", field_type=StringType(), required=False),
    NestedField(field_id=5, name="group_name", field_type=StringType(), required=False),
    NestedField(field_id=6, name="active", field_type=BooleanType(), required=True),
    NestedField(field_id=7, name="metadata", field_type=StringType(), required=False),
    NestedField(field_id=8, name="updated_at", field_type=TimestamptzType(), required=True),
)

ENTERPRISE_TABLE_CONFIGS: dict[str, tuple[Schema, list[str]]] = {
    "business_terms": (BUSINESS_TERMS_SCHEMA, ["term_id", "version"]),
    "cde_criteria": (CDE_CRITERIA_SCHEMA, ["criteria_id", "version"]),
    "criticality_classifications": (CRITICALITY_CLASSIFICATIONS_SCHEMA, ["classification_id", "version"]),
    "pii_classifications": (PII_CLASSIFICATIONS_SCHEMA, ["classification_id", "version"]),
    "policies": (POLICIES_SCHEMA, ["policy_id", "version"]),
    "dq_rule_templates": (DQ_RULE_TEMPLATES_SCHEMA, ["template_id", "version"]),
    "allowed_values": (ALLOWED_VALUES_SCHEMA, ["value_set_id", "version"]),
    "data_domains": (DATA_DOMAINS_SCHEMA, ["domain_id", "version"]),
    "stewards": (STEWARDS_SCHEMA, ["steward_id"]),
}

_ID_FIELDS = {
    "business_terms": "term_id",
    "cde_criteria": "criteria_id",
    "criticality_classifications": "classification_id",
    "pii_classifications": "classification_id",
    "policies": "policy_id",
    "dq_rule_templates": "template_id",
    "allowed_values": "value_set_id",
    "data_domains": "domain_id",
    "stewards": "steward_id",
}


def _get_enterprise_table(table_name: str):
    from brightsmith.config import CATALOG_PATH, GOVERNANCE_WAREHOUSE
    from brightsmith.infra.iceberg_setup import get_catalog, get_or_create_table

    if table_name not in ENTERPRISE_TABLE_CONFIGS:
        raise ValueError(f"Unknown enterprise governance table: {table_name}")
    schema, _ = ENTERPRISE_TABLE_CONFIGS[table_name]
    catalog = get_catalog(GOVERNANCE_WAREHOUSE, CATALOG_PATH)
    return get_or_create_table(catalog, "governance_enterprise", table_name, schema)


def _write_enterprise_records(table_name: str, records: list[dict]) -> dict:
    from brightsmith.infra.promote import promote

    if not records:
        return {"promoted": 0, "skipped": 0, "snapshot_id": None}
    _, grain_fields = ENTERPRISE_TABLE_CONFIGS[table_name]
    for record in records:
        grain_row = {}
        for field in grain_fields:
            value = record.get(field, "")
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            grain_row[field] = value
        record["record_id"] = compute_grain_id(grain_row, grain_fields, prefix=table_name.upper()[:4])
    return promote(_get_enterprise_table(table_name), records)


def write_enterprise_standard(
    table_name: str,
    record_id_value: str,
    name: str,
    *,
    description: str | None = None,
    version: int = 1,
    status: str = "active",
    metadata: dict | None = None,
) -> dict:
    """Write a reusable enterprise governance standard."""
    id_field = _ID_FIELDS[table_name]
    name_field = {
        "business_terms": "term",
        "cde_criteria": "criteria_name",
        "criticality_classifications": "classification_name",
        "pii_classifications": "classification_name",
        "policies": "policy_name",
        "dq_rule_templates": "template_name",
        "allowed_values": "value_set_name",
        "data_domains": "domain_name",
        "stewards": "steward_name",
    }[table_name]
    record = {
        id_field: record_id_value,
        name_field: name,
        "description": description,
        "metadata": json.dumps(metadata or {}, sort_keys=True),
        "updated_at": datetime.now(timezone.utc),
    }
    if table_name == "stewards":
        record["active"] = status == "active"
        record["email"] = (metadata or {}).get("email")
        record["group_name"] = (metadata or {}).get("group_name")
    else:
        record["version"] = version
        record["status"] = status
    return _write_enterprise_records(table_name, [record])


def write_business_term(term_id: str, term: str, **kwargs) -> dict:
    """Write an enterprise business term."""
    return write_enterprise_standard("business_terms", term_id, term, **kwargs)


def enterprise_record_exists(table_name: str, id_field: str, value: str) -> bool:
    """Return whether an enterprise reference exists."""
    import duckdb

    table = _get_enterprise_table(table_name)
    arrow_table = table.scan().to_arrow()
    if arrow_table.num_rows == 0:
        return False
    con = duckdb.connect()
    rel = con.sql(f"SELECT 1 FROM arrow_table WHERE {id_field} = $1 LIMIT 1", params=[value])
    return rel.fetchone() is not None


def get_enterprise_records(table_name: str) -> list[dict]:
    """Read enterprise records from a table."""
    table = _get_enterprise_table(table_name)
    arrow_table = table.scan().to_arrow()
    if arrow_table.num_rows == 0:
        return []
    return arrow_table.to_pylist()

