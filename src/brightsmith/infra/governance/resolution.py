"""Enterprise reference validation helpers."""

from __future__ import annotations

import json

from brightsmith.infra.governance.enterprise import enterprise_record_exists

REFERENCE_TABLES = {
    "business_term_id": ("business_terms", "term_id"),
    "cde_criteria_ids": ("cde_criteria", "criteria_id"),
    "criticality_classification_id": ("criticality_classifications", "classification_id"),
    "policy_ids": ("policies", "policy_id"),
    "pii_classification_id": ("pii_classifications", "classification_id"),
    "dq_template_id": ("dq_rule_templates", "template_id"),
    "allowed_value_set_id": ("allowed_values", "value_set_id"),
}


def validate_enterprise_reference(table_name: str, id_field: str, value: str) -> None:
    """Raise if an enterprise record reference does not exist."""
    if not value:
        return
    if not enterprise_record_exists(table_name, id_field, value):
        raise ValueError(f"Unknown enterprise governance reference {table_name}.{id_field}={value}")


def validate_contract_column_references(column: dict) -> None:
    """Validate enterprise IDs used by a product contract column."""
    for field in ("business_term_id", "criticality_classification_id", "pii_classification_id"):
        value = column.get(field)
        if value:
            table_name, id_field = REFERENCE_TABLES[field]
            validate_enterprise_reference(table_name, id_field, value)

    for field in ("cde_criteria_ids", "policy_ids"):
        values = column.get(field) or []
        if isinstance(values, str):
            try:
                parsed = json.loads(values)
                values = parsed if isinstance(parsed, list) else [values]
            except json.JSONDecodeError:
                values = [v for v in values.split(",") if v]
        table_name, id_field = REFERENCE_TABLES[field]
        for value in values:
            validate_enterprise_reference(table_name, id_field, value)
