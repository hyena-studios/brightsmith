# Framework Spec: Pipeline Must Execute Into Persistent Warehouse

**Status:** DRAFT
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @governance-reviewer
**Created:** 2026-03-28

## Problem Statement

During the sec-edgar field test, the entire gold zone was completed — 19 pipeline steps executed, staff engineer approved, pipeline gate validated PASS — and then Brightforge showed empty tables for every zone. The Iceberg catalog had zero tables in the persistent warehouse.

What happened: the DQ engineer ran rules against live data in ephemeral Claude Code sessions. The golden dataset was validated against live data in ephemeral sessions. Tests passed. Every governance artifact existed. But nobody ever ran the actual pipeline into the persistent warehouse at `data/`. When the sessions ended, the data vanished.

The pipeline gate's `validate` command checked:
- All steps COMPLETED ✓
- All output files exist ✓
- All hashes match ✓
- DQ rules exist ✓
- Golden datasets exist ✓

But it never checked: **are there actually tables in the warehouse?**

A pipeline that has never written to the warehouse is not complete. Tests validate logic. DQ validates quality. But the warehouse is the product — and the product must exist before we call it done.

## Success Criteria

- [ ] CLAUDE.md Rules section requires pipeline execution into persistent warehouse before spec completion
- [ ] CLAUDE.md Rules section requires DQ rules to run against persistent warehouse, not ephemeral catalogs
- [ ] @staff-engineer agent includes a mandatory warehouse population check before approving any zone
- [ ] @dq-engineer agent requires persistent warehouse tables before executing rules (flags missing tables as blocker)
- [ ] `pipeline_gate.py` `_validate_zone_specific()` verifies target tables exist in the Iceberg catalog with non-zero row counts
- [ ] `pipeline_gate validate` fails with clear message when tables are empty or missing

## Technical Design

### 1. CLAUDE.md Rules Section

Add two bullets to the `## Rules` section:

1. **Warehouse is mandatory:** Before a spec can be marked COMPLETE, the pipeline must have executed into the persistent Iceberg warehouse producing queryable tables. "Tests pass" and "DQ rules pass" are not sufficient — the actual pipeline entry points must have run, writing data to the project's warehouse. The staff engineer must verify tables exist with expected row counts before approving.
2. **DQ against persistent data:** DQ rules must be executed against the persistent warehouse, not ephemeral/session-scoped catalogs. If the pipeline entry points haven't populated the warehouse yet, the DQ engineer must flag this as a blocker.

### 2. Staff Engineer Agent (`.claude/agents/staff-engineer.md`)

Add a **Warehouse Population Check (MANDATORY)** subsection to the Verification Gate section:

- Before approving any zone: load the Iceberg catalog, list tables in the target namespace, confirm all spec-defined tables exist with non-zero row counts
- If tables are empty or missing: REJECT with CHANGES REQUESTED — "pipeline has not written to persistent warehouse"
- This check is not optional — a pipeline with no warehouse data is not complete regardless of test results

### 3. DQ Engineer Agent (`.claude/agents/dq-engineer.md`)

Add a **Persistent Warehouse Requirement** section:

- DQ rules must run against the persistent project warehouse, not ephemeral/session catalogs
- Before executing rules: verify target tables exist in the persistent catalog with non-zero rows
- If tables don't exist: flag as blocker — @primary-agent must run the pipeline first
- Do NOT build ad-hoc data loading to work around missing tables

### 4. Pipeline Gate (`src/brightsmith/infra/pipeline_gate.py`)

Add warehouse population verification to `_validate_zone_specific()`:

- Load the PyIceberg SqlCatalog from `CATALOG_PATH`/`WAREHOUSE_PATH`
- List tables in the zone's namespace (zone name = namespace name: bronze, silver, gold, mcp)
- If catalog doesn't exist or no tables found: add issue
- For each table: scan row count; if 0 rows, add issue
- Gracefully handle missing catalog (new project that hasn't run yet)

## Governance Artifacts Affected

| Artifact | Change |
|----------|--------|
| `CLAUDE.md` | 2 new rule bullets |
| `.claude/agents/staff-engineer.md` | New Warehouse Population Check subsection |
| `.claude/agents/dq-engineer.md` | New Persistent Warehouse Requirement section |
| `src/brightsmith/infra/pipeline_gate.py` | Warehouse verification in `_validate_zone_specific()` |

## Tests

- Run `python -m brightsmith.infra.pipeline_gate validate {spec}` against a spec with no warehouse data — should fail with clear "Tables not populated" message
- Run against a spec with populated warehouse — should pass (no false positives)

## Relationship to Other Specs

This spec depends on:
- **Pipeline gate** (`docs/specs/infra-framework-hardening.md`) — extends the existing validation system
- **Data contracts** (`docs/specs/data-contracts.md`) — contracts define expected tables, this spec verifies they exist

This spec is independent of:
- **No hardcoded entity data** (`docs/specs/no-hardcoded-entity-data.md`) — separate governance rule
