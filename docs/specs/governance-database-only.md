# Spec: Governance Database Only (Parent Design Document)

**Status:** DRAFT
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-29

This is the parent design document for the governance database migration. It is implemented as three sub-specs, each independently testable and reviewable:

- **Spec A:** `governance-db-expansion.md` — New tables, schema evolution, migration function
- **Spec B:** `governance-module-migration.md` — Switch all module readers/writers to Iceberg
- **Spec C:** `governance-docs-cleanup.md` — CLAUDE.md, agent definitions, setup.py, config cleanup

## Problem Statement

Brightsmith currently stores governance artifacts in two places: Iceberg tables (via `governance_db.py`) and loose files (JSON, YAML, Markdown) under `governance/`. This dual-write pattern means:

1. **Brightforge must parse files** — CDE catalogs, DQ rules, pipeline state, CAB decisions, golden datasets, and run history all require file I/O. The governance database was supposed to eliminate this.

2. **File and Iceberg can drift** — When both exist, which is authoritative? Today the files are authoritative and Iceberg is a sync mirror. This defeats the purpose of the database.

3. **Some artifacts are file-only** — CAB decisions, golden datasets, chaos manifests, run history, DQ scorecards, and approval records have no Iceberg representation at all.

4. **Markdown artifacts are invisible** — Reviews, insights, data models, and domain context are markdown files that Brightforge cannot query or display in structured views.

## Solution

Make Iceberg the sole source of truth for all governance data. Eliminate runtime file writes. Add new Iceberg tables for artifacts that are currently file-only. Enrich existing tables where schemas are incomplete.

After this spec, the only files in the project are:
- `docs/specs/` — human-authored design documents
- `docs/sessions/` — session logs for open source transparency
- `domain/manifest.yaml` — project bootstrap config
- `domain/sources/*.yaml` — source access configs
- `domain/concept-mappings/` — mapping configs
- `governance/business-glossary.json` — human-curated, git-diffable input (synced to Iceberg, file remains authoritative for human editing)
- `governance/concept-normalization/collision-rules.json` — human-curated config (same rationale as glossary)
- `governance/dq-rule-templates/` — reference templates (read-only)
- `src/` — source code
- `tests/` — test code

Everything under `governance/` that is **generated at runtime** moves to Iceberg. Human-curated inputs that benefit from git diffs stay as files but are synced to Iceberg for Brightforge reads.

## Design Decisions

### 1. Iceberg-authoritative, not file-authoritative

Today: files are written first, then synced to Iceberg. Readers parse files.
After: writers call `governance_db.py` functions directly. Readers query Iceberg. No runtime file writes.

### 2. Markdown content stored as text columns

Reviews, insights, domain context, EDA reports, and data models contain prose. These are stored as `StringType()` columns in Iceberg. Brightforge renders markdown from the text field. This is no different from storing a description column — just longer.

### 3. Scorecards are derived, not stored

DQ scorecards are generated from `dq_runs` + `dq_rule_results` + `dq_rules`. Brightforge generates the scorecard view on the fly via a join: `dq_rule_results JOIN dq_rules ON rule_id` for category/priority enrichment. No separate scorecard table needed.

### 4. Approval records folded into pipeline_events

The existing `governance.pipeline_events` table already has `approval_decision` and `approval_by` fields. Approval documents become a `content` text column on the event record rather than a separate table.

### 5. Two-phase migration with rollback

**Phase 1 (Spec A + B): Iceberg-authoritative, files still written.** All writers write to Iceberg first, then emit files as a backup. All readers switch to Iceberg queries. If Iceberg breaks, readers can fall back to files. Validate: migration report comparing file counts to Iceberg row counts.

**Phase 2 (Spec C): Remove file writes.** After Phase 1 is validated and stable, remove file writes. Archive `governance/` runtime directories.

This gives a natural rollback path: if Phase 1 reveals issues, readers can revert to file-based without data loss.

### 6. Human-curated files stay as files

`business-glossary.json` and `collision-rules.json` are human-edited, git-tracked inputs. Migrating them to Iceberg-only removes the ability to review changes in git diffs. These stay as files but are synced to Iceberg at the start of every pipeline run (automatic sync in `brightsmith.run` entry point). Modules that need glossary data at runtime (`contract.py`, `glossary_validator.py`) read the file directly — same as today. Brightforge reads the Iceberg mirror. This avoids staleness: the file is always current, Iceberg is refreshed on every run.

### 7. CLI fallback for catalog corruption

Critical CLI commands get a `--from-files` flag that reads archived files in emergency:
- `dq_runner status`
- `pipeline_gate status`
- `pipeline_gate validate`
- `pipeline_gate check-transition`
- `contract list`

This is a recovery path, not a runtime path. If the governance catalog is corrupted, the recovery procedure is: (1) `--from-files` to assess state, (2) `python -m brightsmith.infra.governance_db migrate` to rebuild catalog from archived files.

### 8. Acknowledgments as separate table

DQ failure acknowledgments are mutations on existing rows — incompatible with promote()'s append-only dedup. A separate `governance.dq_acknowledgments` table with its own grain keeps the append-only pattern clean. Acknowledgment = new row in `dq_acknowledgments`, not a modification of `dq_rule_results`.

## New Tables (7)

### `governance.dq_rules`

One row per rule version. Replaces `governance/dq-rules/*.json`.

Uses a `version` integer for append-only history. When a rule is created, version=1. When approved, a new row is written with version=2 and status=approved. Re-approving an already-approved rule at the same version is a no-op via promote(). The write function determines the next version by querying `MAX(version) WHERE spec_name = $1 AND rule_id = $2` and incrementing.

**Read pattern:** To get current rules for a spec, query:
```sql
SELECT * FROM governance.dq_rules
WHERE spec_name = $1
  AND (spec_name, rule_id, version) IN (
    SELECT spec_name, rule_id, MAX(version)
    FROM governance.dq_rules
    WHERE spec_name = $1
    GROUP BY spec_name, rule_id
  )
```

To get current rules for a specific table: add `AND table_name = $2` to both the outer and inner query.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| spec_name | string | yes | FK to spec_registry |
| table_name | string | yes | Target table |
| rule_id | string | yes | Unique rule identifier |
| category | string | yes | completeness, validity, uniqueness, etc. |
| priority | string | yes | P0, P1, P2, P3 |
| description | string | yes | Human-readable rule description |
| sql | string | yes | SQL expression to evaluate |
| threshold | string | yes | Pass/fail expression (e.g. "result = 0") |
| status | string | yes | proposed, approved, active |
| version | int | yes | Monotonically increasing version per rule |
| approved_by | string | no | Who approved the rule |
| approved_at | timestamptz | no | When approved |
| updated_at | timestamptz | yes | When written |

**Grain fields:** `[spec_name, rule_id, version]`

### `governance.dq_acknowledgments`

One row per acknowledged DQ failure. Separate from `dq_rule_results` to preserve append-only semantics.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| run_id | string | yes | FK to dq_runs.run_id |
| rule_id | string | yes | FK to dq_rule_results.rule_id |
| spec_name | string | yes | FK to spec_registry |
| acknowledged_by | string | yes | Who acknowledged |
| reason | string | yes | Why failure was acknowledged |
| acknowledged_at | timestamptz | yes | When acknowledged |

**Grain fields:** `[run_id, rule_id]`

Replaces `governance/dq-results/{spec}-ack-*.json` acknowledgment files. To check if a failure is acknowledged: `LEFT JOIN dq_acknowledgments ON run_id AND rule_id`.

### `governance.cab_decisions`

One row per CAB decision. Replaces `governance/cab-decisions/*.json` and `index.json`.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| decision_id | string | yes | Unique decision ID |
| spec_name | string | yes | FK to spec_registry |
| table_name | string | yes | Affected table |
| classification | string | yes | PATCH, MINOR, MAJOR |
| classification_reasons | string | yes | JSON array of change details |
| contract_version_before | string | no | Version before change |
| contract_version_after | string | no | Version after change |
| schema_diff | string | no | JSON object: added/removed/changed columns |
| blast_radius | string | no | JSON object: downstream tables, consumables, MCP tools |
| decision | string | yes | PENDING, APPROVED, APPROVED_WITH_FORK, REJECTED |
| decided_by | string | no | human:name or @agent |
| decided_at | timestamptz | no | When decided |
| notes | string | no | Decision notes |
| rationale | string | no | Decision rationale |
| fork_config | string | no | JSON: v1_table, v2_table, migration, deprecation timeline |
| human_override | string | no | JSON: action, original/override classification, rationale |
| created_at | timestamptz | yes | When created |

**Grain fields:** `[decision_id]`

### `governance.golden_datasets`

One row per golden value per spec. Replaces `governance/golden-datasets/*.json`.

**Note on grain determinism:** The `filters` field is JSON-serialized. To ensure idempotent grain hashes, the write function normalizes filters before grain computation: `json.dumps(filters, sort_keys=True, separators=(',', ':'))`. This is enforced in `write_golden_dataset_values()`, not in `compute_grain_id()` itself.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| spec_name | string | yes | FK to spec_registry |
| table_name | string | yes | Table being verified |
| value_description | string | yes | What this value represents |
| column_name | string | yes | Column containing the expected value |
| expected_value | string | yes | Expected value (string-encoded) |
| tolerance_pct | float | no | Relative tolerance (e.g. 0.01 = 1%) |
| tolerance_type | string | no | relative or absolute |
| filters | string | yes | JSON object of filter conditions (deterministic serialization) |
| last_verified_at | timestamptz | no | When last checked |
| last_verified_passed | boolean | no | Whether last check passed |
| updated_at | timestamptz | yes | When written |

**Grain fields:** `[spec_name, column_name, filters]`

### `governance.run_history`

One row per pipeline run. Replaces `governance/run-history/*.json`.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| run_id | string | yes | Unique run identifier |
| started_at | timestamptz | yes | Run start time |
| completed_at | timestamptz | no | Run end time |
| duration_seconds | float | no | Total duration |
| status | string | yes | SUCCESS, FAILED, DQ_FAILURE, etc. |
| zones_summary | string | yes | JSON: per-zone status, rows, DQ counts |
| golden_datasets_summary | string | no | JSON: checked, passed, failed, rate |
| options | string | no | JSON: zone filter, dry_run, validate_only |
| error_message | string | no | Top-level error if failed |
| updated_at | timestamptz | yes | When written |

**Grain fields:** `[run_id]`

### `governance.chaos_manifests`

One row per chaos monkey run. Replaces `governance/chaos-monkey/*.json`.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| run_id | string | yes | Chaos run identifier |
| source_table | string | yes | Original table |
| shadow_table | string | yes | Shadow copy table |
| total_rows | int | yes | Rows in source |
| corruption_rate | float | yes | Target corruption rate |
| seed | int | no | Random seed for reproducibility |
| rows_corrupted | int | yes | Actual rows corrupted |
| columns_corrupted | int | yes | Distinct columns corrupted |
| total_corruptions | int | yes | Total corruption count |
| dimensions_covered | string | no | JSON array of corruption dimensions |
| corruptions_sample | string | no | JSON array: first 100 corruption records |
| created_at | timestamptz | yes | When created |

**Grain fields:** `[run_id]`

### `governance.documents`

Catch-all for prose governance artifacts. One row per document version.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| doc_type | string | yes | review, insight, model, domain_context, approval, audit_trail, eda, lineage_doc, staging_proposal |
| doc_name | string | yes | Unique name (e.g. "raw-sec-edgar-pre-review") |
| spec_name | string | no | FK to spec_registry (if spec-scoped) |
| agent_id | string | no | Which agent produced it |
| title | string | yes | Human-readable title |
| content | string | yes | Full markdown or JSON content |
| version | int | yes | Document version (monotonic) |
| metadata | string | no | JSON: arbitrary key-value pairs |
| created_at | timestamptz | yes | When created |

**Grain fields:** `[doc_type, doc_name, version]`

This replaces:
- `governance/reviews/*.md` (doc_type="review")
- `governance/insights/*.md` (doc_type="insight")
- `governance/models/*.md` (doc_type="model")
- `governance/domain-context.md` (doc_type="domain_context")
- `governance/approvals/*.md` (doc_type="approval")
- `governance/audit-trail/*.md` (doc_type="audit_trail")
- `governance/eda/*.md` (doc_type="eda")
- `governance/lineage/*.json` (doc_type="lineage_doc")
- staging proposals from `staging.py` (doc_type="staging_proposal")

**Note on data dictionaries:** Data dictionary content is stored as column-level metadata in `governance.contract_columns` (description, business_term, data_type fields). There is no separate data dictionary table — `contract_columns` IS the data dictionary. Brightforge queries `contract_columns` for data dictionary views.

## Existing Table Changes

### `governance.pipeline_events` — Add `content` column

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| content | string | no | Markdown content for approval documents |

This lets approval events carry their approval document inline.

### `governance.contract_metadata` — Deprecate `contract_file_path`

The `contract_file_path` column (field_id=13) becomes meaningless when files no longer exist. Mark as deprecated: writers set it to `"iceberg://governance.contract_metadata"` (a sentinel value indicating database-native). Do NOT remove the column (avoid breaking schema evolution). Phase 2 cleanup can drop it via schema evolution if needed.

### `governance.dq_runs` — Deprecate `result_file_path`

Same treatment as `contract_file_path`. Writers set to `"iceberg://governance.dq_rule_results"` sentinel. Do not remove.

## Pipeline State Reconstruction

The current `PipelineGate` class loads/saves state from a JSON file with nested structure:

```json
{
  "spec": "spec-name",
  "zone": "bronze",
  "mode": "greenfield",
  "started": "2026-03-29T...",
  "steps": { "step-name": { "status": "COMPLETED", ... } },
  "skipped_steps": { "step-name": { "reason": "...", ... } },
  "approvals": { "artifact": { "status": "APPROVED", ... } }
}
```

After migration, this state is reconstructed from Iceberg queries:

| JSON Field | Iceberg Source | Query |
|-----------|---------------|-------|
| `spec`, `zone` | `governance.spec_registry` | `WHERE spec_name = $1` latest row |
| `mode` | `governance.spec_registry` | Store as new column (schema evolution) OR in `pipeline_step_current` |
| `started` | `governance.pipeline_events` | `MIN(event_time) WHERE spec_name = $1` |
| `steps` | `governance.pipeline_events` | `WHERE spec_name = $1 ORDER BY event_time` — group by step_name, latest event_type per step gives status |
| `skipped_steps` | `governance.pipeline_events` | `WHERE spec_name = $1 AND event_type = 'SKIPPED'` — skip_reason field has the reason |
| `approvals` | `governance.pipeline_events` | `WHERE spec_name = $1 AND approval_decision IS NOT NULL` — content field has the approval document |

The `_load()` method becomes:
1. Query `spec_registry` for zone/mode metadata
2. Query `pipeline_events WHERE spec_name = $1` to get all events
3. Build `steps` dict: for each unique `step_name`, take the latest event — `COMPLETED`/`IN_PROGRESS`/`SKIPPED` maps directly to the step status
4. Build `skipped_steps` dict: filter events where `event_type = 'SKIPPED'`, extract `skip_reason`
5. Build `approvals` dict: filter events where `approval_decision IS NOT NULL`, extract `approval_decision`, `approval_by`, `content`
6. Return the assembled state dict — identical structure to the JSON file

The `_save()` method becomes:
1. Write a `pipeline_event` for the current action (step complete, skip, approval)
2. Update `spec_registry` if zone/status changed

This is the riskiest migration. The test strategy (below) has specific requirements for verifying reconstruction fidelity.

### `pipeline_gate.py` `validate()` File Reads

The `validate()` function (and `_validate_zone_specific()`) reads 5 file paths that must each be replaced:

| # | Current File Read | Iceberg Replacement |
|---|-------------------|---------------------|
| 1 | `DQ_RULES_DIR/{spec}.json` — checks rules exist | `governance.dq_rules WHERE spec_name = $1` — check row count > 0 |
| 2 | `GOLDEN_DATASETS_DIR/{spec}-golden.json` — checks golden dataset exists with >= 3 values | `governance.golden_datasets WHERE spec_name = $1` — check row count >= 3 |
| 3 | `governance/models/{spec}-physical.md` — checks physical model exists | `governance.documents WHERE doc_type = 'model' AND doc_name = '{spec}-physical'` — check row exists |
| 4 | `governance/data-contracts/*.yaml` via glob — checks contracts exist for zone | `governance.contract_metadata WHERE zone = $1` — check row count > 0 |
| 5 | `governance/cab-decisions/index.json` — checks for pending CAB decisions | `governance.cab_decisions WHERE spec_name = $1 AND decision = 'PENDING'` — check row count = 0 |

Each replacement must produce identical pass/fail results as the file-based check.

## Module Changes

### `src/brightsmith/infra/dq_runner.py`

| Change | Description |
|--------|-------------|
| Remove `_save_rules_file()` | No more JSON file writes |
| Add `write_dq_rules()` call | Write rules to `governance.dq_rules` table via governance_db. Determine next version by querying MAX(version) per rule_id. |
| Replace `_load_rules_file()` | Query `governance.dq_rules` with latest-version-per-rule pattern. Filter by `table_name` when loading for execution. |
| Remove `_save_results()` | Already writes to Iceberg; stop writing JSON results files |
| Replace `acknowledge_failures()` | Write to `governance.dq_acknowledgments` instead of ack files |
| Remove `approve_rules()` file writes | Write new row to `governance.dq_rules` with version+1 and status=approved |
| Keep README badge writer | README.md is source code, not a governance artifact — stays as-is |
| Add `--from-files` CLI flag | Emergency fallback: read archived JSON files |

### `src/brightsmith/infra/dq_scorecard.py`

| Change | Description |
|--------|-------------|
| Remove `generate_scorecard()` file write | Return markdown string instead of writing file |
| Add `get_scorecard()` | Generate scorecard from `dq_runs JOIN dq_rule_results JOIN dq_rules ON rule_id` on the fly. Join provides category/priority enrichment that previously came from parsing rule JSON files. |

### `src/brightsmith/infra/pipeline_gate.py`

| Change | Description |
|--------|-------------|
| Remove `_save()` file write | Write `pipeline_event` + update `spec_registry` per action |
| Replace `_load()` file read | Reconstruct state from `pipeline_events` + `spec_registry` queries (see Pipeline State Reconstruction) |
| Remove `record_approval()` file write | Write to `pipeline_events` with `content` column for approval document |
| Update `check()`, `complete()`, `skip()` | All read/write Iceberg instead of JSON |
| Update `validate()` | Replace all 5 file reads with Iceberg queries (see table above) |
| Update `check_transition()` | Query Iceberg for zone readiness instead of file checks |
| Update `audit()` / `status()` | Query Iceberg instead of reading pipeline-state JSON |
| Add `--from-files` to `validate` and `check-transition` | Emergency fallback for the most critical CLI commands |

### `src/brightsmith/infra/cab.py`

| Change | Description |
|--------|-------------|
| Remove `save_decision()` file write | Write to `governance.cab_decisions` table |
| Remove `_save_index()` | Index is a query (`get_cab_decisions()`), not a file |
| Replace `load_decision()` | Query Iceberg by decision_id |
| Replace `list_decisions()` | Query Iceberg instead of reading index.json |
| Replace `update_decision()` file write | Write updated row to Iceberg |

### `src/brightsmith/infra/contract.py`

| Change | Description |
|--------|-------------|
| Remove `save_contract()` YAML write | Write to `contract_metadata` + `contract_columns` only |
| Replace `load_contract()` YAML read | Query Iceberg and reconstruct contract dict |
| Replace `list_contracts()` | Query Iceberg instead of globbing files |
| Remove `verify()` file reads | Query Iceberg for contract data |
| Keep glossary file read | `contract.py` reads `business-glossary.json` directly (same as today, per Decision #6). No staleness risk — file is authoritative. |
| Add `--from-files` CLI flag | Emergency fallback |

### `src/brightsmith/infra/golden_dataset.py`

| Change | Description |
|--------|-------------|
| Remove file reads in `load_golden_dataset()` | Query `governance.golden_datasets` table |
| Add `write_golden_dataset()` | Write values to Iceberg. Normalize filters via `json.dumps(filters, sort_keys=True, separators=(',', ':'))` before grain computation. |
| Update `verify()` | Read expected values from Iceberg, write verification results back |

### `src/brightsmith/infra/chaos_monkey/manifest.py`

| Change | Description |
|--------|-------------|
| Remove `ChaosManifest.save()` file write | Write to `governance.chaos_manifests` table |
| Replace `ChaosManifest.from_file()` | Query Iceberg by run_id |

### `src/brightsmith/infra/lineage.py`

| Change | Description |
|--------|-------------|
| Remove `cmd_generate_docs()` JSON file write | Write lineage docs to `governance.documents` with `doc_type="lineage_doc"` |

### `src/brightsmith/infra/glossary_validator.py`

| Change | Description |
|--------|-------------|
| No change | Reads `business-glossary.json` directly (Decision #6). File stays as authoritative source. |

### `src/brightsmith/infra/staging.py`

| Change | Description |
|--------|-------------|
| Remove staging proposal JSON file writes | Write staging proposals to `governance.documents` with `doc_type="staging_proposal"` |
| Replace staging proposal file reads | Query Iceberg |

### `src/brightsmith/run.py`

| Change | Description |
|--------|-------------|
| Remove `_save_run_history()` file write | Write to `governance.run_history` table |
| Add glossary sync at pipeline start | Call `sync_from_files()` for glossary at pipeline entry point to keep Iceberg mirror fresh (Decision #6) |

### `src/brightsmith/infra/governance_db.py`

| Change | Description |
|--------|-------------|
| Add 7 new schemas | dq_rules, dq_acknowledgments, cab_decisions, golden_datasets, run_history, chaos_manifests, documents |
| Add 7 new entries in `_TABLE_CONFIGS` | With grain fields |
| Add write functions | `write_dq_rules()`, `write_dq_acknowledgment()`, `write_cab_decision()`, `write_golden_dataset_values()`, `write_run_history()`, `write_chaos_manifest()`, `write_document()` |
| Add query functions | `get_dq_rules()`, `get_dq_acknowledgments()`, `get_cab_decisions()`, `get_golden_dataset()`, `get_run_history()`, `get_chaos_manifest()`, `get_document()`, `get_documents_by_type()`, `get_scorecard_data()` |
| Evolve `pipeline_events` schema | Add `content` column |
| Deprecate file path columns | `contract_metadata.contract_file_path`, `dq_runs.result_file_path` — sentinel values |
| Add `migrate_files_to_iceberg()` | One-time migration with validation report |
| Update table count | 8 -> 15 tables in docstring |

### `src/brightsmith/config.py`

| Change | Description |
|--------|-------------|
| Deprecate file path constants | Keep `DQ_RULES_DIR`, `DQ_RESULTS_DIR`, etc. but mark as `LEGACY_*` for migration/fallback |
| Add `LEGACY_GOVERNANCE_DIR` | Points to `governance/` for one-time file import |
| Remove from runtime paths | No module uses these for normal reads/writes after migration |

### `src/brightsmith/setup.py` (project bootstrapping)

| Change | Description |
|--------|-------------|
| Stop creating runtime governance directories | Remove `mkdir` for `dq-results`, `dq-scorecards`, `pipeline-state`, `approvals`, `audit-trail`, `cab-decisions`, `run-history`, `chaos-monkey`, `eda`, `lineage`, `reviews`, `insights`, `models` |
| Keep creating | `dq-rule-templates` (read-only reference), `data-contracts` (Phase 1 backup only) |

## CLAUDE.md and Agent Definition Updates

### CLAUDE.md Sections Requiring Changes

| Section | Change |
|---------|--------|
| Key Paths | Remove all `governance/` runtime paths. Add `governance_db.py` as canonical source. Add "15 Iceberg tables in governance namespace" reference. |
| DQ rules/results/scorecards references | Replace file path references with Iceberg table names and governance_db function calls |
| Pipeline state references | Replace `governance/pipeline-state/` with `governance.pipeline_events` + `governance.spec_registry` queries |
| Human Approval Gates | Replace `governance/approvals/` file references with `governance.documents` + `governance.pipeline_events` |
| Data contracts references | Replace `governance/data-contracts/` with `governance.contract_metadata` + `governance.contract_columns` |
| Golden datasets references | Replace `governance/golden-datasets/` with `governance.golden_datasets` |
| CAB decisions references | Replace `governance/cab-decisions/` with `governance.cab_decisions` |
| Audit trail references | Replace `governance/audit-trail/` with `governance.documents WHERE doc_type='audit_trail'` |
| Domain context references | Replace `governance/domain-context.md` with `governance.documents WHERE doc_type='domain_context'` |
| Business glossary references | Keep file reference (Decision #6), add note that Iceberg sync happens at pipeline start |
| Rules section | Update all rules that reference file paths to reference Iceberg tables |

### Agent Definitions Requiring Changes (`.claude/agents/`)

Every agent that references `governance/` file paths in its instructions needs updating. The pattern is consistent: replace "read/write file at `governance/X`" with "query/write `governance.table_name` via governance_db functions."

Agents affected (15): @governance-reviewer, @staff-engineer, @data-analyst, @dq-rule-writer, @dq-engineer, @doc-generator, @cde-tagger, @lineage-tracker, @cab-agent, @principal-data-architect, @adversarial-auditor, @chaos-monkey, @domain-context, @data-steward, @insight-manager.

## Migration Path

### Phase 1: Iceberg-authoritative with file backup (Spec A + Spec B)

1. **(Spec A)** Add all new tables and schema evolution to `governance_db.py`
2. **(Spec A)** Add write/query functions for all 7 new tables
3. **(Spec A)** Add `migrate_files_to_iceberg()` function
4. **(Spec A)** Run migration against existing project data
5. **(Spec A)** **Validate migration:** produce report comparing file artifact counts to Iceberg row counts per table. Spot-check 3+ records per table. Report format:
   ```
   Migration Report
   ================
   dq_rules:          12 files -> 47 rows (47 rules across 12 specs) [3 spot-checks PASS]
   cab_decisions:      3 files ->  3 rows [3 spot-checks PASS]
   golden_datasets:    4 files -> 18 rows (18 values across 4 specs) [3 spot-checks PASS]
   ...
   OVERALL: PASS (all counts match, all spot-checks pass)
   ```
6. **(Spec B)** Update all writers to write Iceberg first, then optionally emit files
7. **(Spec B)** Update all readers to query Iceberg (with `--from-files` fallback)
8. **(Spec B)** Run full test suite + manual validation

### Phase 2: Cleanup (Spec C)

1. Update CLAUDE.md — all `governance/` file path references
2. Update 15 agent definitions
3. Remove file write code from all modules
4. Remove `LEGACY_*` config constants
5. Remove `--from-files` fallback flags
6. Update `setup.py` to stop creating runtime governance directories
7. Archive existing `governance/` runtime directories to `governance/archive/`

### Rollback Plan

If Phase 1 reveals issues:
- All files still exist (dual-write in Phase 1)
- Revert reader changes to use files again
- Fix Iceberg issues
- Re-attempt

If Phase 2 reveals issues:
- Archived files are still available at `governance/archive/`
- `migrate_files_to_iceberg()` can rebuild the catalog from archived files
- `--from-files` flags (if not yet removed) read from archive path

## Brightforge Query Surface

After migration, Brightforge queries Iceberg exclusively. Key access patterns:

| Brightforge View | Iceberg Query |
|-----------------|---------------|
| Spec overview | `governance.spec_registry` latest per spec_name |
| DQ dashboard | `governance.dq_runs` + `governance.dq_rule_results` latest per spec |
| DQ rule catalog | `governance.dq_rules` latest version per rule_id |
| DQ acknowledgments | `governance.dq_rule_results LEFT JOIN governance.dq_acknowledgments` |
| CDE catalog | `governance.contract_columns WHERE is_cde = true` |
| Business term overlay | `governance.contract_columns JOIN glossary_terms ON business_term = term_id` |
| Pipeline progress | `governance.pipeline_events` per spec |
| Contract viewer | `governance.contract_metadata` + `governance.contract_columns` |
| Data dictionary | `governance.contract_columns` with description, business_term, data_type |
| CAB history | `governance.cab_decisions` |
| Golden dataset status | `governance.golden_datasets` with last_verified columns |
| Run history | `governance.run_history` |
| Document viewer | `governance.documents` by doc_type |
| Scorecard | Derived: `dq_runs JOIN dq_rule_results JOIN dq_rules` |

## Test Strategy

### Minimum Test Requirements by Sub-Spec

#### Spec A: governance_db.py expansion (~25 tests)

Per new table (7 tables x 3 tests = 21):
- **Write + query round-trip:** Write a record, query it back, verify all fields
- **Idempotency:** Write same record twice, verify second write produces 0 new rows
- **Query filtering:** Verify query functions return correct results with filters

Plus:
- **dq_rules version increment:** Write version 1, approve (version 2), verify latest-version query returns version 2
- **dq_acknowledgments join:** Write a rule result + acknowledgment, verify join returns acknowledged=true
- **golden_datasets deterministic grain:** Write same golden value with equivalent but differently-ordered filter dicts, verify grain hash is identical
- **migrate_files_to_iceberg():** Run against test fixtures with known file counts, verify row counts match

#### Spec B: Module migrations (~45 tests)

**pipeline_gate.py (15 tests minimum):**
- **State reconstruction fidelity:** Create a known pipeline state via file-based API, migrate to Iceberg, reconstruct via Iceberg queries, compare to original JSON structure
- **validate() parity:** For each of the 5 file reads, create test fixtures where (a) the check should PASS and (b) the check should FAIL. Verify Iceberg-backed validate() produces identical results. (10 tests)
- **check_transition() parity:** Test zone transition with all prerequisites met and with missing prerequisites
- **complete() + skip() round-trip:** Complete a step via Iceberg, verify it shows up in reconstructed state
- **approval round-trip:** Record an approval with content, verify it appears in reconstructed approvals dict

**dq_runner.py (8 tests):**
- Write rules to Iceberg, load them back, verify structure matches old JSON format
- Approve a rule, verify version increments and status changes
- Load rules filtered by table_name
- Acknowledge a failure, verify join with dq_acknowledgments
- Verify `--from-files` fallback reads archived files

**cab.py (5 tests):**
- Save decision, load by decision_id
- List decisions (replaces index.json query)
- Update decision status
- Filter by spec_name, by table_name

**contract.py (5 tests):**
- Generate contract to Iceberg, load and reconstruct dict
- List contracts (replaces glob)
- Verify contract (replaces YAML parse + check)
- Glossary cross-reference reads file directly (no change, but verify)

**golden_dataset.py (4 tests):**
- Write golden values, load back
- Verify deterministic filter serialization
- Verify with matching data (PASS) and mismatched data (FAIL)

**chaos_monkey (3 tests):**
- Save manifest, load by run_id
- Verify corruptions_sample round-trip

**lineage.py, staging.py, run.py (5 tests):**
- Document write + query for lineage_doc, staging_proposal
- Run history write + query

#### Spec C: Documentation + cleanup (~0 code tests, manual verification)

- Verify CLAUDE.md has no `governance/` runtime path references
- Verify agent definitions have no `governance/` runtime path references
- Verify `setup.py` does not create deprecated directories

**Total minimum: ~70 tests across Specs A + B.**

## Success Criteria

### Spec A
- [ ] 15 Iceberg tables exist in governance namespace (7 new + 8 existing)
- [ ] `pipeline_events` schema includes `content` column
- [ ] `contract_metadata.contract_file_path` uses sentinel value for new writes
- [ ] `dq_runs.result_file_path` uses sentinel value for new writes
- [ ] All write/query functions work (25 tests pass)
- [ ] `migrate_files_to_iceberg()` imports all existing file artifacts
- [ ] Migration validation report: row counts match, spot checks pass
- [ ] Idempotent: re-running migration writes 0 new rows
- [ ] All existing tests still pass

### Spec B
- [ ] No module reads from `governance/` file directories at runtime (except glossary, collision rules)
- [ ] `dq_runner.py` reads/writes rules from Iceberg
- [ ] `pipeline_gate.py` reads/writes state from Iceberg (including `validate()`, `check_transition()`, `audit()`)
- [ ] Pipeline state reconstruction produces identical structure to JSON file
- [ ] `cab.py` reads/writes decisions from Iceberg
- [ ] `contract.py` reads/writes contracts from Iceberg
- [ ] `golden_dataset.py` reads/writes values from Iceberg
- [ ] `chaos_monkey/manifest.py` reads/writes manifests from Iceberg
- [ ] `run.py` writes history to Iceberg
- [ ] `lineage.py` writes docs to Iceberg
- [ ] `staging.py` writes proposals to Iceberg
- [ ] `dq_scorecard.py` generates scorecards from Iceberg joins
- [ ] Critical CLIs have `--from-files` fallback (5 commands)
- [ ] 45 module migration tests pass
- [ ] All existing tests still pass

### Spec C
- [ ] CLAUDE.md updated: all `governance/` runtime path references replaced
- [ ] 15 agent definitions updated
- [ ] `setup.py` stops creating runtime governance directories
- [ ] Config `LEGACY_*` constants removed
- [ ] `--from-files` fallback flags removed
- [ ] `governance/` runtime directories archived to `governance/archive/`
- [ ] No module writes to `governance/` file directories at runtime
