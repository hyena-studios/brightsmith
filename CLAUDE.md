# Brightsmith — Claude Code Instructions

## Project Overview
Brightsmith is a domain-agnostic AI agent data pipeline framework that transforms raw data from any source into AI-ready datasets through four zones (Bronze → Silver → Gold → MCP) with full governance metadata at every step. Unlike a domain-specific pipeline, Brightsmith discovers the domain context from the data itself — the framework doesn't know what it's processing until the data analyst examines it.

## Stack
- Python 3.11+
- DuckDB with Iceberg extension
- Apache Iceberg tables (local storage, SQLite catalog)
- uv for dependency management

## Key Paths
- Source code: `src/brightsmith/` (organized by zone: bronze, silver, gold, mcp)
- Infrastructure: `src/brightsmith/infra/` (cross-cutting: iceberg_setup, dq_runner, dq_scorecard, lineage, staging, period_disambiguator, promote, grain, contract, golden_dataset, verification, glossary_validator, pipeline_gate, cab)
- Period disambiguator: `src/brightsmith/infra/period_disambiguator.py` (temporal period classification)
- Chaos monkey: `src/brightsmith/infra/chaos_monkey/` (schema-agnostic adversarial DQ testing)
- Integration test harness: `src/brightsmith/infra/integration_test_harness.py` (golden dataset validation)
- DQ rule templates: `governance/dq-rule-templates/` (mandatory patterns for gold zone)
- Golden datasets: `governance/golden-datasets/` (known-correct reference values)
- Data: `data/` (gitignored, organized by zone)
- Domain pack: `domain/` (manifest.yaml, sources/, concept-mappings/)
- Domain assignment: `domain/manifest.yaml` → `domain.name` (written by @domain-context, read by Brightforge for sidebar display)
- Insight reports: `governance/insights/` (zone transition analysis)
- Governance artifacts: `governance/`
- Data models: `governance/models/` (conceptual, logical, physical)
- DQ rules: `governance/dq-rules/` (JSON rule definitions with SQL + thresholds)
- DQ results: `governance/dq-results/` (timestamped execution results)
- DQ scorecards: `governance/dq-scorecards/` (markdown scorecards from real execution)
- Domain context: `governance/domain-context.md` (canonical domain knowledge for all agents)
- Business glossary: `governance/business-glossary.json`
- Pipeline state: `governance/pipeline-state/` (programmatic gate enforcement per spec)
- Pipeline gate module: `src/brightsmith/infra/pipeline_gate.py` (state machine + CLI)
- Data contracts: `governance/data-contracts/` (machine-readable YAML per table)
- Contract module: `src/brightsmith/infra/contract.py` (generate, verify, diff, list CLI)
- Human approval documents: `governance/approvals/` (plain-English review docs for approval gates)
- Audit trail: `governance/audit-trail/` (approval decisions, skip justifications, pipeline checklists)
- CAB decisions: `governance/cab-decisions/` (schema change reviews, deprecation registry)
- CAB module: `src/brightsmith/infra/cab.py` (classification, blast radius, decision records CLI)
- Specs: `docs/specs/`
- Tests: `tests/` (organized by zone)
- Agent definitions: `.claude/agents/`

## Workflow References (Read On-Demand)

Before starting spec work, read the workflow document for the relevant zone:
- Bronze zone specs: `docs/workflows/bronze-pipeline.md` (includes domain discovery and bootstrapping)
- Silver/Gold zone specs: `docs/workflows/silver-gold-pipeline.md` (greenfield, backfill, concept normalization)
- Zone transitions: `docs/workflows/zone-transitions.md`
- MCP zone specs: `docs/workflows/mcp-pipeline.md`
- Human approval gates: `docs/workflows/human-approval-gates.md`
- At session start and end, follow: `docs/workflows/session-logging.md`

## Rules

- Specs are the source of truth — if it's not in the spec, it doesn't get built
- Every transformation produces governance artifacts (lineage, DQ rules, business term mappings, audit trail)
- DQ rules validate real data, never placeholders
- Every agent logs its reasoning, not just outputs
- No changes to data schemas without a spec
- `REQUIRE_HUMAN_APPROVAL` in `src/config.py` is the single global toggle for all human-in-the-loop gates (exception: MAJOR schema changes always require human approval via @cab-agent regardless of this toggle)
- @staff-engineer reviews last — no spec is marked complete until he approves
- @staff-engineer can send work back to any agent for fixes
- Test theater (tests that don't validate real behavior) is a rejection
- DQ has three agents with distinct roles: @data-analyst (profiles data, produces EDA reports), @dq-rule-writer (writes rules from EDA evidence), @dq-engineer (executes rules, produces scorecards). No agent does another's job.
- DQ rules follow a lifecycle: `PROPOSED → APPROVED → ACTIVE`. Rules must be executed against real Iceberg data via `python -m brightsmith.infra.dq_runner run`. P0 failures block spec completion.
- DQ rule approval respects `REQUIRE_HUMAN_APPROVAL` — when False, proposed rules auto-advance to approved
- DQ scorecards must be generated from real execution results (`python -m brightsmith.infra.dq_runner scorecard`), not test results
- @governance-reviewer post-implementation check verifies: DQ rules exist, rules have been executed (results file exists), no P0 failures in latest results
- Zone transformers MUST use the idempotent promote pattern (`from brightsmith.infra.promote import promote`) — no bare `append_data()` for derived tables. Re-running with the same data must produce 0 new rows.
- Every derived table row gets a deterministic `record_id` via `compute_grain_id(row, grain_fields, prefix)` from `brightsmith.infra.grain`. Same input → same hash → dedup skips it.
- Grain fields are defined once per table and used everywhere: promote dedup, DQ uniqueness rules, data contracts, golden dataset filters.
- `BaseIngestor` (bronze zone) already has grain-based dedup — the promote pattern extends this to silver/gold/MCP zones.
- Every pipeline agent MUST be either executed or explicitly skipped with documented justification — silent omission is not allowed
- Skip justifications must reference a specific governance artifact (e.g., "domain-context.md PII section says 'No personal data expected'")
- Pipeline execution is tracked by `src/brightsmith/infra/pipeline_gate.py` — every spec gets a state file at `governance/pipeline-state/{spec}-pipeline.json`
- Before any agent runs: `python3 -m brightsmith.infra.pipeline_gate check {spec} {step}` — if BLOCKED, stop
- After any agent completes: `python3 -m brightsmith.infra.pipeline_gate complete {spec} {step} --output {path}`
- Before marking a spec COMPLETE: `python3 -m brightsmith.infra.pipeline_gate validate {spec}` must PASS
- @staff-engineer enforces minimum test counts per zone: Raw=10, Base=15, Consumable=15, AI-Ready=10, Integration=5. Specs below minimum get CHANGES REQUESTED.
- Never hardcode entity-specific data (CIK lists, fiscal year end months, company names, ticker symbols, sector mappings, entity counts) in Python source code. All entity-specific values must come from governance artifacts (`governance/entity-registry.json`, `domain/sources/*.yaml`, `governance/business-glossary.json`) or be derived from source data at runtime. Adding a new entity must never require a code change — only a config/registry update and pipeline re-run.
- Hardcoded entity patterns include: Python dicts keyed by CIK/ticker/entity name with literal values, if/elif chains that branch on entity identifiers, list literals containing specific entity IDs, and any constant that would need updating when a new entity is added. These are governance violations — entity data belongs in governance artifacts, not source code.
- The litmus test for entity hardcoding: "If a user adds a new entity to entity-registry.json and re-runs the pipeline, does the new entity flow through correctly without any code changes?" If the answer is no, the implementation violates this rule.
- Before a spec can be marked COMPLETE, the pipeline must have executed end-to-end into the persistent Iceberg warehouse producing queryable tables. "Tests pass" and "DQ rules pass against ephemeral data" are not sufficient — the actual pipeline entry points (registered in `domain/manifest.yaml`) must have run successfully, writing data to the project's warehouse at `data/`. The staff engineer must verify that tables exist in the catalog with expected row counts before approving.
- DQ rules must be executed against the persistent project warehouse (`data/` directory), not ephemeral or session-scoped catalogs. If the pipeline entry points haven't populated the warehouse yet (no tables exist), the DQ engineer must flag this as a blocker rather than building ad-hoc data loading. If it didn't write to the warehouse, it didn't happen.
