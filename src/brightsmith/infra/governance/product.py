"""Product/runtime governance state stored in Iceberg — re-export facade.

All implementation has moved to the submodules below. This file exists so that
every existing ``from brightsmith.infra.governance.product import X`` import
continues to work unchanged.

    schemas.py  — Iceberg schemas + _TABLE_CONFIGS + _GRAIN_PREFIXES
    queries.py  — table-access helpers + GovernanceReadError + get_* functions
    writers.py  — write_* functions
    sync.py     — sync_from_files, migrate_files_to_iceberg, Mermaid parser
    cli.py      — CLI commands + main()

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

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
from brightsmith.infra.governance.schemas import (  # noqa: F401
    AGENT_ACTIVITY_SCHEMA,
    CAB_DECISIONS_SCHEMA,
    CHAOS_MANIFESTS_SCHEMA,
    CONTRACT_COLUMNS_SCHEMA,
    CONTRACT_METADATA_SCHEMA,
    DATA_DICTIONARY_SCHEMA,
    DOCUMENTS_SCHEMA,
    DQ_ACKNOWLEDGMENTS_SCHEMA,
    DQ_RULE_RESULTS_SCHEMA,
    DQ_RULES_SCHEMA,
    DQ_RUNS_SCHEMA,
    GLOSSARY_TERMS_SCHEMA,
    GOLDEN_DATASETS_SCHEMA,
    MODEL_COLUMNS_SCHEMA,
    MODEL_ENTITIES_SCHEMA,
    MODEL_RELATIONSHIPS_SCHEMA,
    PIPELINE_EVENTS_SCHEMA,
    POLICIES_SCHEMA,
    RUN_HISTORY_SCHEMA,
    SPEC_REGISTRY_SCHEMA,
    _GRAIN_PREFIXES,
    _TABLE_CONFIGS,
)

# ---------------------------------------------------------------------------
# Query infrastructure + read API
# ---------------------------------------------------------------------------
from brightsmith.infra.governance.queries import (  # noqa: F401
    GovernanceReadError,
    _get_governance_table,
    _query_table,
    _write_records,
    get_agent_activity,
    get_cab_decisions,
    get_chaos_manifest,
    get_contract_columns,
    get_contracts,
    get_current_specs,
    get_data_dictionary,
    get_document,
    get_documents_by_type,
    get_dq_acknowledgments,
    get_dq_rule_results,
    get_dq_rules,
    get_dq_runs,
    get_golden_dataset,
    get_governance_summary,
    get_latest_dq_run,
    get_model_columns,
    get_model_entities,
    get_model_relationships,
    get_pipeline_events,
    get_policies,
    get_run_history,
    get_scorecard_data,
)

# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------
from brightsmith.infra.governance.model_writers import (  # noqa: F401
    write_model_columns,
    write_model_entity,
    write_model_relationships,
    write_policy,
)
from brightsmith.infra.governance.writers import (  # noqa: F401
    log_agent_finding,
    sync_contract,
    sync_glossary_term,
    write_agent_activity,
    write_cab_decision,
    write_chaos_manifest,
    write_data_dictionary,
    write_dq_acknowledgment,
    write_dq_rule_results,
    write_dq_rules,
    write_dq_run,
    write_document,
    write_golden_dataset_values,
    write_pipeline_event,
    write_run_history,
    write_spec_registry,
)

# ---------------------------------------------------------------------------
# Sync / migration
# ---------------------------------------------------------------------------
from brightsmith.infra.governance.migration import (  # noqa: F401
    cmd_migrate,
    migrate_files_to_iceberg,
)
from brightsmith.infra.governance.parsers import (  # noqa: F401
    _parse_mermaid_columns,
    _parse_mermaid_erdiagram,
)
from brightsmith.infra.governance.sync import sync_from_files  # noqa: F401

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
from brightsmith.infra.governance.cli import (  # noqa: F401
    cmd_export,
    cmd_query,
    cmd_status,
    cmd_sync,
    main,
)

# ---------------------------------------------------------------------------
# Export compatibility wrapper (delegates to exporters)
# ---------------------------------------------------------------------------
from brightsmith.infra.governance.exporters import export_to_files  # noqa: F401
