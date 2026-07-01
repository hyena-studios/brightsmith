"""Iceberg schemas and table configuration for product governance tables."""

from __future__ import annotations

from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    FloatType,
    IntegerType,
    NestedField,
    StringType,
    TimestamptzType,
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SPEC_REGISTRY_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="zone", field_type=StringType(), required=True),
    NestedField(field_id=4, name="status", field_type=StringType(), required=True),
    NestedField(field_id=5, name="output_tables", field_type=StringType(), required=True),
    NestedField(field_id=6, name="dq_score_pct", field_type=FloatType(), required=False),
    NestedField(field_id=7, name="dq_rules_total", field_type=IntegerType(), required=False),
    NestedField(field_id=8, name="dq_rules_passing", field_type=IntegerType(), required=False),
    NestedField(field_id=9, name="dq_rules_failing", field_type=IntegerType(), required=False),
    NestedField(field_id=10, name="dq_p0_passed", field_type=BooleanType(), required=False),
    NestedField(field_id=11, name="has_contract", field_type=BooleanType(), required=False),
    NestedField(field_id=12, name="has_lineage", field_type=BooleanType(), required=False),
    NestedField(field_id=13, name="has_golden_dataset", field_type=BooleanType(), required=False),
    NestedField(field_id=14, name="has_data_dictionary", field_type=BooleanType(), required=False),
    NestedField(field_id=15, name="has_cde_tags", field_type=BooleanType(), required=False),
    NestedField(field_id=16, name="pipeline_step_current", field_type=StringType(), required=False),
    NestedField(field_id=17, name="pipeline_steps_total", field_type=IntegerType(), required=False),
    NestedField(field_id=18, name="pipeline_steps_completed", field_type=IntegerType(), required=False),
    NestedField(field_id=19, name="spec_file_path", field_type=StringType(), required=False),
    NestedField(field_id=20, name="updated_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=21, name="updated_by", field_type=StringType(), required=True),
)

DQ_RUNS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="run_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="executed_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=6, name="rules_total", field_type=IntegerType(), required=True),
    NestedField(field_id=7, name="rules_passed", field_type=IntegerType(), required=True),
    NestedField(field_id=8, name="rules_failed", field_type=IntegerType(), required=True),
    NestedField(field_id=9, name="rules_errored", field_type=IntegerType(), required=True),
    NestedField(field_id=10, name="rules_warning", field_type=IntegerType(), required=True),
    NestedField(field_id=11, name="score_pct", field_type=FloatType(), required=True),
    NestedField(field_id=12, name="p0_passed", field_type=BooleanType(), required=True),
    NestedField(field_id=13, name="p0_total", field_type=IntegerType(), required=False),
    NestedField(field_id=14, name="p0_failed", field_type=IntegerType(), required=False),
    NestedField(field_id=15, name="p1_total", field_type=IntegerType(), required=False),
    NestedField(field_id=16, name="p1_failed", field_type=IntegerType(), required=False),
    NestedField(field_id=17, name="duration_ms", field_type=IntegerType(), required=False),
    NestedField(field_id=18, name="result_file_path", field_type=StringType(), required=False),
    NestedField(field_id=19, name="updated_at", field_type=TimestamptzType(), required=True),
)

DQ_RULE_RESULTS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="run_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="rule_id", field_type=StringType(), required=True),
    NestedField(field_id=5, name="category", field_type=StringType(), required=True),
    NestedField(field_id=6, name="priority", field_type=StringType(), required=True),
    NestedField(field_id=7, name="description", field_type=StringType(), required=True),
    NestedField(field_id=8, name="passed", field_type=BooleanType(), required=True),
    NestedField(field_id=9, name="raw_value", field_type=StringType(), required=False),
    NestedField(field_id=10, name="threshold", field_type=StringType(), required=False),
    NestedField(field_id=11, name="violations", field_type=IntegerType(), required=False),
    NestedField(field_id=12, name="execution_time_ms", field_type=IntegerType(), required=False),
    NestedField(field_id=13, name="error_message", field_type=StringType(), required=False),
    NestedField(field_id=14, name="executed_at", field_type=TimestamptzType(), required=True),
)

PIPELINE_EVENTS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="step_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="event_type", field_type=StringType(), required=True),
    NestedField(field_id=5, name="agent_id", field_type=StringType(), required=False),
    NestedField(field_id=6, name="output_path", field_type=StringType(), required=False),
    NestedField(field_id=7, name="skip_reason", field_type=StringType(), required=False),
    NestedField(field_id=8, name="approval_decision", field_type=StringType(), required=False),
    NestedField(field_id=9, name="approval_by", field_type=StringType(), required=False),
    NestedField(field_id=10, name="notes", field_type=StringType(), required=False),
    NestedField(field_id=11, name="event_time", field_type=TimestamptzType(), required=True),
    NestedField(field_id=12, name="content", field_type=StringType(), required=False),
)

CONTRACT_METADATA_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="contract_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="spec_name", field_type=StringType(), required=False),
    NestedField(field_id=4, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="zone", field_type=StringType(), required=True),
    NestedField(field_id=6, name="version", field_type=StringType(), required=True),
    NestedField(field_id=7, name="status", field_type=StringType(), required=True),
    NestedField(field_id=8, name="column_count", field_type=IntegerType(), required=False),
    NestedField(field_id=9, name="grain_columns", field_type=StringType(), required=False),
    NestedField(field_id=10, name="has_dq_rules", field_type=BooleanType(), required=False),
    NestedField(field_id=11, name="has_golden_dataset", field_type=BooleanType(), required=False),
    NestedField(field_id=12, name="freshness_sla_hours", field_type=IntegerType(), required=False),
    NestedField(field_id=13, name="contract_file_path", field_type=StringType(), required=True),
    NestedField(field_id=14, name="updated_at", field_type=TimestamptzType(), required=True),
)

CONTRACT_COLUMNS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="contract_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="zone", field_type=StringType(), required=True),
    NestedField(field_id=5, name="column_name", field_type=StringType(), required=True),
    NestedField(field_id=6, name="ordinal_position", field_type=IntegerType(), required=True),
    NestedField(field_id=7, name="data_type", field_type=StringType(), required=False),
    NestedField(field_id=8, name="is_nullable", field_type=BooleanType(), required=False),
    NestedField(field_id=9, name="is_cde", field_type=BooleanType(), required=False),
    NestedField(field_id=10, name="cde_rationale", field_type=StringType(), required=False),
    NestedField(field_id=11, name="is_pii", field_type=BooleanType(), required=False),
    NestedField(field_id=12, name="pii_rationale", field_type=StringType(), required=False),
    NestedField(field_id=13, name="business_term_id", field_type=StringType(), required=False),
    NestedField(field_id=14, name="cde_criteria_ids", field_type=StringType(), required=False),
    NestedField(field_id=15, name="criticality_classification_id", field_type=StringType(), required=False),
    NestedField(field_id=16, name="policy_ids", field_type=StringType(), required=False),
    NestedField(field_id=17, name="pii_classification_id", field_type=StringType(), required=False),
    NestedField(field_id=18, name="description", field_type=StringType(), required=False),
    NestedField(field_id=19, name="version", field_type=StringType(), required=True),
    NestedField(field_id=20, name="updated_at", field_type=TimestamptzType(), required=True),
)

GLOSSARY_TERMS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="term_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="term", field_type=StringType(), required=True),
    NestedField(field_id=4, name="definition", field_type=StringType(), required=True),
    NestedField(field_id=5, name="category", field_type=StringType(), required=True),
    NestedField(field_id=6, name="source", field_type=StringType(), required=True),
    NestedField(field_id=7, name="approval_status", field_type=StringType(), required=True),
    NestedField(field_id=8, name="used_in_specs", field_type=StringType(), required=False),
    NestedField(field_id=9, name="updated_at", field_type=TimestamptzType(), required=True),
)

AGENT_ACTIVITY_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="agent_id", field_type=StringType(), required=True),
    NestedField(field_id=4, name="pipeline_step", field_type=StringType(), required=False),
    NestedField(field_id=5, name="activity_type", field_type=StringType(), required=True),
    NestedField(field_id=6, name="severity", field_type=StringType(), required=True),
    NestedField(field_id=7, name="summary", field_type=StringType(), required=True),
    NestedField(field_id=8, name="detail", field_type=StringType(), required=False),
    NestedField(field_id=9, name="references", field_type=StringType(), required=False),
    NestedField(field_id=10, name="related_table", field_type=StringType(), required=False),
    NestedField(field_id=11, name="related_rule_id", field_type=StringType(), required=False),
    NestedField(field_id=12, name="resolution_status", field_type=StringType(), required=False),
    NestedField(field_id=13, name="resolved_by", field_type=StringType(), required=False),
    NestedField(field_id=14, name="resolved_at", field_type=TimestamptzType(), required=False),
    NestedField(field_id=15, name="event_time", field_type=TimestamptzType(), required=True),
)

DQ_RULES_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="rule_id", field_type=StringType(), required=True),
    NestedField(field_id=5, name="category", field_type=StringType(), required=True),
    NestedField(field_id=6, name="priority", field_type=StringType(), required=True),
    NestedField(field_id=7, name="description", field_type=StringType(), required=True),
    NestedField(field_id=8, name="sql", field_type=StringType(), required=True),
    NestedField(field_id=9, name="threshold", field_type=StringType(), required=True),
    NestedField(field_id=10, name="status", field_type=StringType(), required=True),
    NestedField(field_id=11, name="version", field_type=IntegerType(), required=True),
    NestedField(field_id=12, name="approved_by", field_type=StringType(), required=False),
    NestedField(field_id=13, name="approved_at", field_type=TimestamptzType(), required=False),
    NestedField(field_id=14, name="updated_at", field_type=TimestamptzType(), required=True),
)

DQ_ACKNOWLEDGMENTS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="run_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="rule_id", field_type=StringType(), required=True),
    NestedField(field_id=4, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="acknowledged_by", field_type=StringType(), required=True),
    NestedField(field_id=6, name="reason", field_type=StringType(), required=True),
    NestedField(field_id=7, name="acknowledged_at", field_type=TimestamptzType(), required=True),
)

CAB_DECISIONS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="decision_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="classification", field_type=StringType(), required=True),
    NestedField(field_id=6, name="classification_reasons", field_type=StringType(), required=True),
    NestedField(field_id=7, name="contract_version_before", field_type=StringType(), required=False),
    NestedField(field_id=8, name="contract_version_after", field_type=StringType(), required=False),
    NestedField(field_id=9, name="schema_diff", field_type=StringType(), required=False),
    NestedField(field_id=10, name="blast_radius", field_type=StringType(), required=False),
    NestedField(field_id=11, name="decision", field_type=StringType(), required=True),
    NestedField(field_id=12, name="decided_by", field_type=StringType(), required=False),
    NestedField(field_id=13, name="decided_at", field_type=TimestamptzType(), required=False),
    NestedField(field_id=14, name="notes", field_type=StringType(), required=False),
    NestedField(field_id=15, name="rationale", field_type=StringType(), required=False),
    NestedField(field_id=16, name="fork_config", field_type=StringType(), required=False),
    NestedField(field_id=17, name="human_override", field_type=StringType(), required=False),
    NestedField(field_id=18, name="created_at", field_type=TimestamptzType(), required=True),
)

GOLDEN_DATASETS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="spec_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="value_description", field_type=StringType(), required=True),
    NestedField(field_id=5, name="column_name", field_type=StringType(), required=True),
    NestedField(field_id=6, name="expected_value", field_type=StringType(), required=True),
    NestedField(field_id=7, name="tolerance_pct", field_type=FloatType(), required=False),
    NestedField(field_id=8, name="tolerance_type", field_type=StringType(), required=False),
    NestedField(field_id=9, name="filters", field_type=StringType(), required=True),
    NestedField(field_id=10, name="last_verified_at", field_type=TimestamptzType(), required=False),
    NestedField(field_id=11, name="last_verified_passed", field_type=BooleanType(), required=False),
    NestedField(field_id=12, name="updated_at", field_type=TimestamptzType(), required=True),
)

RUN_HISTORY_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="run_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="started_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=4, name="completed_at", field_type=TimestamptzType(), required=False),
    NestedField(field_id=5, name="duration_seconds", field_type=FloatType(), required=False),
    NestedField(field_id=6, name="status", field_type=StringType(), required=True),
    NestedField(field_id=7, name="zones_summary", field_type=StringType(), required=True),
    NestedField(field_id=8, name="golden_datasets_summary", field_type=StringType(), required=False),
    NestedField(field_id=9, name="options", field_type=StringType(), required=False),
    NestedField(field_id=10, name="error_message", field_type=StringType(), required=False),
    NestedField(field_id=11, name="updated_at", field_type=TimestamptzType(), required=True),
)

CHAOS_MANIFESTS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="run_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="source_table", field_type=StringType(), required=True),
    NestedField(field_id=4, name="shadow_table", field_type=StringType(), required=True),
    NestedField(field_id=5, name="total_rows", field_type=IntegerType(), required=True),
    NestedField(field_id=6, name="corruption_rate", field_type=FloatType(), required=True),
    NestedField(field_id=7, name="seed", field_type=IntegerType(), required=False),
    NestedField(field_id=8, name="rows_corrupted", field_type=IntegerType(), required=True),
    NestedField(field_id=9, name="columns_corrupted", field_type=IntegerType(), required=True),
    NestedField(field_id=10, name="total_corruptions", field_type=IntegerType(), required=True),
    NestedField(field_id=11, name="dimensions_covered", field_type=StringType(), required=False),
    NestedField(field_id=12, name="corruptions_sample", field_type=StringType(), required=False),
    NestedField(field_id=13, name="created_at", field_type=TimestamptzType(), required=True),
)

DOCUMENTS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="doc_type", field_type=StringType(), required=True),
    NestedField(field_id=3, name="doc_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="spec_name", field_type=StringType(), required=False),
    NestedField(field_id=5, name="agent_id", field_type=StringType(), required=False),
    NestedField(field_id=6, name="title", field_type=StringType(), required=True),
    NestedField(field_id=7, name="content", field_type=StringType(), required=True),
    NestedField(field_id=8, name="version", field_type=IntegerType(), required=True),
    NestedField(field_id=9, name="metadata", field_type=StringType(), required=False),
    NestedField(field_id=10, name="created_at", field_type=TimestamptzType(), required=True),
)

DATA_DICTIONARY_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="zone", field_type=StringType(), required=True),
    NestedField(field_id=4, name="column_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="data_type", field_type=StringType(), required=False),
    NestedField(field_id=6, name="definition", field_type=StringType(), required=False),
    NestedField(field_id=7, name="nullable", field_type=BooleanType(), required=False),
    NestedField(field_id=8, name="is_grain", field_type=BooleanType(), required=False),
    NestedField(field_id=9, name="ordinal_position", field_type=IntegerType(), required=False),
    NestedField(field_id=10, name="updated_at", field_type=TimestamptzType(), required=True),
)

MODEL_ENTITIES_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="entity_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="entity_group", field_type=StringType(), required=True),
    NestedField(field_id=4, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=5, name="zone", field_type=StringType(), required=True),
    NestedField(field_id=6, name="display_name", field_type=StringType(), required=True),
    NestedField(field_id=7, name="level", field_type=StringType(), required=True),
    NestedField(field_id=8, name="updated_at", field_type=TimestamptzType(), required=True),
)

MODEL_COLUMNS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="entity_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="column_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="data_type", field_type=StringType(), required=True),
    NestedField(field_id=5, name="is_pk", field_type=BooleanType(), required=False),
    NestedField(field_id=6, name="is_fk", field_type=BooleanType(), required=False),
    NestedField(field_id=7, name="nullable", field_type=BooleanType(), required=False),
    NestedField(field_id=8, name="description", field_type=StringType(), required=False),
    NestedField(field_id=9, name="source_mapping", field_type=StringType(), required=False),
    NestedField(field_id=10, name="ordinal_position", field_type=IntegerType(), required=False),
    NestedField(field_id=11, name="level", field_type=StringType(), required=True),
    NestedField(field_id=12, name="updated_at", field_type=TimestamptzType(), required=True),
)

MODEL_RELATIONSHIPS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="relationship_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="entity_group", field_type=StringType(), required=True),
    NestedField(field_id=4, name="source_entity", field_type=StringType(), required=True),
    NestedField(field_id=5, name="target_entity", field_type=StringType(), required=True),
    NestedField(field_id=6, name="source_column", field_type=StringType(), required=False),
    NestedField(field_id=7, name="target_column", field_type=StringType(), required=False),
    NestedField(field_id=8, name="source_cardinality", field_type=StringType(), required=False),
    NestedField(field_id=9, name="target_cardinality", field_type=StringType(), required=False),
    NestedField(field_id=10, name="label", field_type=StringType(), required=False),
    NestedField(field_id=11, name="level", field_type=StringType(), required=True),
    NestedField(field_id=12, name="updated_at", field_type=TimestamptzType(), required=True),
)

POLICIES_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="policy_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="policy_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="policy_type", field_type=StringType(), required=True),
    NestedField(field_id=5, name="target_table", field_type=StringType(), required=False),
    NestedField(field_id=6, name="target_zone", field_type=StringType(), required=False),
    NestedField(field_id=7, name="description", field_type=StringType(), required=False),
    NestedField(field_id=8, name="enforcement", field_type=StringType(), required=True),
    NestedField(field_id=9, name="config", field_type=StringType(), required=False),
    NestedField(field_id=10, name="created_by", field_type=StringType(), required=False),
    NestedField(field_id=11, name="created_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=12, name="updated_at", field_type=TimestamptzType(), required=True),
)

# Table name -> (schema, grain_fields) mapping
_TABLE_CONFIGS: dict[str, tuple[Schema, list[str]]] = {
    "spec_registry": (SPEC_REGISTRY_SCHEMA, ["spec_name", "status", "updated_at"]),
    "dq_runs": (DQ_RUNS_SCHEMA, ["run_id"]),
    "dq_rule_results": (DQ_RULE_RESULTS_SCHEMA, ["run_id", "rule_id"]),
    "pipeline_events": (PIPELINE_EVENTS_SCHEMA, ["spec_name", "step_name", "event_type", "event_time"]),
    "contract_metadata": (CONTRACT_METADATA_SCHEMA, ["contract_name", "version"]),
    "contract_columns": (CONTRACT_COLUMNS_SCHEMA, ["contract_name", "column_name", "version"]),
    "glossary_terms": (GLOSSARY_TERMS_SCHEMA, ["term_id", "updated_at"]),
    "agent_activity": (AGENT_ACTIVITY_SCHEMA, ["spec_name", "agent_id", "activity_type", "summary", "event_time"]),
    "dq_rules": (DQ_RULES_SCHEMA, ["spec_name", "rule_id", "version"]),
    "dq_acknowledgments": (DQ_ACKNOWLEDGMENTS_SCHEMA, ["run_id", "rule_id"]),
    "cab_decisions": (CAB_DECISIONS_SCHEMA, ["decision_id"]),
    "golden_datasets": (GOLDEN_DATASETS_SCHEMA, ["spec_name", "column_name", "filters"]),
    "run_history": (RUN_HISTORY_SCHEMA, ["run_id"]),
    "chaos_manifests": (CHAOS_MANIFESTS_SCHEMA, ["run_id"]),
    "documents": (DOCUMENTS_SCHEMA, ["doc_type", "doc_name", "version"]),
    "data_dictionary": (DATA_DICTIONARY_SCHEMA, ["table_name", "column_name"]),
    "model_entities": (MODEL_ENTITIES_SCHEMA, ["entity_id", "level"]),
    "model_columns": (MODEL_COLUMNS_SCHEMA, ["entity_id", "column_name", "level"]),
    "model_relationships": (MODEL_RELATIONSHIPS_SCHEMA, ["relationship_id", "level"]),
    "policies": (POLICIES_SCHEMA, ["policy_id"]),
}

# Override grain ID prefixes for tables where the default (table_name.upper()[:4]) is wrong.
_GRAIN_PREFIXES: dict[str, str] = {
    "data_dictionary": "DICT",
    "model_entities": "MENT",
    "model_columns": "MCOL",
    "model_relationships": "MREL",
    "policies": "POLI",
}
