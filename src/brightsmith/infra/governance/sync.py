"""File-sync for governance tables (backfill from existing file artifacts)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Literal, overload

from brightsmith.infra.governance.model_writers import (
    write_model_columns,
    write_model_entity,
    write_model_relationships,
    write_policy,
)
from brightsmith.infra.governance.parsers import _parse_mermaid_erdiagram
from brightsmith.infra.governance.queries import _query_table
from brightsmith.infra.governance.writers import (
    sync_contract,
    sync_glossary_term,
    write_data_dictionary,
    write_document,
    write_dq_rule_results,
    write_dq_run,
    write_pipeline_event,
    write_spec_registry,
)

logger = logging.getLogger(__name__)


def _last_known_status(spec_name: str) -> str | None:
    """Look up the spec's most recent status, ignoring rows sync itself wrote.

    The pipeline-state JSON schema never persists a top-level status field, so
    a sync run has nothing authoritative to read when a state file carries
    none. Defaulting blindly to IN_PROGRESS overwrites a terminal status (e.g.
    COMPLETE) a human/agent wrote directly via write_spec_registry. Excluding
    sync-authored rows from the lookup means a re-sync always finds that true
    status instead of reading back its own earlier default — so once fixed,
    sync heals rather than re-clobbering on every run.
    """
    rows = _query_table("spec_registry", """
        SELECT status FROM arrow_table
        WHERE spec_name = $1 AND updated_by != 'sync'
        ORDER BY updated_at DESC
        LIMIT 1
    """, [spec_name])
    return rows[0]["status"] if rows else None


@overload
def _last_known_value(spec_name: str, field: Literal["zone"]) -> str | None: ...


@overload
def _last_known_value(spec_name: str, field: Literal["output_tables"]) -> list[str] | None: ...


def _last_known_value(
    spec_name: str,
    field: Literal["zone", "output_tables"],
) -> str | list[str] | None:
    """Look up the spec's most recent non-empty ``zone`` or ``output_tables``.

    Deliberately a different doctrine from ``_last_known_status`` above. A
    guessed status (``IN_PROGRESS``) is itself a plausible real status, so the
    only way to tell a guess from the truth is by writer identity (exclude
    rows sync wrote). A guessed zone/output_tables, by contrast, is *always*
    written as the empty value (``""`` / ``[]``) at every clobber site this
    project has -- no writer ever has a legitimate reason to overwrite a known
    zone or known table list with nothing. So the safe rule here is simpler
    and writer-agnostic: find the latest row where the field isn't empty, no
    matter who wrote it. A writer with a genuine new non-empty value is never
    routed through this helper at all -- callers only fall back to it when
    their own write has nothing to say, so a real update still wins outright.
    """
    empty_literal = "[]" if field == "output_tables" else ""
    rows = _query_table("spec_registry", f"""
        SELECT {field} FROM arrow_table
        WHERE spec_name = $1 AND {field} != $2
        ORDER BY updated_at DESC
        LIMIT 1
    """, [spec_name, empty_literal])
    if not rows:
        return None
    value = rows[0][field]
    return json.loads(value) if field == "output_tables" else value


def _string_value(value: object, fallback: str = "") -> str:
    """Return a string artifact value, falling back for malformed JSON data."""
    return value if isinstance(value, str) and value else fallback


def _string_list_value(value: object, fallback: list[str] | None = None) -> list[str]:
    """Return a validated string list, falling back for malformed JSON data."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return fallback or []


# ---------------------------------------------------------------------------
# Sync: backfill from existing file artifacts
#
# ``sync_from_files`` is an orchestrator over one ``_sync_*`` helper per source
# artifact type. Each helper reads its own directory, writes its own governance
# table(s) via idempotent promote, and returns its slice of the counts dict.
# Adding a new source = add a helper + one line in the orchestrator tuple.
# ---------------------------------------------------------------------------


def sync_from_files() -> dict:
    """Backfill governance tables from existing file artifacts.

    Idempotent via promote() — safe to run repeatedly.

    Returns counts of records synced per table.
    """
    counts: dict[str, int] = {}
    for step in (
        _sync_dq_results,
        _sync_pipeline_state,
        _sync_contracts,
        _sync_glossary,
        _enrich_spec_registry,
        _sync_data_dictionary,
        _sync_data_models,
        _sync_policies,
        _sync_domain_context,
    ):
        counts.update(step())
    return counts


# 1. DQ results -> dq_runs + dq_rule_results
def _sync_dq_results() -> dict:
    from brightsmith.config import DQ_RESULTS_DIR, DQ_RULES_DIR, PROJECT_ROOT

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
                executed_at = datetime.fromisoformat(executed_at_str) if executed_at_str else datetime.now(UTC)

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

                # p0_passed can be bool or string like "PASSED". A file that
                # does not say is NOT a pass: the runner never writes these
                # files, so whatever is here was written by hand or by another
                # tool, and silence used to read as "P0 PASS" (audit §3 P0).
                p0_raw = data.get("p0_passed") or data.get("p0_gate")
                if isinstance(p0_raw, str):
                    p0_passed = p0_raw.upper() in ("PASSED", "PASS", "TRUE")
                else:
                    p0_passed = bool(p0_raw)

                result = write_dq_run(
                    run_id=run_id, spec_name=spec, table_name=table_name,
                    executed_at=executed_at, rules_total=total, rules_passed=passed,
                    rules_failed=failed, rules_errored=errored, score_pct=score,
                    p0_passed=p0_passed,
                    result_file_path=str(path.relative_to(PROJECT_ROOT)),
                    mode="imported",
                )
                dq_synced += result.get("promoted", 0)

                # Individual rule results
                rule_results = write_dq_rule_results(run_id, spec, rules, mode="imported")
                dq_rules_synced += rule_results.get("promoted", 0)
            except Exception:
                logger.warning("Failed to sync DQ results from %s", path, exc_info=True)

    return {"dq_runs": dq_synced, "dq_rule_results": dq_rules_synced}


# 2. Pipeline state -> pipeline_events + spec_registry
def _sync_pipeline_state() -> dict:
    from brightsmith.config import PIPELINE_STATE_DIR

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
                        event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(UTC)
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
                    event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(UTC)
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
                    event_time = datetime.fromisoformat(event_time_str) if event_time_str else datetime.now(UTC)
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
                spec_status = data.get("status") or _last_known_status(spec) or "IN_PROGRESS"
                zone = _string_value(zone, _last_known_value(spec, "zone") or "")
                output_tables = _string_list_value(
                    data.get("output_tables"),
                    _last_known_value(spec, "output_tables"),
                )

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

    return {"pipeline_events": pipeline_synced, "spec_registry": registry_synced}


# 3. Contracts -> contract_metadata
def _sync_contracts() -> dict:
    import yaml

    from brightsmith.config import PROJECT_ROOT

    contract_synced = 0
    contracts_dir = PROJECT_ROOT / "governance" / "data-contracts"
    if contracts_dir.exists():
        for path in sorted(contracts_dir.glob("*.yaml")):
            try:
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
    return {"contract_metadata": contract_synced}


# 4. Glossary -> glossary_terms
def _sync_glossary() -> dict:
    from brightsmith.config import PROJECT_ROOT

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
    return {"glossary_terms": glossary_synced}


# 5. Enrich spec_registry with DQ scores and governance completeness flags
#    by cross-referencing the data we just synced
def _enrich_spec_registry() -> dict:
    from brightsmith.config import PIPELINE_STATE_DIR, PROJECT_ROOT
    from brightsmith.infra.governance.queries import get_latest_dq_run

    enriched = 0
    if PIPELINE_STATE_DIR.exists():
        for path in sorted(PIPELINE_STATE_DIR.glob("*-pipeline.json")):
            try:
                data = json.loads(path.read_text())
                spec = data.get("spec", path.stem.replace("-pipeline", ""))
                zone = _string_value(data.get("zone"), _last_known_value(spec, "zone") or "")
                output_tables = _string_list_value(
                    data.get("output_tables"),
                    _last_known_value(spec, "output_tables"),
                )

                # DQ scores from the latest REAL run in the governance DB.
                # Result files on disk are imports (hand-written or from another
                # tool) and never set the spec's score or P0 status: a file with
                # failing rules and no p0_passed field used to land here as a
                # P0 PASS (2026-09-08 audit §3; governance review of Phase 0).
                dq_kwargs: dict = {}
                latest = get_latest_dq_run(spec)
                if latest:
                    dq_kwargs = {
                        "dq_score_pct": latest["score_pct"],
                        "dq_rules_total": latest["rules_total"],
                        "dq_rules_passing": latest["rules_passed"],
                        "dq_rules_failing": latest["rules_failed"],
                        "dq_p0_passed": latest["p0_passed"],
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
                    status=data.get("status") or _last_known_status(spec) or "IN_PROGRESS",
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
    return {"spec_registry_enriched": enriched}


# 6. Data Dictionary backfill -> data_dictionary
def _sync_data_dictionary() -> dict:
    from brightsmith.config import PROJECT_ROOT

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
    return {"data_dictionary": dict_synced}


# 7. Data Models backfill -> model_entities, model_columns, model_relationships
def _sync_data_models() -> dict:
    from brightsmith.config import PROJECT_ROOT

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
    return {
        "model_entities": entities_synced,
        "model_columns": columns_synced,
        "model_relationships": rels_synced,
    }


# 8. Policies backfill -> policies
def _sync_policies() -> dict:
    from brightsmith.config import PROJECT_ROOT

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
    return {"policies": policies_synced}


# 9. Domain Context backfill -> documents (doc_type="domain_context")
def _sync_domain_context() -> dict:
    from brightsmith.config import PROJECT_ROOT

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
    return {"domain_context": domain_context_synced}
