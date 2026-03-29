# Spec: Governance Admin Database

**Status:** NOT STARTED
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-29

## Problem Statement

Brightsmith was built for terminal-based, human-at-keyboard workflows. All governance artifacts (DQ results, scorecards, pipeline state, contracts, etc.) are written as loose files — JSON, YAML, and markdown — under `governance/`. This worked fine when a human was reviewing files directly.

Now that Brightforge (the UI) exists, this file-based approach causes three problems:

1. **No spec-to-table registry** — the mapping between spec names and table names is implicit and scattered across files with inconsistent naming conventions. Some artifacts are keyed by spec name (DQ rules, scorecards, pipeline state), others by table name (contracts), and others by either (lineage events). No single place maps spec → table(s).

2. **No machine-readable aggregation** — DQ scorecards are markdown-only. Governance completeness requires stitching across naming schemes. The API returns `passingRules: 61` and `failingRules: 5` but `totalRules: 0` because the aggregation fields aren't computed in a queryable format.

3. **No temporal history** — most artifacts are overwritten in place, losing run-over-run DQ trends and pipeline execution timelines.

4. **No structured agent feedback** — agent findings, decisions, and recommendations live in session logs and audit trail markdown. Brightforge's design system has a dedicated style for agent citations, but there's no stable, queryable backing store for that component.

The result: Brightforge shows 0% DQ and 0% Governance Completeness despite real governance data existing, because it can't reliably correlate artifacts across naming conventions.

## Solution

Add 7 Iceberg tables in the existing `governance` namespace that governance agents write to alongside their current file outputs. Brightforge queries Iceberg instead of parsing files. Files remain for git versioning and human review but are no longer the source of truth for the UI.

## Design Decisions

### 1. Iceberg-primary, files as generated views

Agents write structured records to governance Iceberg tables via the existing `promote()` pattern. A CLI command (`python -m brightsmith.infra.governance_db export`) regenerates file artifacts from the tables for git versioning and human review. The tables are the source of truth for UI consumption.

Exception: data contracts (YAML) remain file-primary since they are human-authored. A metadata row is synced to Iceberg for queryability.

### 2. Spec registry as the central hub

`governance.spec_registry` is the authoritative mapping of spec_name to table_name(s), zone, pipeline status, DQ scores, and governance completeness flags. Every other governance table references `spec_name` as the join key. Brightforge queries spec_registry first, then joins to DQ/pipeline/contract/activity tables. This eliminates the filename-guessing problem entirely.

### 3. Append-only with "latest row wins"

No updates — Iceberg append-only is simpler and more reliable. Mutable state (like spec status or DQ scores) uses an `updated_at` timestamp; queries use `ROW_NUMBER() OVER (PARTITION BY key ORDER BY updated_at DESC) = 1` to get current state. This gives free history/audit trail from the append-only design.

Idempotent via `compute_grain_id()` on grain fields — re-runs with the same data produce the same hash, and `promote()` skips duplicates.

### 4. Bitemporal where it matters

- **Transaction time**: Iceberg snapshots (free time travel via snapshot metadata)
- **Valid time**: Explicit `executed_at` / `event_time` / `updated_at` columns depending on the table
- DQ runs are naturally bitemporal: `executed_at` = when rules ran, Iceberg snapshot = when row was written
- Pipeline events: `event_time` = when the step happened
- Spec registry: `updated_at` = when state changed (new row appended per state change)

### 5. Agent activity as a first-class table

Every agent that produces findings, decisions, or recommendations writes structured records to `governance.agent_activity`. This backs the agent citation component in Brightforge's design system — the `summary` field appears in the citation bubble, `detail` is the expandable content, and `severity` controls the visual treatment.

## Table Schemas

All tables live in the existing `governance` namespace at `data/governance/iceberg_warehouse/`.

### Table 1: `governance.spec_registry`

The hub table. One row per spec state change (latest row = current state).

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| spec_name | string | yes | e.g., `raw-sec-edgar-ingest` |
| zone | string | yes | `raw`, `base`, `consumable`, `ai_ready` |
| status | string | yes | `IN_PROGRESS`, `COMPLETE`, `BLOCKED`, `FAILED` |
| output_tables | string | yes | JSON array of `namespace.table` names |
| dq_score_pct | float | no | Latest DQ pass rate (0-100) |
| dq_rules_total | int | no | Total rules for this spec |
| dq_rules_passing | int | no | Passing rules |
| dq_rules_failing | int | no | Failing rules |
| dq_p0_passed | boolean | no | P0 gate status |
| has_contract | boolean | no | Any table has active contract |
| has_lineage | boolean | no | Lineage events exist |
| has_golden_dataset | boolean | no | Golden dataset exists |
| has_data_dictionary | boolean | no | Dictionary entries exist |
| has_cde_tags | boolean | no | CDE/PII tags assigned |
| pipeline_step_current | string | no | Current pipeline step name |
| pipeline_steps_total | int | no | Total steps in pipeline |
| pipeline_steps_completed | int | no | Completed steps |
| spec_file_path | string | no | Path to spec markdown |
| updated_at | timestamptz | yes | When this row was written |
| updated_by | string | yes | Agent or system that wrote it |

**Grain fields:** `[spec_name, status, updated_at]`

### Table 2: `governance.dq_runs`

One row per DQ execution run. Replaces `governance/dq-results/{spec}-{timestamp}.json` as the source of truth for the UI.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| run_id | string | yes | Unique run identifier |
| spec_name | string | yes | FK to spec_registry |
| table_name | string | yes | `namespace.table` being tested |
| executed_at | timestamptz | yes | When the run happened |
| rules_total | int | yes | Total rules executed |
| rules_passed | int | yes | Passing count |
| rules_failed | int | yes | Failing count |
| rules_errored | int | yes | Error count |
| rules_warning | int | yes | Warning count |
| score_pct | float | yes | `passed / total * 100` |
| p0_passed | boolean | yes | P0 gate result |
| p0_total | int | no | Count of P0 rules |
| p0_failed | int | no | Count of P0 failures |
| p1_total | int | no | Count of P1 rules |
| p1_failed | int | no | Count of P1 failures |
| duration_ms | int | no | Total execution time |
| result_file_path | string | no | Path to JSON results file |
| updated_at | timestamptz | yes | When row was written |

**Grain fields:** `[run_id]`

### Table 3: `governance.dq_rule_results`

Individual rule outcomes per run.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| run_id | string | yes | FK to dq_runs |
| spec_name | string | yes | FK to spec_registry |
| rule_id | string | yes | e.g., `RAW-EDGAR-001` |
| category | string | yes | Completeness, Validity, etc. |
| priority | string | yes | P0, P1, P2, P3 |
| description | string | yes | Human-readable rule description |
| passed | boolean | yes | Pass/fail |
| raw_value | string | no | Actual query result |
| threshold | string | no | Expected threshold expression |
| violations | int | no | Count of violations |
| execution_time_ms | int | no | Per-rule execution time |
| error_message | string | no | Error if rule errored |
| executed_at | timestamptz | yes | When rule was evaluated |

**Grain fields:** `[run_id, rule_id]`

### Table 4: `governance.pipeline_events`

Append-only log of pipeline step executions.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| spec_name | string | yes | FK to spec_registry |
| step_name | string | yes | e.g., `governance-reviewer-pre`, `dq-engineer` |
| event_type | string | yes | `STARTED`, `COMPLETED`, `SKIPPED`, `FAILED`, `APPROVED` |
| agent_id | string | no | `@governance-reviewer`, `@dq-engineer`, etc. |
| output_path | string | no | Path to artifact produced |
| skip_reason | string | no | Justification if skipped |
| approval_decision | string | no | `APPROVED`, `CHANGES_REQUESTED` if approval event |
| approval_by | string | no | `human:jeff`, `auto` |
| notes | string | no | Free-text context |
| event_time | timestamptz | yes | When event occurred |

**Grain fields:** `[spec_name, step_name, event_type, event_time]`

### Table 5: `governance.contract_metadata`

Synced from YAML contract files. One row per contract version.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| contract_name | string | yes | e.g., `company-financials` |
| spec_name | string | no | Spec that produced the table |
| table_name | string | yes | `namespace.table` |
| zone | string | yes | Derived from namespace |
| version | string | yes | Semantic version `1.0.0` |
| status | string | yes | `DRAFT`, `ACTIVE`, `DEPRECATED` |
| column_count | int | no | Number of columns |
| grain_columns | string | no | JSON array of grain column names |
| has_dq_rules | boolean | no | DQ rules referenced |
| has_golden_dataset | boolean | no | Golden dataset referenced |
| freshness_sla_hours | int | no | Max staleness SLA |
| contract_file_path | string | yes | Path to YAML file |
| updated_at | timestamptz | yes | When synced |

**Grain fields:** `[contract_name, version]`

### Table 6: `governance.glossary_terms`

Synced from `governance/business-glossary.json`. One row per term version.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| term_id | string | yes | e.g., `BT-001` |
| term | string | yes | Display name |
| definition | string | yes | Plain-English definition |
| category | string | yes | entity, measurement, etc. |
| source | string | yes | domain-standard, project-specific |
| approval_status | string | yes | proposed, approved, auto-approved |
| used_in_specs | string | no | JSON array of spec names |
| updated_at | timestamptz | yes | When synced |

**Grain fields:** `[term_id, updated_at]`

### Table 7: `governance.agent_activity`

Structured agent feedback for UI display. Backs the agent citation component in Brightforge's design system.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| spec_name | string | yes | FK to spec_registry |
| agent_id | string | yes | `@dq-engineer`, `@staff-engineer`, `@governance-reviewer`, etc. |
| pipeline_step | string | no | Pipeline step context (e.g., `dq-engineer`, `governance-reviewer-post`) |
| activity_type | string | yes | `finding`, `decision`, `recommendation`, `warning`, `blocker`, `approval`, `rejection`, `skip_justification` |
| severity | string | yes | `info`, `warning`, `blocker` |
| summary | string | yes | Short display text (agent citation bubble content) |
| detail | string | no | Full reasoning/explanation (expandable in UI) |
| references | string | no | JSON array of referenced artifacts |
| related_table | string | no | `namespace.table` if activity relates to a specific table |
| related_rule_id | string | no | DQ rule ID if activity relates to a specific rule |
| resolution_status | string | no | `open`, `resolved`, `accepted`, `wont_fix` |
| resolved_by | string | no | Agent or human that resolved it |
| resolved_at | timestamptz | no | When resolved |
| event_time | timestamptz | yes | When the agent produced this |

**Grain fields:** `[spec_name, agent_id, activity_type, summary, event_time]`

**Activity types:**
- `finding` — Observation (e.g., "3.2% of CIK values are null")
- `decision` — Judgment call (e.g., "Setting P1 threshold at 99% based on EDA evidence")
- `recommendation` — Future work suggestion (e.g., "Consider adding sector-level aggregation")
- `warning` — Non-blocking concern (e.g., "Lineage events exist but missing column-level detail")
- `blocker` — Blocks spec completion (e.g., "P0 DQ failure on grain uniqueness")
- `approval` — Explicit approval (e.g., "Code quality meets standards, approved")
- `rejection` — Sent back for rework (e.g., "CHANGES REQUESTED — test count below minimum")
- `skip_justification` — Why a pipeline step was skipped (e.g., "No PII per domain-context.md section 4")

**UI mapping:** `summary` → citation bubble text. `detail` → expandable content. `severity` → visual treatment (info=neutral, warning=amber, blocker=red).

### Existing table (no changes): `governance.lineage_events`

Already in Iceberg with `spec_reference` and `output_table` columns. No modifications needed.

## Implementation

### Step 1: Create `src/brightsmith/infra/governance_db.py`

New module following the `lineage.py` pattern:
- 7 PyIceberg schema definitions
- `get_governance_catalog()` — catalog accessor for governance warehouse
- `get_or_create_governance_table(name, schema)` — lazy table accessor
- Write functions: `write_spec_registry()`, `write_dq_run()`, `write_dq_rule_results()`, `write_pipeline_event()`, `sync_contract()`, `sync_glossary_term()`, `write_agent_activity()`
- Query functions: `get_current_specs()`, `get_dq_runs()`, `get_latest_dq_run()`, `get_pipeline_events()`, `get_contracts()`, `get_agent_activity()`, `get_governance_summary()`
- Convenience wrapper: `log_agent_finding(spec, agent, summary, detail, severity, **kwargs)`
- All writes via `promote()` for idempotency

Reuses: `iceberg_setup.get_catalog()`, `iceberg_setup.get_or_create_table()`, `promote.promote()`, `grain.compute_grain_id()`

### Step 2: Move `GOVERNANCE_WAREHOUSE` to `config.py`

Currently hardcoded in `lineage.py` line 44. Move to `config.py` alongside other path constants, update `configure()` rebuild logic, update `lineage.py` to import from config.

### Step 3: Instrument DQ runner (`dq_runner.py`)

After `run_rules()` writes JSON results to `governance/dq-results/`:
1. Call `write_dq_run()` with aggregate counts
2. Call `write_dq_rule_results()` with individual rule outcomes
3. Call `write_spec_registry()` to update DQ score fields

Dual-write: files for git/human review, Iceberg for UI.

### Step 4: Instrument pipeline gate (`pipeline_gate.py`)

After each `complete()`, `skip()`, or `approve()`:
1. Call `write_pipeline_event()` with step status change
2. Call `write_spec_registry()` to update pipeline progress

On `init()` (first `check()` or explicit initialization):
1. Call `write_spec_registry()` with initial state and output_tables mapping

### Step 5: Instrument contract module (`contract.py`)

After `generate()` or `verify()`:
1. Call `sync_contract()` to write/update contract metadata row

### Step 6: Instrument agents for activity logging

Agent definitions (`.claude/agents/*.md`) include instructions to call `log_agent_finding()` at key decision points. The function is fault-tolerant — if the write fails, the agent continues and logs a warning.

| Agent | Activity Types |
|-------|---------------|
| @data-analyst | `finding` |
| @dq-rule-writer | `decision` |
| @dq-engineer | `finding`, `warning`, `blocker` |
| @governance-reviewer | `finding`, `warning`, `blocker`, `approval` |
| @staff-engineer | `approval`, `rejection`, `blocker`, `warning` |
| @semantic-modeler | `decision` |
| @data-steward | `decision`, `recommendation` |
| @lineage-tracker | `finding`, `warning` |
| @cde-tagger | `decision` |
| @cab-agent | `decision`, `warning`, `blocker` |
| @chaos-monkey | `finding` |
| @insight-manager | `recommendation` |
| @principal-data-architect | `finding`, `recommendation`, `blocker` |

### Step 7: CLI commands

`python -m brightsmith.infra.governance_db`:
- `status` — Governance database summary (all specs, DQ scores, completeness)
- `sync` — Backfill from existing file artifacts into Iceberg tables
- `export` — Regenerate file artifacts from Iceberg tables
- `query <table>` — Ad-hoc DuckDB query against governance tables

### Step 8: Governance summary query

`get_governance_summary()` returns everything Brightforge needs for the dashboard in a single call — DQ score aggregation, governance completeness percentages, pipeline progress per spec, zone-level rollups. This replaces the broken `GovernanceStore.get_dq_scorecards()` and `/api/analytics/governance-completeness` logic in Brightforge.

## What Brightforge Gets

After implementation, Brightforge replaces file-parsing with SQL queries:

```sql
-- Dashboard: all specs with current state
SELECT * FROM governance.spec_registry
WHERE (spec_name, updated_at) IN (
    SELECT spec_name, MAX(updated_at) FROM governance.spec_registry GROUP BY spec_name
);

-- DQ scorecard: latest run with all rule results
SELECT r.*, rr.rule_id, rr.category, rr.passed, rr.priority
FROM governance.dq_runs r
JOIN governance.dq_rule_results rr ON r.run_id = rr.run_id
WHERE r.spec_name = ? ORDER BY r.executed_at DESC;

-- Governance completeness by zone
SELECT zone, COUNT(*) as total_specs,
    SUM(CASE WHEN has_contract THEN 1 ELSE 0 END) as with_contract,
    SUM(CASE WHEN dq_rules_total > 0 THEN 1 ELSE 0 END) as with_dq,
    SUM(CASE WHEN has_lineage THEN 1 ELSE 0 END) as with_lineage
FROM (SELECT DISTINCT ON (spec_name) * FROM governance.spec_registry
      ORDER BY spec_name, updated_at DESC)
GROUP BY zone;

-- Agent activity feed (backs agent citation component)
SELECT agent_id, activity_type, severity, summary, detail, event_time
FROM governance.agent_activity WHERE spec_name = ? ORDER BY event_time DESC;

-- Open blockers across all specs
SELECT spec_name, agent_id, summary, detail, event_time
FROM governance.agent_activity
WHERE severity = 'blocker' AND (resolution_status IS NULL OR resolution_status = 'open');

-- Agent activity summary per spec
SELECT agent_id,
    COUNT(*) FILTER (WHERE activity_type = 'finding') as findings,
    COUNT(*) FILTER (WHERE activity_type = 'warning') as warnings,
    COUNT(*) FILTER (WHERE activity_type = 'blocker') as blockers,
    COUNT(*) FILTER (WHERE activity_type = 'approval') as approvals,
    MAX(event_time) as last_activity
FROM governance.agent_activity WHERE spec_name = ? GROUP BY agent_id;
```

## Design Constraint: Orchestrator-Agnostic Write API

Today, Brightsmith agents are orchestrated by Claude Code — agent definitions in `.claude/agents/*.md` encode the pipeline workflow, and Claude Code invokes them in sequence. But the long-term direction is a web application backend (Brightforge or a dedicated orchestration service) driving agent execution directly, with Claude Code as one possible orchestration option rather than the only one.

This spec is a deliberate step toward that decoupling. The governance database write API (`write_dq_run()`, `write_pipeline_event()`, `log_agent_finding()`, etc.) must be designed so that **the caller's identity doesn't matter**. Whether a DQ run is triggered by Claude Code invoking `@dq-engineer`, a web backend calling `dq_runner.run_rules()` via API, or a cron job running `python -m brightsmith.run` — the same Python functions write the same structured records to the same Iceberg tables.

**Principles for this spec:**

1. **No Claude Code assumptions in the write path.** The governance_db module must not import from or reference Claude Code, agent definitions, or session logs. It's a pure Python library that any caller can use.
2. **Agent identity is a string, not a coupling point.** The `agent_id` field in `agent_activity` and `pipeline_events` is a freeform string (`@dq-engineer`, `web-backend`, `cron-runner`). It identifies who acted, not how they were invoked.
3. **Pipeline state is queryable, not file-locked.** Moving state from JSON files to Iceberg means a web backend can read pipeline progress without filesystem access to the project directory — it just needs DuckDB access to the catalog.
4. **Write functions accept data, not context.** Functions take explicit parameters (`spec_name`, `agent_id`, `summary`) rather than inferring context from the runtime environment.

**What this spec does NOT do** (future spec territory):

- Extract pipeline orchestration logic (step ordering, dependency resolution, retry) from CLAUDE.md into a programmatic workflow engine
- Define a REST/gRPC API surface for remote orchestration
- Build an abstraction layer between Brightsmith, Brightforge, and the orchestrator
- Handle multi-user concurrency or distributed locking on pipeline state

Those are the next layer. This spec gives them a foundation by ensuring that the governance state layer is already clean, structured, and orchestrator-agnostic.

## Files Modified

| File | Action |
|------|--------|
| `src/brightsmith/infra/governance_db.py` | **CREATE** |
| `src/brightsmith/config.py` | **MODIFY** — Add `GOVERNANCE_WAREHOUSE` |
| `src/brightsmith/infra/lineage.py` | **MODIFY** — Import warehouse path from config |
| `src/brightsmith/infra/dq_runner.py` | **MODIFY** — Dual-write to governance DB |
| `src/brightsmith/infra/pipeline_gate.py` | **MODIFY** — Event writes on state transitions |
| `src/brightsmith/infra/contract.py` | **MODIFY** — Contract metadata sync |
| `src/brightsmith/infra/dq_scorecard.py` | **MODIFY** — Generate from Iceberg data |
| `.claude/agents/*.md` | **MODIFY** — Add `log_agent_finding()` instructions |

## Verification

1. **Unit tests**: Write governance records, query back, verify structure and grain dedup
2. **Integration test**: Run DQ rules, verify both JSON file and Iceberg row created with matching data
3. **Sync test**: Populate governance files manually, run `sync`, verify Iceberg tables populated
4. **Query test**: Run `get_governance_summary()`, verify aggregation matches manual artifact count
5. **Idempotency test**: Run same pipeline twice, verify no duplicate rows
6. **Agent activity test**: Write findings, query back, verify correct spec_name/agent_id/severity
7. **Activity feed test**: Multiple agents write activities, verify chronological ordering
8. **Brightforge smoke test**: Point Brightforge at project with governance DB, verify non-zero DQ% and completeness%, verify agent citations render
