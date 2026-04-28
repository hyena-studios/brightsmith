"""Product/runtime governance state stored in Iceberg.

Iceberg is the runtime source of truth for generated governance state.

Tables:
    governance.spec_registry      — Hub: spec -> tables, DQ scores, completeness
    governance.dq_runs            — DQ execution runs (aggregate)
    governance.dq_rule_results    — Individual rule outcomes per run
    governance.dq_rules           — DQ rule definitions (versioned)
    governance.dq_acknowledgments — Acknowledged DQ failures (append-only)
    governance.pipeline_events    — Pipeline step execution log (+ approval content)
    governance.contract_metadata  — Synced from YAML contracts
    governance.contract_columns   — Per-column governance data from contracts
    governance.glossary_terms     — Synced from business-glossary.json
    governance.agent_activity     — Structured agent findings/decisions
    governance.cab_decisions      — CAB schema change decisions
    governance.golden_datasets    — Golden dataset verification values
    governance.run_history        — Pipeline run history
    governance.chaos_manifests    — Chaos monkey run manifests
    governance.documents          — Prose governance artifacts (reviews, models, etc.)
    governance.data_dictionary    — Per-column data dictionary (zone, type, definition)
    governance.model_entities     — Semantic model entities per level (conceptual/logical/physical)
    governance.model_columns      — Per-column detail for model entities
    governance.model_relationships — Entity relationships per level
    governance.policies           — Data governance policies

Usage:
    from brightsmith.infra.governance_db import (
        write_spec_registry, write_dq_run, write_dq_rule_results,
        write_pipeline_event, sync_contract, sync_glossary_term,
        write_agent_activity, log_agent_finding,
        write_data_dictionary, write_model_entity, write_model_columns,
        write_model_relationships, write_policy,
        get_current_specs, get_governance_summary, get_contract_columns,
        get_data_dictionary, get_model_entities, get_model_columns,
        get_model_relationships, get_policies,
    )

CLI:
    python -m brightsmith.infra.governance_db status
    python -m brightsmith.infra.governance_db sync
    python -m brightsmith.infra.governance_db export
    python -m brightsmith.infra.governance_db query <table>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    FloatType,
    IntegerType,
    NestedField,
    StringType,
    TimestamptzType,
)

from brightsmith.infra.grain import compute_grain_id
from brightsmith.infra.governance.serializers import normalize_table_name, normalize_zone

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Table access
# ---------------------------------------------------------------------------


def _get_governance_table(table_name: str):
    """Lazily create and return a governance Iceberg table."""
    from brightsmith.config import CATALOG_PATH, GOVERNANCE_WAREHOUSE
    from brightsmith.infra.iceberg_setup import get_catalog, get_or_create_table

    if table_name not in _TABLE_CONFIGS:
        raise ValueError(f"Unknown governance table: {table_name}")

    schema, _ = _TABLE_CONFIGS[table_name]
    catalog = get_catalog(GOVERNANCE_WAREHOUSE, CATALOG_PATH)
    return get_or_create_table(catalog, "governance_product", table_name, schema)


def _write_records(table_name: str, records: list[dict]) -> dict:
    """Write records to a governance table via promote().

    Computes grain IDs and uses promote() for idempotent append.
    Returns promote result dict.
    """
    from brightsmith.infra.promote import promote

    if not records:
        return {"promoted": 0, "skipped": 0, "snapshot_id": None}

    _, grain_fields = _TABLE_CONFIGS[table_name]
    prefix = _GRAIN_PREFIXES.get(table_name, table_name.upper()[:4])

    for record in records:
        # Compute grain ID — need string representation of timestamps
        grain_row = {}
        for f in grain_fields:
            val = record.get(f, "")
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            grain_row[f] = val
        record["record_id"] = compute_grain_id(grain_row, grain_fields, prefix=prefix)

    table = _get_governance_table(table_name)
    return promote(table, records)


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------


def write_spec_registry(
    spec_name: str,
    zone: str,
    status: str,
    output_tables: list[str],
    updated_by: str,
    *,
    dq_score_pct: float | None = None,
    dq_rules_total: int | None = None,
    dq_rules_passing: int | None = None,
    dq_rules_failing: int | None = None,
    dq_p0_passed: bool | None = None,
    has_contract: bool | None = None,
    has_lineage: bool | None = None,
    has_golden_dataset: bool | None = None,
    has_data_dictionary: bool | None = None,
    has_cde_tags: bool | None = None,
    pipeline_step_current: str | None = None,
    pipeline_steps_total: int | None = None,
    pipeline_steps_completed: int | None = None,
    spec_file_path: str | None = None,
) -> dict:
    """Write a spec registry row. Append-only; latest row wins."""
    now = datetime.now(timezone.utc)
    canonical_tables = [normalize_table_name(table) for table in output_tables]
    record = {
        "spec_name": spec_name,
        "zone": normalize_zone(zone),
        "status": status,
        "output_tables": json.dumps(canonical_tables),
        "dq_score_pct": dq_score_pct,
        "dq_rules_total": dq_rules_total,
        "dq_rules_passing": dq_rules_passing,
        "dq_rules_failing": dq_rules_failing,
        "dq_p0_passed": dq_p0_passed,
        "has_contract": has_contract,
        "has_lineage": has_lineage,
        "has_golden_dataset": has_golden_dataset,
        "has_data_dictionary": has_data_dictionary,
        "has_cde_tags": has_cde_tags,
        "pipeline_step_current": pipeline_step_current,
        "pipeline_steps_total": pipeline_steps_total,
        "pipeline_steps_completed": pipeline_steps_completed,
        "spec_file_path": spec_file_path,
        "updated_at": now,
        "updated_by": updated_by,
    }
    return _write_records("spec_registry", [record])


def write_dq_run(
    run_id: str,
    spec_name: str,
    table_name: str,
    executed_at: datetime,
    rules_total: int,
    rules_passed: int,
    rules_failed: int,
    rules_errored: int,
    score_pct: float,
    p0_passed: bool,
    *,
    rules_warning: int = 0,
    p0_total: int | None = None,
    p0_failed: int | None = None,
    p1_total: int | None = None,
    p1_failed: int | None = None,
    duration_ms: int | None = None,
    result_file_path: str | None = None,
) -> dict:
    """Write a DQ run summary row."""
    now = datetime.now(timezone.utc)
    record = {
        "run_id": run_id,
        "spec_name": spec_name,
        "table_name": normalize_table_name(table_name),
        "executed_at": executed_at,
        "rules_total": rules_total,
        "rules_passed": rules_passed,
        "rules_failed": rules_failed,
        "rules_errored": rules_errored,
        "rules_warning": rules_warning,
        "score_pct": score_pct,
        "p0_passed": p0_passed,
        "p0_total": p0_total,
        "p0_failed": p0_failed,
        "p1_total": p1_total,
        "p1_failed": p1_failed,
        "duration_ms": duration_ms,
        "result_file_path": result_file_path,
        "updated_at": now,
    }
    return _write_records("dq_runs", [record])


def write_dq_rule_results(run_id: str, spec_name: str, results: list[dict]) -> dict:
    """Write individual DQ rule results for a run.

    Args:
        run_id: FK to dq_runs.
        spec_name: FK to spec_registry.
        results: List of result dicts from dq_runner (rule_id, category, passed, etc.).
    """
    records = []
    for r in results:
        executed_at = r.get("executed_at")
        if isinstance(executed_at, str):
            executed_at = datetime.fromisoformat(executed_at)
        elif executed_at is None:
            executed_at = datetime.now(timezone.utc)

        records.append({
            "run_id": run_id,
            "spec_name": spec_name,
            "rule_id": r.get("rule_id", ""),
            "category": r.get("category", ""),
            "priority": r.get("priority", "P3"),
            "description": r.get("description", r.get("detail", "")),
            "passed": r.get("passed", False),
            "raw_value": str(r.get("raw_value")) if r.get("raw_value") is not None else None,
            "threshold": r.get("threshold"),
            "violations": r.get("violations"),
            "execution_time_ms": r.get("execution_time_ms"),
            "error_message": r.get("error"),
            "executed_at": executed_at,
        })
    return _write_records("dq_rule_results", records)


def write_pipeline_event(
    spec_name: str,
    step_name: str,
    event_type: str,
    *,
    agent_id: str | None = None,
    output_path: str | None = None,
    skip_reason: str | None = None,
    approval_decision: str | None = None,
    approval_by: str | None = None,
    notes: str | None = None,
    content: str | None = None,
    event_time: datetime | None = None,
) -> dict:
    """Write a pipeline step event."""
    record = {
        "spec_name": spec_name,
        "step_name": step_name,
        "event_type": event_type,
        "agent_id": agent_id,
        "output_path": output_path,
        "skip_reason": skip_reason,
        "approval_decision": approval_decision,
        "approval_by": approval_by,
        "notes": notes,
        "content": content,
        "event_time": event_time or datetime.now(timezone.utc),
    }
    return _write_records("pipeline_events", [record])


def sync_contract(contract: dict, contract_file_path: str) -> dict:
    """Sync a contract dict to contract_metadata and contract_columns tables."""
    from brightsmith.infra.governance.resolution import validate_contract_column_references

    meta = contract.get("metadata", {})
    schema = contract.get("schema", {})
    quality = contract.get("quality", {})
    table_name = normalize_table_name(schema.get("table", ""))
    namespace = normalize_zone(schema.get("namespace", table_name.split(".")[0] if "." in table_name else ""))

    record = {
        "contract_name": meta.get("name", ""),
        "spec_name": meta.get("spec") or None,
        "table_name": table_name,
        "zone": namespace,
        "version": meta.get("version", "1.0.0"),
        "status": meta.get("status", "draft"),
        "column_count": len(schema.get("columns", [])),
        "grain_columns": json.dumps(schema.get("grain", {}).get("columns", [])),
        "has_dq_rules": bool(quality.get("dq_rules", {}).get("rules_file")),
        "has_golden_dataset": bool(quality.get("accuracy", {}).get("golden_dataset")),
        "freshness_sla_hours": quality.get("freshness", {}).get("max_staleness_hours"),
        "contract_file_path": contract_file_path,
        "updated_at": datetime.now(timezone.utc),
    }
    result = _write_records("contract_metadata", [record])

    # Write column-level records
    columns = schema.get("columns", [])
    contract_name = meta.get("name", "")
    version = meta.get("version", "1.0.0")
    now = datetime.now(timezone.utc)

    col_records = []
    for i, col in enumerate(columns):
        validate_contract_column_references(col)
        col_records.append({
            "contract_name": contract_name,
            "table_name": table_name,
            "zone": namespace,
            "column_name": col.get("name", ""),
            "ordinal_position": i,
            "data_type": col.get("type"),
            "is_nullable": col.get("nullable", True),
            "is_cde": col.get("is_cde", False),
            "cde_rationale": col.get("cde_rationale"),
            "is_pii": col.get("is_pii", False),
            "pii_rationale": col.get("pii_rationale"),
            "business_term_id": col.get("business_term_id", col.get("business_term")),
            "cde_criteria_ids": json.dumps(col.get("cde_criteria_ids", [])),
            "criticality_classification_id": col.get("criticality_classification_id"),
            "policy_ids": json.dumps(col.get("policy_ids", [])),
            "pii_classification_id": col.get("pii_classification_id"),
            "description": col.get("description"),
            "version": version,
            "updated_at": now,
        })

    if col_records:
        col_result = _write_records("contract_columns", col_records)
        result["columns_promoted"] = col_result.get("promoted", 0)
        result["columns_skipped"] = col_result.get("skipped", 0)

    return result


def sync_glossary_term(term: dict) -> dict:
    """Import a glossary term into enterprise standards and legacy table."""
    from brightsmith.infra.governance.enterprise import write_business_term

    write_business_term(
        term_id=term.get("term_id", ""),
        term=term.get("name", term.get("term", "")),
        description=term.get("definition"),
        status=term.get("approval_status", "approved"),
        metadata={
            "category": term.get("category", ""),
            "source": term.get("source", ""),
            "used_in_specs": term.get("used_in_specs", []),
        },
    )
    record = {
        "term_id": term.get("term_id", ""),
        "term": term.get("name", term.get("term", "")),
        "definition": term.get("definition", ""),
        "category": term.get("category", ""),
        "source": term.get("source", ""),
        "approval_status": term.get("approval_status", term.get("status", "")),
        "used_in_specs": json.dumps(term.get("used_in_specs", [])),
        "updated_at": datetime.now(timezone.utc),
    }
    return _write_records("glossary_terms", [record])


def write_agent_activity(
    spec_name: str,
    agent_id: str,
    activity_type: str,
    severity: str,
    summary: str,
    *,
    pipeline_step: str | None = None,
    detail: str | None = None,
    references: list[str] | None = None,
    related_table: str | None = None,
    related_rule_id: str | None = None,
    resolution_status: str | None = None,
    resolved_by: str | None = None,
    resolved_at: datetime | None = None,
    event_time: datetime | None = None,
) -> dict:
    """Write an agent activity record."""
    record = {
        "spec_name": spec_name,
        "agent_id": agent_id,
        "pipeline_step": pipeline_step,
        "activity_type": activity_type,
        "severity": severity,
        "summary": summary,
        "detail": detail,
        "references": json.dumps(references) if references else None,
        "related_table": related_table,
        "related_rule_id": related_rule_id,
        "resolution_status": resolution_status,
        "resolved_by": resolved_by,
        "resolved_at": resolved_at,
        "event_time": event_time or datetime.now(timezone.utc),
    }
    return _write_records("agent_activity", [record])


def log_agent_finding(
    spec_name: str,
    agent_id: str,
    summary: str,
    detail: str | None = None,
    severity: str = "info",
    **kwargs,
) -> dict | None:
    """Convenience wrapper for write_agent_activity.

    Fault-tolerant: logs a warning on failure, never raises.
    """
    try:
        return write_agent_activity(
            spec_name=spec_name,
            agent_id=agent_id,
            activity_type=kwargs.pop("activity_type", "finding"),
            severity=severity,
            summary=summary,
            detail=detail,
            **kwargs,
        )
    except Exception:
        logger.warning(
            "Failed to log agent finding for %s/%s: %s",
            spec_name, agent_id, summary,
            exc_info=True,
        )
        return None


def write_dq_rules(
    spec_name: str,
    table_name: str,
    rules: list[dict],
) -> dict:
    """Write DQ rule definitions to the dq_rules table.

    Each rule dict should have: rule_id, category, priority, description, sql, threshold.
    Status defaults to 'proposed'. Version is determined automatically by querying
    MAX(version) for each rule_id and incrementing.
    """
    if not rules:
        return {"promoted": 0, "skipped": 0}

    # Query existing versions for this spec to determine next version per rule
    existing = _query_table("dq_rules", """
        SELECT rule_id, MAX(version) as max_version
        FROM arrow_table
        WHERE spec_name = $1
        GROUP BY rule_id
    """, [spec_name])
    version_map = {r["rule_id"]: r["max_version"] for r in existing}

    now = datetime.now(timezone.utc)
    records = []
    for r in rules:
        rule_id = r.get("rule_id", "")
        version = r.get("version")
        if version is None:
            version = (version_map.get(rule_id, 0) or 0) + 1
        records.append({
            "spec_name": spec_name,
            "table_name": normalize_table_name(table_name),
            "rule_id": rule_id,
            "category": r.get("category", ""),
            "priority": r.get("priority", "P3"),
            "description": r.get("description", ""),
            "sql": r.get("sql", ""),
            "threshold": r.get("threshold", ""),
            "status": r.get("status", "proposed"),
            "version": version,
            "approved_by": r.get("approved_by"),
            "approved_at": r.get("approved_at"),
            "updated_at": now,
        })
    return _write_records("dq_rules", records)


def write_dq_acknowledgment(
    run_id: str,
    rule_id: str,
    spec_name: str,
    acknowledged_by: str,
    reason: str,
    *,
    acknowledged_at: datetime | None = None,
) -> dict:
    """Write a DQ failure acknowledgment."""
    record = {
        "run_id": run_id,
        "rule_id": rule_id,
        "spec_name": spec_name,
        "acknowledged_by": acknowledged_by,
        "reason": reason,
        "acknowledged_at": acknowledged_at or datetime.now(timezone.utc),
    }
    return _write_records("dq_acknowledgments", [record])


def write_cab_decision(
    decision_id: str,
    spec_name: str,
    table_name: str,
    classification: str,
    classification_reasons: list[str],
    decision: str,
    *,
    contract_version_before: str | None = None,
    contract_version_after: str | None = None,
    schema_diff: dict | None = None,
    blast_radius: dict | None = None,
    decided_by: str | None = None,
    decided_at: datetime | None = None,
    notes: str | None = None,
    rationale: str | None = None,
    fork_config: dict | None = None,
    human_override: dict | None = None,
) -> dict:
    """Write a CAB decision record."""
    record = {
        "decision_id": decision_id,
        "spec_name": spec_name,
        "table_name": normalize_table_name(table_name),
        "classification": classification,
        "classification_reasons": json.dumps(classification_reasons),
        "contract_version_before": contract_version_before,
        "contract_version_after": contract_version_after,
        "schema_diff": json.dumps(schema_diff) if schema_diff else None,
        "blast_radius": json.dumps(blast_radius) if blast_radius else None,
        "decision": decision,
        "decided_by": decided_by,
        "decided_at": decided_at,
        "notes": notes,
        "rationale": rationale,
        "fork_config": json.dumps(fork_config) if fork_config else None,
        "human_override": json.dumps(human_override) if human_override else None,
        "created_at": datetime.now(timezone.utc),
    }
    return _write_records("cab_decisions", [record])


def write_golden_dataset_values(
    spec_name: str,
    table_name: str,
    values: list[dict],
) -> dict:
    """Write golden dataset values.

    Each value dict should have: value_description, column_name, expected_value, filters.
    Filters are normalized via json.dumps(sort_keys=True, separators=(',', ':'))
    before grain computation to ensure deterministic hashes.
    """
    now = datetime.now(timezone.utc)
    records = []
    for v in values:
        # Normalize filters for deterministic grain
        filters = v.get("filters", {})
        if isinstance(filters, str):
            filters_str = filters
        else:
            filters_str = json.dumps(filters, sort_keys=True, separators=(",", ":"))

        records.append({
            "spec_name": spec_name,
            "table_name": normalize_table_name(table_name),
            "value_description": v.get("value_description", ""),
            "column_name": v.get("column_name", ""),
            "expected_value": str(v.get("expected_value", "")),
            "tolerance_pct": v.get("tolerance_pct"),
            "tolerance_type": v.get("tolerance_type"),
            "filters": filters_str,
            "last_verified_at": v.get("last_verified_at"),
            "last_verified_passed": v.get("last_verified_passed"),
            "updated_at": now,
        })
    return _write_records("golden_datasets", records)


def write_run_history(
    run_id: str,
    started_at: datetime,
    status: str,
    zones_summary: dict,
    *,
    completed_at: datetime | None = None,
    duration_seconds: float | None = None,
    golden_datasets_summary: dict | None = None,
    options: dict | None = None,
    error_message: str | None = None,
) -> dict:
    """Write a pipeline run history record."""
    record = {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": duration_seconds,
        "status": status,
        "zones_summary": json.dumps(zones_summary),
        "golden_datasets_summary": json.dumps(golden_datasets_summary) if golden_datasets_summary else None,
        "options": json.dumps(options) if options else None,
        "error_message": error_message,
        "updated_at": datetime.now(timezone.utc),
    }
    return _write_records("run_history", [record])


def write_chaos_manifest(
    run_id: str,
    source_table: str,
    shadow_table: str,
    total_rows: int,
    corruption_rate: float,
    rows_corrupted: int,
    columns_corrupted: int,
    total_corruptions: int,
    *,
    seed: int | None = None,
    dimensions_covered: list[str] | None = None,
    corruptions_sample: list[dict] | None = None,
) -> dict:
    """Write a chaos monkey manifest record."""
    record = {
        "run_id": run_id,
        "source_table": normalize_table_name(source_table),
        "shadow_table": normalize_table_name(shadow_table),
        "total_rows": total_rows,
        "corruption_rate": corruption_rate,
        "seed": seed,
        "rows_corrupted": rows_corrupted,
        "columns_corrupted": columns_corrupted,
        "total_corruptions": total_corruptions,
        "dimensions_covered": json.dumps(dimensions_covered) if dimensions_covered else None,
        "corruptions_sample": json.dumps(corruptions_sample) if corruptions_sample else None,
        "created_at": datetime.now(timezone.utc),
    }
    return _write_records("chaos_manifests", [record])


def write_document(
    doc_type: str,
    doc_name: str,
    title: str,
    content: str,
    *,
    version: int | None = None,
    spec_name: str | None = None,
    agent_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Write a prose governance document.

    If version is not provided, auto-increments by querying MAX(version)
    for the given doc_type + doc_name.
    """
    if version is None:
        existing = _query_table("documents", """
            SELECT MAX(version) as max_version
            FROM arrow_table
            WHERE doc_type = $1 AND doc_name = $2
        """, [doc_type, doc_name])
        max_v = existing[0]["max_version"] if existing and existing[0]["max_version"] is not None else 0
        version = max_v + 1

    record = {
        "doc_type": doc_type,
        "doc_name": doc_name,
        "spec_name": spec_name,
        "agent_id": agent_id,
        "title": title,
        "content": content,
        "version": version,
        "metadata": json.dumps(metadata) if metadata else None,
        "created_at": datetime.now(timezone.utc),
    }
    return _write_records("documents", [record])


def write_data_dictionary(
    table_name: str,
    zone: str,
    columns: list[dict],
) -> dict:
    """Write data dictionary column records for a table.

    Each column dict should have: column_name, and optionally data_type,
    definition, nullable, is_grain, ordinal_position.
    """
    now = datetime.now(timezone.utc)
    records = []
    for i, col in enumerate(columns):
        records.append({
            "table_name": normalize_table_name(table_name),
            "zone": normalize_zone(zone),
            "column_name": col.get("column_name", col.get("name", "")),
            "data_type": col.get("data_type", col.get("type")),
            "definition": col.get("definition", col.get("description")),
            "nullable": col.get("nullable"),
            "is_grain": col.get("is_grain", col.get("grain", False)),
            "ordinal_position": col.get("ordinal_position", i),
            "updated_at": now,
        })
    return _write_records("data_dictionary", records)


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
        "updated_at": datetime.now(timezone.utc),
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
    now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)
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


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


def _query_table(table_name: str, sql: str, params: list | None = None) -> list[dict]:
    """Run a DuckDB query against a governance table."""
    import duckdb

    try:
        table = _get_governance_table(table_name)
        arrow_table = table.scan().to_arrow()
        if arrow_table.num_rows == 0:
            return []
        con = duckdb.connect()
        if params:
            rel = con.sql(sql, params=params)
        else:
            rel = con.sql(sql)
        columns = [desc[0] for desc in rel.description]
        rows = rel.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        logger.warning("Query failed on governance.%s", table_name, exc_info=True)
        return []


def get_current_specs() -> list[dict]:
    """Get current state of all specs (latest row per spec_name)."""
    return _query_table("spec_registry", """
        SELECT * FROM arrow_table
        WHERE (spec_name, updated_at) IN (
            SELECT spec_name, MAX(updated_at)
            FROM arrow_table
            GROUP BY spec_name
        )
        ORDER BY spec_name
    """)


def get_dq_runs(spec_name: str | None = None, limit: int = 20) -> list[dict]:
    """Get DQ run history."""
    if spec_name:
        return _query_table("dq_runs", """
            SELECT * FROM arrow_table
            WHERE spec_name = $1
            ORDER BY executed_at DESC
            LIMIT $2
        """, [spec_name, limit])
    return _query_table("dq_runs", """
        SELECT * FROM arrow_table
        ORDER BY executed_at DESC
        LIMIT $1
    """, [limit])


def get_latest_dq_run(spec_name: str) -> dict | None:
    """Get the most recent DQ run for a spec."""
    results = get_dq_runs(spec_name, limit=1)
    return results[0] if results else None


def get_dq_rule_results(run_id: str) -> list[dict]:
    """Get individual rule results for a DQ run."""
    return _query_table("dq_rule_results", """
        SELECT * FROM arrow_table
        WHERE run_id = $1
        ORDER BY rule_id
    """, [run_id])


def get_pipeline_events(spec_name: str) -> list[dict]:
    """Get pipeline events for a spec, ordered chronologically."""
    return _query_table("pipeline_events", """
        SELECT * FROM arrow_table
        WHERE spec_name = $1
        ORDER BY event_time
    """, [spec_name])


def get_contracts() -> list[dict]:
    """Get current contract metadata (latest version per contract)."""
    return _query_table("contract_metadata", """
        SELECT * FROM arrow_table
        WHERE (contract_name, updated_at) IN (
            SELECT contract_name, MAX(updated_at)
            FROM arrow_table
            GROUP BY contract_name
        )
        ORDER BY contract_name
    """)


def get_contract_columns(contract_name: str | None = None) -> list[dict]:
    """Get contract column records, optionally filtered by contract name."""
    if contract_name:
        rows = _query_table("contract_columns", """
            SELECT * FROM arrow_table
            WHERE contract_name = $1
            ORDER BY ordinal_position
        """, [contract_name])
    else:
        rows = _query_table("contract_columns", """
        SELECT * FROM arrow_table
        ORDER BY contract_name, ordinal_position
    """)
    for row in rows:
        row.setdefault("business_term", row.get("business_term_id"))
    return rows


def get_agent_activity(
    spec_name: str | None = None,
    agent_id: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Get agent activity records with optional filters."""
    conditions = ["1=1"]
    params: list = []
    idx = 0

    if spec_name:
        idx += 1
        conditions.append(f"spec_name = ${idx}")
        params.append(spec_name)
    if agent_id:
        idx += 1
        conditions.append(f"agent_id = ${idx}")
        params.append(agent_id)
    if severity:
        idx += 1
        conditions.append(f"severity = ${idx}")
        params.append(severity)
    idx += 1
    where = " AND ".join(conditions)

    return _query_table("agent_activity", f"""
        SELECT * FROM arrow_table
        WHERE {where}
        ORDER BY event_time DESC
        LIMIT ${idx}
    """, params + [limit])


def get_dq_rules(spec_name: str, *, table_name: str | None = None) -> list[dict]:
    """Get current DQ rules for a spec (latest version per rule_id)."""
    if table_name:
        table_name = normalize_table_name(table_name)
        return _query_table("dq_rules", """
            SELECT * FROM arrow_table
            WHERE spec_name = $1 AND table_name = $2
              AND (spec_name, rule_id, version) IN (
                SELECT spec_name, rule_id, MAX(version)
                FROM arrow_table
                WHERE spec_name = $1 AND table_name = $2
                GROUP BY spec_name, rule_id
              )
            ORDER BY rule_id
        """, [spec_name, table_name])
    return _query_table("dq_rules", """
        SELECT * FROM arrow_table
        WHERE spec_name = $1
          AND (spec_name, rule_id, version) IN (
            SELECT spec_name, rule_id, MAX(version)
            FROM arrow_table
            WHERE spec_name = $1
            GROUP BY spec_name, rule_id
          )
        ORDER BY rule_id
    """, [spec_name])


def get_dq_acknowledgments(run_id: str | None = None, spec_name: str | None = None) -> list[dict]:
    """Get DQ acknowledgments, optionally filtered by run_id or spec_name."""
    if run_id:
        return _query_table("dq_acknowledgments", """
            SELECT * FROM arrow_table WHERE run_id = $1 ORDER BY acknowledged_at
        """, [run_id])
    if spec_name:
        return _query_table("dq_acknowledgments", """
            SELECT * FROM arrow_table WHERE spec_name = $1 ORDER BY acknowledged_at
        """, [spec_name])
    return _query_table("dq_acknowledgments", """
        SELECT * FROM arrow_table ORDER BY acknowledged_at DESC LIMIT 100
    """)


def get_cab_decisions(
    spec_name: str | None = None,
    table_name: str | None = None,
    decision_id: str | None = None,
) -> list[dict]:
    """Get CAB decisions with optional filters."""
    if decision_id:
        return _query_table("cab_decisions", """
            SELECT * FROM arrow_table WHERE decision_id = $1
        """, [decision_id])
    if spec_name:
        return _query_table("cab_decisions", """
            SELECT * FROM arrow_table WHERE spec_name = $1 ORDER BY created_at DESC
        """, [spec_name])
    if table_name:
        return _query_table("cab_decisions", """
            SELECT * FROM arrow_table WHERE table_name = $1 ORDER BY created_at DESC
        """, [table_name])
    return _query_table("cab_decisions", """
        SELECT * FROM arrow_table ORDER BY created_at DESC LIMIT 100
    """)


def get_golden_dataset(spec_name: str) -> list[dict]:
    """Get golden dataset values for a spec."""
    return _query_table("golden_datasets", """
        SELECT * FROM arrow_table WHERE spec_name = $1 ORDER BY column_name
    """, [spec_name])


def get_run_history(limit: int = 20) -> list[dict]:
    """Get pipeline run history, most recent first."""
    return _query_table("run_history", """
        SELECT * FROM arrow_table ORDER BY started_at DESC LIMIT $1
    """, [limit])


def get_chaos_manifest(run_id: str) -> dict | None:
    """Get a chaos manifest by run_id."""
    results = _query_table("chaos_manifests", """
        SELECT * FROM arrow_table WHERE run_id = $1
    """, [run_id])
    return results[0] if results else None


def get_document(doc_type: str, doc_name: str, *, version: int | None = None) -> dict | None:
    """Get a document by type and name. Returns latest version if version not specified."""
    if version:
        results = _query_table("documents", """
            SELECT * FROM arrow_table
            WHERE doc_type = $1 AND doc_name = $2 AND version = $3
        """, [doc_type, doc_name, version])
    else:
        results = _query_table("documents", """
            SELECT * FROM arrow_table
            WHERE doc_type = $1 AND doc_name = $2
            ORDER BY version DESC LIMIT 1
        """, [doc_type, doc_name])
    return results[0] if results else None


def get_documents_by_type(doc_type: str, *, spec_name: str | None = None) -> list[dict]:
    """Get all documents of a given type (latest version per doc_name)."""
    if spec_name:
        return _query_table("documents", """
            SELECT * FROM arrow_table
            WHERE doc_type = $1 AND spec_name = $2
              AND (doc_type, doc_name, version) IN (
                SELECT doc_type, doc_name, MAX(version)
                FROM arrow_table
                WHERE doc_type = $1 AND spec_name = $2
                GROUP BY doc_type, doc_name
              )
            ORDER BY doc_name
        """, [doc_type, spec_name])
    return _query_table("documents", """
        SELECT * FROM arrow_table
        WHERE doc_type = $1
          AND (doc_type, doc_name, version) IN (
            SELECT doc_type, doc_name, MAX(version)
            FROM arrow_table
            WHERE doc_type = $1
            GROUP BY doc_type, doc_name
          )
        ORDER BY doc_name
    """, [doc_type])


def get_data_dictionary(
    table_name: str | None = None,
    zone: str | None = None,
) -> list[dict]:
    """Get data dictionary entries, optionally filtered by table_name or zone."""
    if table_name:
        return _query_table("data_dictionary", """
            SELECT * FROM arrow_table
            WHERE table_name = $1
            ORDER BY ordinal_position
        """, [table_name])
    if zone:
        return _query_table("data_dictionary", """
            SELECT * FROM arrow_table
            WHERE zone = $1
            ORDER BY table_name, ordinal_position
        """, [zone])
    return _query_table("data_dictionary", """
        SELECT * FROM arrow_table ORDER BY table_name, ordinal_position
    """)


def get_model_entities(
    level: str | None = None,
    zone: str | None = None,
    entity_group: str | None = None,
) -> list[dict]:
    """Get model entities with optional filters."""
    conditions = ["1=1"]
    params: list = []
    idx = 0

    if level:
        idx += 1
        conditions.append(f"level = ${idx}")
        params.append(level)
    if zone:
        idx += 1
        conditions.append(f"zone = ${idx}")
        params.append(zone)
    if entity_group:
        idx += 1
        conditions.append(f"entity_group = ${idx}")
        params.append(entity_group)

    where = " AND ".join(conditions)
    return _query_table("model_entities", f"""
        SELECT * FROM arrow_table WHERE {where} ORDER BY entity_id
    """, params or None)


def get_model_columns(
    entity_id: str | None = None,
    level: str | None = None,
) -> list[dict]:
    """Get model columns with optional filters."""
    if entity_id and level:
        return _query_table("model_columns", """
            SELECT * FROM arrow_table
            WHERE entity_id = $1 AND level = $2
            ORDER BY ordinal_position
        """, [entity_id, level])
    if entity_id:
        return _query_table("model_columns", """
            SELECT * FROM arrow_table
            WHERE entity_id = $1
            ORDER BY level, ordinal_position
        """, [entity_id])
    if level:
        return _query_table("model_columns", """
            SELECT * FROM arrow_table
            WHERE level = $1
            ORDER BY entity_id, ordinal_position
        """, [level])
    return _query_table("model_columns", """
        SELECT * FROM arrow_table ORDER BY entity_id, level, ordinal_position
    """)


def get_model_relationships(
    level: str | None = None,
    entity_group: str | None = None,
) -> list[dict]:
    """Get model relationships with optional filters."""
    if level and entity_group:
        return _query_table("model_relationships", """
            SELECT * FROM arrow_table
            WHERE level = $1 AND entity_group = $2
            ORDER BY relationship_id
        """, [level, entity_group])
    if level:
        return _query_table("model_relationships", """
            SELECT * FROM arrow_table WHERE level = $1 ORDER BY relationship_id
        """, [level])
    if entity_group:
        return _query_table("model_relationships", """
            SELECT * FROM arrow_table WHERE entity_group = $1 ORDER BY relationship_id
        """, [entity_group])
    return _query_table("model_relationships", """
        SELECT * FROM arrow_table ORDER BY level, relationship_id
    """)


def get_policies(
    policy_type: str | None = None,
    target_zone: str | None = None,
    target_table: str | None = None,
) -> list[dict]:
    """Get policies with optional filters."""
    if policy_type:
        return _query_table("policies", """
            SELECT * FROM arrow_table WHERE policy_type = $1 ORDER BY policy_name
        """, [policy_type])
    if target_zone:
        return _query_table("policies", """
            SELECT * FROM arrow_table WHERE target_zone = $1 ORDER BY policy_name
        """, [target_zone])
    if target_table:
        return _query_table("policies", """
            SELECT * FROM arrow_table WHERE target_table = $1 ORDER BY policy_name
        """, [target_table])
    return _query_table("policies", """
        SELECT * FROM arrow_table ORDER BY policy_type, policy_name
    """)


def get_scorecard_data(spec_name: str) -> dict | None:
    """Get scorecard data by joining dq_runs + dq_rule_results + dq_rules.

    Returns a dict with run info and enriched rule results (with category/priority
    from dq_rules table if available).
    """
    latest_run = get_latest_dq_run(spec_name)
    if not latest_run:
        return None

    run_id = latest_run.get("run_id", "")
    rule_results = get_dq_rule_results(run_id)
    rules = get_dq_rules(spec_name)

    # Build rule lookup for enrichment
    rule_lookup = {r["rule_id"]: r for r in rules}

    # Enrich rule results with dq_rules data
    enriched = []
    for rr in rule_results:
        rule_def = rule_lookup.get(rr.get("rule_id", ""), {})
        enriched.append({
            **rr,
            "rule_sql": rule_def.get("sql", ""),
            "rule_status": rule_def.get("status", ""),
            "rule_version": rule_def.get("version"),
        })

    return {
        "run": latest_run,
        "results": enriched,
        "rules_count": len(rules),
    }


def get_governance_summary() -> dict:
    """Comprehensive governance summary for Brightforge dashboard.

    Returns aggregated DQ scores, governance completeness, pipeline progress,
    and zone-level rollups in a single call.
    """
    summary: dict = {
        "specs": [],
        "dq_overall": {"score_pct": 0.0, "rules_total": 0, "rules_passing": 0, "rules_failing": 0, "p0_passed": True},
        "governance_completeness": {"total_specs": 0, "with_dq": 0, "with_contract": 0, "with_lineage": 0, "with_golden_dataset": 0},
        "zones": {},
        "open_blockers": [],
    }

    specs = get_current_specs()
    if not specs:
        return summary

    summary["specs"] = specs
    total = len(specs)
    summary["governance_completeness"]["total_specs"] = total

    total_rules = 0
    total_passing = 0
    total_failing = 0
    all_p0 = True

    for s in specs:
        zone = s.get("zone", "unknown")
        summary["zones"].setdefault(zone, {"specs": 0, "complete": 0, "dq_score": 0.0})
        summary["zones"][zone]["specs"] += 1
        if s.get("status") == "COMPLETE":
            summary["zones"][zone]["complete"] += 1

        rt = s.get("dq_rules_total") or 0
        rp = s.get("dq_rules_passing") or 0
        rf = s.get("dq_rules_failing") or 0
        total_rules += rt
        total_passing += rp
        total_failing += rf
        if s.get("dq_p0_passed") is False:
            all_p0 = False

        if rt > 0:
            summary["governance_completeness"]["with_dq"] += 1
        if s.get("has_contract"):
            summary["governance_completeness"]["with_contract"] += 1
        if s.get("has_lineage"):
            summary["governance_completeness"]["with_lineage"] += 1
        if s.get("has_golden_dataset"):
            summary["governance_completeness"]["with_golden_dataset"] += 1

    summary["dq_overall"]["rules_total"] = total_rules
    summary["dq_overall"]["rules_passing"] = total_passing
    summary["dq_overall"]["rules_failing"] = total_failing
    summary["dq_overall"]["p0_passed"] = all_p0
    if total_rules > 0:
        summary["dq_overall"]["score_pct"] = round(total_passing / total_rules * 100, 1)

    for zone_data in summary["zones"].values():
        if zone_data["specs"] > 0:
            # Average DQ score would require per-spec data; just count completion
            pass

    # Open blockers from agent_activity
    summary["open_blockers"] = get_agent_activity(severity="blocker")

    return summary


# ---------------------------------------------------------------------------
# Sync: backfill from existing file artifacts
# ---------------------------------------------------------------------------


def sync_from_files() -> dict:
    """Backfill governance tables from existing file artifacts.

    Idempotent via promote() — safe to run repeatedly.

    Returns counts of records synced per table.
    """
    from brightsmith.config import (
        DQ_RESULTS_DIR,
        DQ_RULES_DIR,
        PIPELINE_STATE_DIR,
        PROJECT_ROOT,
    )

    counts: dict[str, int] = {}

    # 1. DQ results -> dq_runs + dq_rule_results
    dq_synced = 0
    dq_rules_synced = 0
    if DQ_RESULTS_DIR.exists():
        for path in sorted(DQ_RESULTS_DIR.glob("*.json")):
            if "-ack-" in path.name:
                continue
            try:
                data = json.loads(path.read_text())
                run_id = data.get("run_id", "")
                spec = data.get("spec", "")
                executed_at_str = data.get("executed_at", "")
                executed_at = datetime.fromisoformat(executed_at_str) if executed_at_str else datetime.now(timezone.utc)

                # Extract table names from rules
                rules = data.get("results", [])
                tables = set()
                for r in rules:
                    if r.get("spec"):
                        for rule_file in DQ_RULES_DIR.glob("*.json"):
                            rd = json.loads(rule_file.read_text())
                            if rd.get("spec") == r["spec"]:
                                tables.update(rd.get("tables", []))
                table_name = ", ".join(sorted(tables)) if tables else spec

                total = (data.get("rules_total")
                         or data.get("rules_executed")
                         or data.get("total_rules")
                         or len(rules))
                passed = data.get("rules_passed") or data.get("passed") or sum(1 for r in rules if r.get("passed"))
                failed = data.get("rules_failed") or data.get("failed") or sum(1 for r in rules if not r.get("passed"))
                errored = data.get("rules_errored", sum(1 for r in rules if r.get("error")))
                score = (passed / total * 100) if total > 0 else 0.0

                # p0_passed can be bool or string like "PASSED"
                p0_raw = data.get("p0_passed") or data.get("p0_gate")
                if isinstance(p0_raw, str):
                    p0_passed = p0_raw.upper() in ("PASSED", "PASS", "TRUE")
                else:
                    p0_passed = bool(p0_raw) if p0_raw is not None else True

                result = write_dq_run(
                    run_id=run_id, spec_name=spec, table_name=table_name,
                    executed_at=executed_at, rules_total=total, rules_passed=passed,
                    rules_failed=failed, rules_errored=errored, score_pct=score,
                    p0_passed=p0_passed,
                    result_file_path=str(path.relative_to(PROJECT_ROOT)),
                )
                dq_synced += result.get("promoted", 0)

                # Individual rule results
                rule_results = write_dq_rule_results(run_id, spec, rules)
                dq_rules_synced += rule_results.get("promoted", 0)
            except Exception:
                logger.warning("Failed to sync DQ results from %s", path, exc_info=True)

    counts["dq_runs"] = dq_synced
    counts["dq_rule_results"] = dq_rules_synced

    # 2. Pipeline state -> pipeline_events + spec_registry
    pipeline_synced = 0
    registry_synced = 0
    if PIPELINE_STATE_DIR.exists():
        for path in sorted(PIPELINE_STATE_DIR.glob("*-pipeline.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem.replace("-pipeline", ""))
                zone = data.get("zone", "")
                steps = data.get("steps", {})

                # Pipeline events from step completions
                for step_name, step_data in steps.items():
                    status = step_data.get("status", "NOT_STARTED")
                    if status in ("COMPLETED", "SKIPPED"):
                        event_type = status
                        event_time_str = step_data.get("completed_at") or step_data.get("started_at")
                        event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(timezone.utc)
                        result = write_pipeline_event(
                            spec_name=spec, step_name=step_name,
                            event_type=event_type,
                            agent_id=step_data.get("agent"),
                            output_path=step_data.get("output"),
                            event_time=event_time,
                        )
                        pipeline_synced += result.get("promoted", 0)

                # Skipped steps
                for step_name, skip_data in data.get("skipped_steps", {}).items():
                    event_time_str = skip_data.get("skipped_at")
                    event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(timezone.utc)
                    result = write_pipeline_event(
                        spec_name=spec, step_name=step_name,
                        event_type="SKIPPED",
                        skip_reason=skip_data.get("reason"),
                        event_time=event_time,
                    )
                    pipeline_synced += result.get("promoted", 0)

                # Approvals
                for artifact, approval in data.get("approvals", {}).items():
                    event_time_str = approval.get("decided_at")
                    event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(timezone.utc)
                    result = write_pipeline_event(
                        spec_name=spec, step_name=artifact,
                        event_type="APPROVED" if approval.get("status") == "APPROVED" else "FAILED",
                        approval_decision=approval.get("status"),
                        approval_by=approval.get("decided_by"),
                        notes=approval.get("notes"),
                        event_time=event_time,
                    )
                    pipeline_synced += result.get("promoted", 0)

                # Spec registry from pipeline state
                completed = sum(1 for s in steps.values() if s.get("status") == "COMPLETED")
                total_steps = len(steps)
                spec_status = data.get("status", "IN_PROGRESS")
                output_tables = data.get("output_tables", [])

                result = write_spec_registry(
                    spec_name=spec, zone=zone, status=spec_status,
                    output_tables=output_tables, updated_by="sync",
                    pipeline_steps_total=total_steps,
                    pipeline_steps_completed=completed,
                    spec_file_path=f"docs/specs/{spec}.md",
                )
                registry_synced += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to sync pipeline state from %s", path, exc_info=True)

    counts["pipeline_events"] = pipeline_synced
    counts["spec_registry"] = registry_synced

    # 3. Contracts -> contract_metadata
    contract_synced = 0
    contracts_dir = PROJECT_ROOT / "governance" / "data-contracts"
    if contracts_dir.exists():
        for path in sorted(contracts_dir.glob("*.yaml")):
            try:
                import yaml
                text = path.read_text()
                # Handle multi-document YAML (some contracts have --- separators)
                docs = [d for d in yaml.safe_load_all(text) if d is not None]
                for data in docs:
                    # Normalize: some contracts use "contract:" wrapper, others use "metadata:"
                    if "contract" in data and "metadata" not in data:
                        inner = data["contract"]
                        data = {
                            "metadata": {
                                "name": inner.get("name", ""),
                                "version": inner.get("version", "1.0.0"),
                                "status": inner.get("status", "active"),
                                "spec": inner.get("spec", ""),
                            },
                            "schema": {
                                "table": inner.get("name", ""),
                                "namespace": inner.get("name", "").split(".")[0] if "." in inner.get("name", "") else "",
                                "grain": {"columns": inner.get("grain", [])},
                                "columns": inner.get("columns", []),
                            },
                            "quality": inner.get("quality", {}),
                        }
                    rel_path = str(path.relative_to(PROJECT_ROOT))
                    result = sync_contract(data, rel_path)
                    contract_synced += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to sync contract from %s", path, exc_info=True)
    counts["contract_metadata"] = contract_synced

    # 4. Glossary -> glossary_terms
    glossary_synced = 0
    glossary_path = PROJECT_ROOT / "governance" / "business-glossary.json"
    if glossary_path.exists():
        try:
            data = json.loads(glossary_path.read_text())
            for term in data.get("terms", []):
                result = sync_glossary_term(term)
                glossary_synced += result.get("promoted", 0)
        except Exception:
            logger.warning("Failed to sync glossary", exc_info=True)
    counts["glossary_terms"] = glossary_synced

    # 5. Enrich spec_registry with DQ scores and governance completeness flags
    #    by cross-referencing the data we just synced
    enriched = 0
    if PIPELINE_STATE_DIR.exists():
        for path in sorted(PIPELINE_STATE_DIR.glob("*-pipeline.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem.replace("-pipeline", ""))
                zone = data.get("zone", "")
                output_tables = data.get("output_tables", [])

                # DQ scores from latest results file
                dq_kwargs: dict = {}
                if DQ_RESULTS_DIR.exists():
                    dq_files = sorted(DQ_RESULTS_DIR.glob(f"{spec}-*.json"), reverse=True)
                    dq_files = [f for f in dq_files if "-ack-" not in f.name]
                    if dq_files:
                        dq_data = json.loads(dq_files[0].read_text())
                        total = (dq_data.get("rules_total")
                                 or dq_data.get("rules_executed")
                                 or dq_data.get("total_rules")
                                 or 0)
                        passed = dq_data.get("rules_passed") or dq_data.get("passed") or 0
                        failed = dq_data.get("rules_failed") or dq_data.get("failed") or 0
                        # p0_passed can be bool or string like "PASSED"
                        p0_raw = dq_data.get("p0_passed") or dq_data.get("p0_gate")
                        if isinstance(p0_raw, str):
                            p0_passed = p0_raw.upper() in ("PASSED", "PASS", "TRUE")
                        else:
                            p0_passed = bool(p0_raw) if p0_raw is not None else True
                        dq_kwargs = {
                            "dq_score_pct": (passed / total * 100) if total > 0 else 0.0,
                            "dq_rules_total": total,
                            "dq_rules_passing": passed,
                            "dq_rules_failing": failed,
                            "dq_p0_passed": p0_passed,
                        }

                # Governance completeness flags
                contracts_dir_path = PROJECT_ROOT / "governance" / "data-contracts"
                has_contract = False
                if contracts_dir_path.exists():
                    # Check by spec name or table name slug
                    spec_slug = spec.replace("_", "-")
                    has_contract = bool(
                        any(contracts_dir_path.glob(f"*{spec_slug}*"))
                        or any(
                            contracts_dir_path.glob(f"*{tbl.replace('.', '-').replace('_', '-')}*")
                            for tbl in output_tables
                        )
                    )

                lineage_dir = PROJECT_ROOT / "governance" / "lineage"
                has_lineage = lineage_dir.exists() and any(lineage_dir.glob(f"*{spec}*"))

                gd_dir = PROJECT_ROOT / "governance" / "golden-datasets"
                has_golden = gd_dir.exists() and any(gd_dir.glob(f"*{spec}*"))

                steps = data.get("steps", {})
                completed = sum(1 for s in steps.values() if s.get("status") in ("COMPLETED", "SKIPPED"))

                result = write_spec_registry(
                    spec_name=spec, zone=zone,
                    status=data.get("status", "IN_PROGRESS"),
                    output_tables=output_tables, updated_by="sync",
                    pipeline_steps_total=len(steps),
                    pipeline_steps_completed=completed,
                    spec_file_path=f"docs/specs/{spec}.md",
                    has_contract=has_contract,
                    has_lineage=has_lineage,
                    has_golden_dataset=has_golden,
                    **dq_kwargs,
                )
                enriched += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to enrich spec registry for %s", path, exc_info=True)
    counts["spec_registry_enriched"] = enriched

    # 6. Data Dictionary backfill -> data_dictionary
    dict_synced = 0
    data_dict_path = PROJECT_ROOT / "governance" / "data-dictionary.json"
    if data_dict_path.exists():
        try:
            data = json.loads(data_dict_path.read_text())
            for tbl in data.get("tables", []):
                table_name = tbl.get("table_name", tbl.get("name", ""))
                zone = tbl.get("zone", tbl.get("namespace", ""))
                columns = tbl.get("fields", tbl.get("columns", []))
                if table_name and columns:
                    result = write_data_dictionary(table_name, zone, columns)
                    dict_synced += result.get("promoted", 0)
        except Exception:
            logger.warning("Failed to sync data dictionary", exc_info=True)
    counts["data_dictionary"] = dict_synced

    # 7. Data Models backfill -> model_entities, model_columns, model_relationships
    entities_synced = 0
    columns_synced = 0
    rels_synced = 0
    models_dir = PROJECT_ROOT / "governance" / "models"
    if models_dir.exists():
        for path in sorted(models_dir.glob("*.md")):
            try:
                content = path.read_text()
                # Infer level from filename: e.g. "financial-conceptual.md" -> "conceptual"
                stem = path.stem  # e.g. "financial-conceptual"
                level = "logical"
                for lvl in ("conceptual", "logical", "physical"):
                    if stem.endswith(f"-{lvl}"):
                        level = lvl
                        break
                entity_group = stem.replace(f"-{level}", "") if stem.endswith(f"-{level}") else stem

                diagram = _parse_mermaid_erdiagram(content)
                if diagram is None:
                    continue

                for entity in diagram["entities"]:
                    entity_name = entity["name"]
                    entity_id = f"{entity_group}.{entity_name}.{level}"
                    result = write_model_entity(
                        entity_id=entity_id,
                        entity_group=entity_group,
                        table_name=entity_name.lower(),
                        zone=entity_group,
                        display_name=entity_name,
                        level=level,
                    )
                    entities_synced += result.get("promoted", 0)

                    if entity["columns"]:
                        result = write_model_columns(entity_id, level, entity["columns"])
                        columns_synced += result.get("promoted", 0)

                if diagram["relationships"]:
                    result = write_model_relationships(entity_group, level, diagram["relationships"])
                    rels_synced += result.get("promoted", 0)

            except Exception:
                logger.warning("Failed to sync model from %s", path, exc_info=True)
    counts["model_entities"] = entities_synced
    counts["model_columns"] = columns_synced
    counts["model_relationships"] = rels_synced

    # 8. Policies backfill -> policies
    policies_synced = 0
    policies_dir = PROJECT_ROOT / "governance" / "policies"
    if policies_dir.exists():
        for path in sorted(policies_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                # Each file may contain a single policy dict or an array of policies
                policy_list = data if isinstance(data, list) else [data]
                for p in policy_list:
                    result = write_policy(
                        policy_id=p.get("policy_id", path.stem),
                        policy_name=p.get("policy_name", p.get("name", path.stem)),
                        policy_type=p.get("policy_type", p.get("type", "")),
                        enforcement=p.get("enforcement", "advisory"),
                        target_table=p.get("target_table"),
                        target_zone=p.get("target_zone", p.get("zone")),
                        description=p.get("description"),
                        config=p.get("config"),
                        created_by=p.get("created_by"),
                    )
                    policies_synced += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to sync policy from %s", path, exc_info=True)
    counts["policies"] = policies_synced

    # 9. Domain Context backfill -> documents (doc_type="domain_context")
    domain_context_synced = 0
    domain_context_path = PROJECT_ROOT / "governance" / "domain-context.md"
    if domain_context_path.exists():
        try:
            content_text = domain_context_path.read_text()
            result = write_document(
                doc_type="domain_context",
                doc_name="domain_context",
                title="Domain Context",
                content=content_text,
            )
            domain_context_synced += result.get("promoted", 0)
        except Exception:
            logger.warning("Failed to sync domain context", exc_info=True)
    counts["domain_context"] = domain_context_synced

    return counts


# ---------------------------------------------------------------------------
# Mermaid erDiagram parser (simplified — used by sync_from_files backfill)
# ---------------------------------------------------------------------------


def _parse_mermaid_erdiagram(markdown: str) -> dict | None:
    """Parse a Mermaid erDiagram block from markdown into a simple dict.

    Returns a dict with 'entities' and 'relationships' lists, or None if
    no erDiagram block is found.

    Entity format: {"name": str, "columns": list[dict]}
    Column format: {"column_name": str, "data_type": str, "is_pk": bool,
                    "is_fk": bool, "description": str|None, "source_mapping": str|None}
    Relationship format: {"relationship_id": str, "source_entity": str,
                          "target_entity": str, "source_cardinality": str|None,
                          "target_cardinality": str|None, "label": str|None}
    """
    import re

    # Extract erDiagram block
    block_match = re.search(r"```mermaid\s*\nerDiagram\s*\n(.*?)```", markdown, re.DOTALL)
    if not block_match:
        return None
    block = block_match.group(1).strip()

    entities: list[dict] = []
    entity_names: set[str] = set()

    # Parse entity blocks: entity_name { ... }
    entity_pattern = re.compile(r"(\w{2,})\s*\{([^}]*)\}", re.DOTALL)
    for m in entity_pattern.finditer(block):
        entity_name = m.group(1)
        body = m.group(2).strip()
        columns = _parse_mermaid_columns(body)
        entities.append({"name": entity_name, "columns": columns})
        entity_names.add(entity_name)

    relationships: list[dict] = []
    rel_pattern = re.compile(
        r"(\w+)\s+([|o{}]{2})(--|-\.)([|o{}]{2})\s+(\w+)\s*:\s*\"([^\"]*)\""
    )
    _cardinality_map = {
        "||": "1", "|o": "0..1", "o|": "0..1",
        "}|": "1..*", "|}": "1..*", "}o": "0..*",
        "o{": "0..*", "|{": "1..*",
    }
    for m in rel_pattern.finditer(block):
        source = m.group(1)
        left_card = _cardinality_map.get(m.group(2), m.group(2))
        right_card = _cardinality_map.get(m.group(4), m.group(4))
        target = m.group(5)
        label = m.group(6)

        # Add entities that only appear in relationships (conceptual models)
        if source not in entity_names:
            entities.append({"name": source, "columns": []})
            entity_names.add(source)
        if target not in entity_names:
            entities.append({"name": target, "columns": []})
            entity_names.add(target)

        rel_id = f"{source}__{target}"
        relationships.append({
            "relationship_id": rel_id,
            "source_entity": source,
            "target_entity": target,
            "source_cardinality": left_card,
            "target_cardinality": right_card,
            "label": label,
            "source_column": None,
            "target_column": None,
        })

    return {"entities": entities, "relationships": relationships}


def _parse_mermaid_columns(body: str) -> list[dict]:
    """Parse column lines from a Mermaid entity body.

    Handles: TYPE column_name [PK|FK] ["description | source_mapping"]
    """
    import re

    columns = []
    col_pattern = re.compile(
        r"(\w+)\s+(\w+)(?:\s+(PK|FK))?(?:\s+\"([^\"]*)\")?"
    )
    for i, line in enumerate(body.split("\n")):
        line = line.strip()
        if not line:
            continue
        m = col_pattern.match(line)
        if m:
            data_type = m.group(1)
            col_name = m.group(2)
            key_marker = m.group(3)
            desc_raw = m.group(4)

            description = None
            source_mapping = None
            if desc_raw:
                if "|" in desc_raw:
                    parts = desc_raw.split("|", 1)
                    description = parts[0].strip() or None
                    source_mapping = parts[1].strip() or None
                else:
                    description = desc_raw.strip() or None

            columns.append({
                "column_name": col_name,
                "data_type": data_type,
                "is_pk": key_marker == "PK",
                "is_fk": key_marker == "FK",
                "nullable": key_marker not in ("PK",),
                "description": description,
                "source_mapping": source_mapping,
                "ordinal_position": i,
            })
    return columns


def migrate_files_to_iceberg() -> dict:
    """One-time migration of all governance file artifacts to Iceberg.

    Reads existing governance files and writes them to the 7 new Iceberg tables.
    Produces a validation report comparing file counts to Iceberg row counts.

    Idempotent via promote() — safe to run repeatedly.

    Returns a migration report dict with per-table counts and spot-check results.
    """
    from brightsmith.config import (
        AUDIT_TRAIL_DIR,
        CAB_DECISIONS_DIR,
        DQ_RESULTS_DIR,
        DQ_RULES_DIR,
        GOLDEN_DATASETS_DIR,
        PROJECT_ROOT,
    )

    report: dict = {}

    # 1. DQ Rules -> governance.dq_rules
    dq_rules_files = 0
    dq_rules_rows = 0
    if DQ_RULES_DIR.exists():
        for path in sorted(DQ_RULES_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem)
                tables = data.get("tables", [])
                table_name = ", ".join(tables) if tables else spec
                rules = data.get("rules", [])
                if rules:
                    dq_rules_files += 1
                    result = write_dq_rules(spec, table_name, rules)
                    dq_rules_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate DQ rules from %s", path, exc_info=True)
    report["dq_rules"] = {"files": dq_rules_files, "rows": dq_rules_rows}

    # 2. DQ Acknowledgments -> governance.dq_acknowledgments
    ack_files = 0
    ack_rows = 0
    if DQ_RESULTS_DIR.exists():
        for path in sorted(DQ_RESULTS_DIR.glob("*-ack-*.json")):
            try:
                data = json.loads(path.read_text())
                ack_files += 1
                run_id = data.get("run_id", "")
                spec = data.get("spec_name", data.get("spec", ""))
                for ack in data.get("acknowledgments", [data]):
                    result = write_dq_acknowledgment(
                        run_id=ack.get("run_id", run_id),
                        rule_id=ack.get("rule_id", ""),
                        spec_name=ack.get("spec_name", spec),
                        acknowledged_by=ack.get("acknowledged_by", ""),
                        reason=ack.get("reason", ""),
                    )
                    ack_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate ack from %s", path, exc_info=True)
    report["dq_acknowledgments"] = {"files": ack_files, "rows": ack_rows}

    # 3. CAB Decisions -> governance.cab_decisions
    cab_files = 0
    cab_rows = 0
    if CAB_DECISIONS_DIR.exists():
        for path in sorted(CAB_DECISIONS_DIR.glob("*.json")):
            if path.name == "index.json":
                continue
            try:
                data = json.loads(path.read_text())
                cab_files += 1
                result = write_cab_decision(
                    decision_id=data.get("decision_id", path.stem),
                    spec_name=data.get("spec_name", data.get("spec", "")),
                    table_name=data.get("table_name", data.get("table", "")),
                    classification=data.get("classification", ""),
                    classification_reasons=data.get("classification_reasons", data.get("reasons", [])),
                    decision=data.get("decision", data.get("status", "PENDING")),
                    contract_version_before=data.get("contract_version_before"),
                    contract_version_after=data.get("contract_version_after"),
                    schema_diff=data.get("schema_diff"),
                    blast_radius=data.get("blast_radius"),
                    decided_by=data.get("decided_by"),
                    notes=data.get("notes"),
                    rationale=data.get("rationale"),
                    fork_config=data.get("fork_config"),
                    human_override=data.get("human_override"),
                )
                cab_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate CAB decision from %s", path, exc_info=True)
    report["cab_decisions"] = {"files": cab_files, "rows": cab_rows}

    # 4. Golden Datasets -> governance.golden_datasets
    gd_files = 0
    gd_rows = 0
    if GOLDEN_DATASETS_DIR.exists():
        for path in sorted(GOLDEN_DATASETS_DIR.glob("*-golden.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem.replace("-golden", ""))
                table_name_val = data.get("table", "")
                values = data.get("values", [])
                if values:
                    gd_files += 1
                    result = write_golden_dataset_values(spec, table_name_val, values)
                    gd_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate golden dataset from %s", path, exc_info=True)
    report["golden_datasets"] = {"files": gd_files, "rows": gd_rows}

    # 5. Run History -> governance.run_history
    rh_files = 0
    rh_rows = 0
    run_history_dir = PROJECT_ROOT / "governance" / "run-history"
    if run_history_dir.exists():
        for path in sorted(run_history_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                rh_files += 1
                started_str = data.get("started_at", "")
                started = datetime.fromisoformat(started_str) if started_str else datetime.now(timezone.utc)
                completed_str = data.get("completed_at")
                completed = datetime.fromisoformat(completed_str) if completed_str else None
                result = write_run_history(
                    run_id=data.get("run_id", path.stem),
                    started_at=started,
                    status=data.get("status", "UNKNOWN"),
                    zones_summary=data.get("zones_summary", data.get("zones", {})),
                    completed_at=completed,
                    duration_seconds=data.get("duration_seconds"),
                    golden_datasets_summary=data.get("golden_datasets_summary"),
                    options=data.get("options"),
                    error_message=data.get("error_message", data.get("error")),
                )
                rh_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate run history from %s", path, exc_info=True)
    report["run_history"] = {"files": rh_files, "rows": rh_rows}

    # 6. Chaos Manifests -> governance.chaos_manifests
    cm_files = 0
    cm_rows = 0
    chaos_dir = PROJECT_ROOT / "governance" / "chaos-monkey"
    if chaos_dir.exists():
        for path in sorted(chaos_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                cm_files += 1
                result = write_chaos_manifest(
                    run_id=data.get("run_id", path.stem),
                    source_table=data.get("source_table", ""),
                    shadow_table=data.get("shadow_table", ""),
                    total_rows=data.get("total_rows", 0),
                    corruption_rate=data.get("corruption_rate", 0.0),
                    rows_corrupted=data.get("rows_corrupted", 0),
                    columns_corrupted=data.get("columns_corrupted", 0),
                    total_corruptions=data.get("total_corruptions", 0),
                    seed=data.get("seed"),
                    dimensions_covered=data.get("dimensions_covered"),
                    corruptions_sample=data.get("corruptions_sample", data.get("corruptions", []))[:100],
                )
                cm_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate chaos manifest from %s", path, exc_info=True)
    report["chaos_manifests"] = {"files": cm_files, "rows": cm_rows}

    # 7. Documents -> governance.documents (reviews, insights, models, etc.)
    doc_files = 0
    doc_rows = 0
    doc_dirs = {
        "review": PROJECT_ROOT / "governance" / "reviews",
        "insight": PROJECT_ROOT / "governance" / "insights",
        "model": PROJECT_ROOT / "governance" / "models",
        "approval": PROJECT_ROOT / "governance" / "approvals",
        "audit_trail": AUDIT_TRAIL_DIR,
        "eda": PROJECT_ROOT / "governance" / "eda",
    }
    for doc_type, doc_dir in doc_dirs.items():
        if not doc_dir.exists():
            continue
        for path in sorted(doc_dir.glob("*.md")):
            try:
                content_text = path.read_text()
                doc_name = path.stem
                # Extract title from first markdown heading
                title = doc_name
                for line in content_text.split("\n"):
                    if line.startswith("# ") or line.startswith("## "):
                        title = line.lstrip("#").strip()
                        break

                # Try to determine spec_name from filename
                spec = None
                # Common pattern: spec-name-suffix.md
                parts = doc_name.rsplit("-", 1)
                if len(parts) > 1:
                    spec = parts[0]

                doc_files += 1
                result = write_document(
                    doc_type=doc_type,
                    doc_name=doc_name,
                    title=title,
                    content=content_text,
                    version=1,
                    spec_name=spec,
                )
                doc_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate document from %s", path, exc_info=True)

    # Domain context is a single file
    domain_context_path = PROJECT_ROOT / "governance" / "domain-context.md"
    if domain_context_path.exists():
        try:
            content_text = domain_context_path.read_text()
            doc_files += 1
            result = write_document(
                doc_type="domain_context",
                doc_name="domain-context",
                title="Domain Context",
                content=content_text,
                version=1,
            )
            doc_rows += result.get("promoted", 0)
        except Exception:
            logger.warning("Failed to migrate domain context", exc_info=True)

    # Lineage docs (JSON)
    lineage_dir = PROJECT_ROOT / "governance" / "lineage"
    if lineage_dir.exists():
        for path in sorted(lineage_dir.glob("*.json")):
            try:
                content_text = path.read_text()
                doc_files += 1
                result = write_document(
                    doc_type="lineage_doc",
                    doc_name=path.stem,
                    title=f"Lineage: {path.stem}",
                    content=content_text,
                    version=1,
                )
                doc_rows += result.get("promoted", 0)
            except Exception:
                logger.warning("Failed to migrate lineage doc from %s", path, exc_info=True)

    report["documents"] = {"files": doc_files, "rows": doc_rows}

    # Also run the existing sync_from_files for the original 8 tables
    existing_sync = sync_from_files()
    report["existing_sync"] = existing_sync

    return report


def cmd_migrate() -> None:
    """One-time migration of governance files to Iceberg tables."""
    print("Migrating governance file artifacts to Iceberg tables...")
    print("=" * 60)
    report = migrate_files_to_iceberg()

    print("\nMigration Report")
    print("=" * 60)
    for table, counts in sorted(report.items()):
        if table == "existing_sync":
            continue
        if isinstance(counts, dict) and "files" in counts:
            print(f"  {table:<25} {counts['files']:>3} files -> {counts['rows']:>4} rows")
    existing = report.get("existing_sync", {})
    if existing:
        print("\nExisting table sync:")
        for table, count in sorted(existing.items()):
            print(f"  {table:<25} {count:>4} records")

    total_files = sum(
        c.get("files", 0) for c in report.values() if isinstance(c, dict) and "files" in c
    )
    total_rows = sum(
        c.get("rows", 0) for c in report.values() if isinstance(c, dict) and "rows" in c
    )
    print(f"\nTotal: {total_files} files -> {total_rows} rows migrated")


# ---------------------------------------------------------------------------
# Export: regenerate file artifacts from Iceberg tables
# ---------------------------------------------------------------------------


def export_to_files() -> dict:
    """Compatibility wrapper for explicit governance exporters."""
    from brightsmith.infra.governance.exporters import export_to_files as _export_to_files

    return _export_to_files()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_status() -> None:
    """Print governance database summary."""
    summary = get_governance_summary()

    specs = summary.get("specs", [])
    dq = summary.get("dq_overall", {})
    gc = summary.get("governance_completeness", {})

    print("Governance Database Status")
    print("=" * 60)

    # DQ summary
    print(f"\nDQ Score: {dq.get('score_pct', 0):.1f}%")
    print(f"  Rules: {dq.get('rules_passing', 0)}/{dq.get('rules_total', 0)} passing")
    print(f"  P0 Gate: {'PASS' if dq.get('p0_passed', True) else 'FAIL'}")

    # Governance completeness
    total = gc.get("total_specs", 0)
    print(f"\nGovernance Completeness ({total} specs):")
    if total > 0:
        print(f"  DQ rules:       {gc.get('with_dq', 0)}/{total} ({gc.get('with_dq', 0)/total*100:.0f}%)")
        print(f"  Contracts:      {gc.get('with_contract', 0)}/{total} ({gc.get('with_contract', 0)/total*100:.0f}%)")
        print(f"  Lineage:        {gc.get('with_lineage', 0)}/{total} ({gc.get('with_lineage', 0)/total*100:.0f}%)")
        print(f"  Golden datasets:{gc.get('with_golden_dataset', 0)}/{total} ({gc.get('with_golden_dataset', 0)/total*100:.0f}%)")

    # Per-spec table
    if specs:
        print(f"\n{'Spec':<35} {'Zone':<8} {'Status':<12} {'DQ%':>6} {'Steps':>8}")
        print("-" * 75)
        for s in specs:
            dq_pct = f"{s.get('dq_score_pct', 0):.0f}%" if s.get("dq_rules_total") else "N/A"
            steps_done = s.get("pipeline_steps_completed") or 0
            steps_total = s.get("pipeline_steps_total") or 0
            steps_str = f"{steps_done}/{steps_total}" if steps_total else "N/A"
            print(f"{s.get('spec_name', '?'):<35} {s.get('zone', '?'):<8} {s.get('status', '?'):<12} {dq_pct:>6} {steps_str:>8}")

    # Open blockers
    blockers = summary.get("open_blockers", [])
    if blockers:
        print(f"\nOpen Blockers ({len(blockers)}):")
        for b in blockers[:5]:
            print(f"  [{b.get('agent_id')}] {b.get('spec_name')}: {b.get('summary', '')[:60]}")

    # Zone summary
    zones = summary.get("zones", {})
    if zones:
        print(f"\n{'Zone':<12} {'Specs':>6} {'Complete':>10}")
        print("-" * 30)
        for zone, data in sorted(zones.items()):
            print(f"{zone:<12} {data.get('specs', 0):>6} {data.get('complete', 0):>10}")


def cmd_sync() -> None:
    """Backfill governance tables from existing file artifacts."""
    print("Syncing governance tables from file artifacts...")
    counts = sync_from_files()
    print("\nSync complete:")
    for table, count in sorted(counts.items()):
        print(f"  {table}: {count} records synced")
    total = sum(counts.values())
    print(f"\nTotal: {total} records")


def cmd_export() -> None:
    """Regenerate file artifacts from governance tables."""
    print("Exporting file artifacts from governance tables...")
    counts = export_to_files()
    print("\nExport complete:")
    for artifact_type, count in sorted(counts.items()):
        print(f"  {artifact_type}: {count} generated")


def cmd_query(table_name: str) -> None:
    """Run an ad-hoc query against a governance table."""
    import duckdb

    if table_name not in _TABLE_CONFIGS:
        print(f"Unknown table: {table_name}")
        print(f"Available: {', '.join(sorted(_TABLE_CONFIGS.keys()))}")
        sys.exit(1)

    try:
        table = _get_governance_table(table_name)
        arrow_table = table.scan().to_arrow()
        if arrow_table.num_rows == 0:
            print(f"governance.{table_name}: 0 rows")
            return

        con = duckdb.connect()
        con.sql("SELECT * FROM arrow_table ORDER BY 1 DESC LIMIT 20").show()
        print(f"\n({arrow_table.num_rows} total rows)")
    except Exception as e:
        print(f"Error querying governance.{table_name}: {e}")
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="governance_db",
        description="Brightsmith Governance Admin Database",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Governance database summary")
    subparsers.add_parser("sync", help="Backfill from existing file artifacts")
    subparsers.add_parser("migrate", help="One-time migration of files to Iceberg")
    subparsers.add_parser("export", help="Regenerate file artifacts from tables")

    query_parser = subparsers.add_parser("query", help="Query a governance table")
    query_parser.add_argument("table", help=f"Table name: {', '.join(sorted(_TABLE_CONFIGS.keys()))}")

    args = parser.parse_args()

    commands = {
        "status": cmd_status,
        "sync": cmd_sync,
        "migrate": cmd_migrate,
        "export": cmd_export,
        "query": lambda: cmd_query(args.table),
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
