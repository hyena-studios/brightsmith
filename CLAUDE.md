# Grist — Claude Code Instructions

## Project Overview
Grist is a domain-agnostic AI agent data pipeline framework that transforms raw data from any source into AI-ready datasets through four zones (Raw → Base → Consumable → AI-Ready) with full governance metadata at every step. Unlike a domain-specific pipeline, Grist discovers the domain context from the data itself — the framework doesn't know what it's processing until the data analyst examines it.

## Stack
- Python 3.11+
- DuckDB with Iceberg extension
- Apache Iceberg tables (local storage, SQLite catalog)
- uv for dependency management

## Key Paths
- Source code: `src/grist/` (organized by zone: raw, base, consumable, ai_ready)
- Infrastructure: `src/grist/infra/` (cross-cutting: iceberg_setup, dq_runner, dq_scorecard, lineage, staging, period_disambiguator)
- Period disambiguator: `src/grist/infra/period_disambiguator.py` (temporal period classification)
- Chaos monkey: `src/grist/infra/chaos_monkey/` (schema-agnostic adversarial DQ testing)
- Integration test harness: `src/grist/infra/integration_test_harness.py` (golden dataset validation)
- DQ rule templates: `governance/dq-rule-templates/` (mandatory patterns for consumable zone)
- Golden datasets: `governance/golden-datasets/` (known-correct reference values)
- Data: `data/` (gitignored, organized by zone)
- Domain pack: `domain/` (manifest.yaml, sources/, concept-mappings/)
- Insight reports: `governance/insights/` (zone transition analysis)
- Governance artifacts: `governance/`
- Data models: `governance/models/` (conceptual, logical, physical)
- DQ rules: `governance/dq-rules/` (JSON rule definitions with SQL + thresholds)
- DQ results: `governance/dq-results/` (timestamped execution results)
- DQ scorecards: `governance/dq-scorecards/` (markdown scorecards from real execution)
- Domain context: `governance/domain-context.md` (canonical domain knowledge for all agents)
- Business glossary: `governance/business-glossary.json`
- Specs: `docs/specs/`
- Tests: `tests/` (organized by zone)
- Agent definitions: `.claude/agents/`

## Project Bootstrapping

New domain projects are scaffolded by @setup — the first agent a user interacts with. It creates the full project structure (domain pack, governance directories, ingestor skeleton, first spec, CLAUDE.md, pyproject.toml with grist dependency) from a few questions about the data source. After @setup finishes, the normal spec-driven pipeline takes over.

## Domain Discovery

Grist does not assume domain knowledge upfront. The discovery process works as follows:

1. **Domain pack provides raw access** — `domain/manifest.yaml` and `domain/sources/*.yaml` define HOW to get data (URLs, API endpoints, file paths, fetch methods), not what it means
2. **Raw zone lands data as-is** — no interpretation, just storage with metadata
3. **@data-analyst discovers context** — after raw ingestion, the data analyst profiles the data to determine: what entities exist, what the grain is, what fields mean, what patterns emerge, what the domain vocabulary looks like
4. **@domain-context synthesizes domain knowledge** — takes the data analyst's EDA findings and produces `governance/domain-context.md`, the **canonical domain context document**. This replaces the hardcoded domain knowledge that would exist in a domain-specific pipeline. It covers: domain vocabulary, entity types, temporal patterns, applicable regulations, taxonomy/classification systems, edge cases, concept mapping guidance, and PII expectations.
5. **All downstream agents reference domain context** — @data-steward, @cde-tagger, @entity-resolver, @dq-rule-writer, @pii-scanner, @temporal-modeler, @insight-manager, @bcbs239-auditor, @adversarial-auditor, @content-strategist, @principal-data-architect, @doc-generator, and @mcp-engineer all read `governance/domain-context.md` as their source of domain knowledge. No agent independently invents domain assumptions.

This is the key difference from a domain-specific pipeline: specs for Base and Consumable zones may be written AFTER discovery, not before. The domain context document is the bridge between "we don't know what this data is" and "every agent operates with full domain awareness."

## Agent Workflow

### Raw Zone Pipeline (physical-only, quick and dirty)
1. @governance-reviewer — Pre-implementation review
2. @primary-agent — Implementation (ingest raw data via BaseIngestor)
3. @data-analyst — EDA on raw data (distributions, outliers, edge cases, threshold evidence, **domain discovery**)
4. @domain-context — Synthesize domain knowledge from EDA into `governance/domain-context.md` (canonical domain context for all downstream agents). Includes **EDA-informed user interview**: generates 5-10 targeted questions from EDA findings (temporal patterns, grain/uniqueness, domain semantics, known edge cases, external context), presents to user, flags unanswered questions as risks with mandatory DQ rule requirements.
5. @dq-rule-writer — Write raw DQ rules from EDA report + domain context (completeness, validity, volume, freshness)
6. @dq-engineer — Execute rules against real data, produce scorecard
7. @chaos-monkey — 5-cycle adversarial hardening:
   a. Inject corruptions into shadow copy (rates: 5%, 6%, 7%, 8%, 10%)
   b. Run DQ rules against shadow tables (`--shadow` flag)
   c. @chaos-monkey generates After-Action Report
   d. If gaps found: @dq-rule-writer patches rules, return to (a)
   e. After 5 cycles or no new gaps for 2 consecutive cycles: proceed
8. @lineage-tracker — OpenLineage capture
9. @cde-tagger — CDE mapping update (using domain context for taxonomy interpretation)
10. @doc-generator — Dictionary + contracts update (using domain context for plain-English definitions)
11. @governance-reviewer — Post-implementation completeness check
12. @staff-engineer — Final quality review (LAST gate before completion)

### Zone Transitions

At **every** zone boundary (raw-to-base, base-to-consumable, consumable-to-ai-ready), after all specs in a zone are complete:

1. @principal-data-architect — **Architecture review of the completed zone**
   - Reviews all code, tests, governance artifacts, DQ results, and data in the completed zone
   - Assesses: architecture decisions, data quality trust, governance proportionality, domain context accuracy, code quality
   - Produces zone transition review: `governance/reviews/[zone]-architecture-review.md`
   - Can flag risks that block progression to the next zone
   - This is a checkpoint — catching structural issues is cheaper here than after the next zone is built

2. @insight-manager — **Strategic analysis** (base-to-consumable and consumable-to-ai-ready only, NOT raw-to-base)
   - Queries real Iceberg tables (not just schemas)
   - Builds on existing EDA reports, DQ scorecards, CDE catalog
   - Recommends data products ranked by value/feasibility
   - Identifies external data combination opportunities
   - Recommends chat agent design (questions users will ask, tools needed, grounding context)
   - Each recommendation includes **Verification Criteria** (what DQ rule confirms implementation)
   - Suggests spec order for the next zone
   - Output: `governance/insights/[source-zone]-to-[target-zone]-insights.md`

@principal-data-architect runs at ALL transitions (including raw-to-base). @insight-manager runs at base-to-consumable and consumable-to-ai-ready only.

The Insight Report is the primary input for spec writing in the next zone. No spec should be written without it. The pipeline always produces a **tool-use chat agent** as the AI-Ready zone deliverable — insight reports at the consumable-to-ai-ready transition should focus on chat agent design.

### Base & Consumable Zone Pipeline (with data modeling gates)

The pipeline auto-detects **greenfield** vs **backfill** mode:

- **Greenfield** (tables don't exist yet): models are proposed BEFORE implementation
- **Backfill** (tables already exist): models are reverse-engineered FROM existing tables/code

#### Greenfield Mode (new tables)
1. @governance-reviewer — Pre-implementation review (checks model gate below)
2. @data-steward — Identify and propose **business terms** from spec → HUMAN APPROVAL GATE (project-specific terms only; external standard terms auto-approve)
3. @semantic-modeler — Propose **conceptual model** (referencing approved glossary terms) → HUMAN APPROVAL GATE
4. @semantic-modeler — Propose **logical model** → HUMAN APPROVAL GATE
5. @data-analyst — EDA on source data (profile what will populate base tables, inform thresholds)
6. @dq-rule-writer — Write base DQ rules from EDA report + logical model (uniqueness, referential integrity, consistency, coverage)
7. @semantic-modeler — Generate **physical model** from approved logical
8. @primary-agent — Implementation (must match approved physical model)
9. @dq-engineer — Execute all rules against real data, produce scorecard
10. @chaos-monkey — 5-cycle adversarial hardening:
    a. Inject corruptions into shadow copy (rates: 5%, 6%, 7%, 8%, 10%)
    b. Run DQ rules against shadow tables (`--shadow` flag)
    c. @chaos-monkey generates After-Action Report
    d. If gaps found: @dq-rule-writer patches rules, return to (a)
    e. After 5 cycles or no new gaps for 2 consecutive cycles: proceed
11. @lineage-tracker — OpenLineage capture
12. @cde-tagger — CDE mapping update
13. @doc-generator — Dictionary + contracts update
14. @governance-reviewer — Post-implementation completeness check (verifies models match)
15. @staff-engineer — Final quality review (LAST gate before completion)

#### Backfill Mode (existing tables, missing models)
1. @semantic-modeler — Reverse-engineer **physical model** from existing tables/code
2. @semantic-modeler — Abstract **logical model** from physical → HUMAN APPROVAL GATE
3. @data-analyst — EDA on existing base data (profile actual data state)
4. @dq-rule-writer — Write base DQ rules from EDA report + logical model
5. @dq-engineer — Execute rules, produce scorecard
6. @chaos-monkey — 5-cycle adversarial hardening (same protocol as Greenfield)
7. @semantic-modeler — Abstract **conceptual model** from logical → HUMAN APPROVAL GATE
8. @data-steward — Extract **business terms** from conceptual model → HUMAN APPROVAL GATE (project-specific terms only)
9. @governance-reviewer — Post-backfill completeness check (verifies models and glossary are consistent with existing implementation)
10. @staff-engineer — Final review

#### Mode Detection
@semantic-modeler determines the mode automatically:
- If the spec's target tables exist in the Iceberg catalog AND source code exists in `src/` → **backfill**
- If the spec's target tables do not exist → **greenfield**
- If a spec modifies existing tables (schema evolution) → **greenfield** for the new/changed parts

The human approval gates are controlled by `REQUIRE_HUMAN_APPROVAL` in `src/config.py`. When False (dev/demo mode), models auto-advance but all three artifacts are still produced in `governance/models/`.

Model artifacts are stored in `governance/models/` as `[spec-name]-conceptual.md`, `[spec-name]-logical.md`, `[spec-name]-physical.md`.

## Rules
- Specs are the source of truth — if it's not in the spec, it doesn't get built
- Every transformation produces governance artifacts (lineage, DQ rules, business term mappings, audit trail)
- DQ rules validate real data, never placeholders
- Every agent logs its reasoning, not just outputs
- No changes to data schemas without a spec
- Base/Consumable tables require approved business terms → conceptual → logical → physical models before implementation
- Business terms from recognized external standards are auto-approved; project-specific terms require human approval
- `REQUIRE_HUMAN_APPROVAL` in `src/config.py` is the single global toggle for all human-in-the-loop gates
- @staff-engineer reviews last — no spec is marked complete until he approves
- @staff-engineer can send work back to any agent for fixes
- Test theater (tests that don't validate real behavior) is a rejection
- When model files in `governance/models/` are created or modified, update the corresponding Mermaid diagrams in the "Data Models" section of `README.md` (all three levels: conceptual, logical, AND physical — full details live in governance/models/)
- When `governance/business-glossary.json` is modified, update the "Business Glossary" section of `README.md` (term counts, key terms tables)
- Data models store governance metadata as **IDs only** (`BT-XXX`) with derived flags (`is_cde`, `is_pii`) — never inline definitions. Authoritative source: `governance/business-glossary.json` (terms with `is_cde` and `is_pii` boolean flags). Documentation (README) dereferences IDs into human-readable names.
- All model levels include `Business Term`, `Is CDE`, `Is PII` columns: conceptual on entity tables, logical on attribute tables, physical on column tables. CDE and PII flags are derived from the referenced business term.
- DQ has three agents with distinct roles: @data-analyst (profiles data, produces EDA reports), @dq-rule-writer (writes rules from EDA evidence), @dq-engineer (executes rules, produces scorecards). No agent does another's job.
- DQ rules follow a lifecycle: `PROPOSED → APPROVED → ACTIVE`. Rules must be executed against real Iceberg data via `python -m grist.infra.dq_runner run`. P0 failures block spec completion.
- DQ rule approval respects `REQUIRE_HUMAN_APPROVAL` — when False, proposed rules auto-advance to approved
- DQ scorecards must be generated from real execution results (`python -m grist.infra.dq_runner scorecard`), not test results
- @governance-reviewer post-implementation check verifies: DQ rules exist, rules have been executed (results file exists), no P0 failures in latest results
- Domain packs extend `BaseIngestor` and implement `fetch()`, `flatten()`, and `get_schema()` — the framework handles Iceberg table management, dedup, metadata enrichment, and snapshot management
- Concept normalization uses a tiered matching engine (exact → prefix → pattern → heuristic) with discovery mode when no mappings exist
- Base/Consumable specs involving temporal data MUST use PeriodDisambiguator (`src/grist/infra/period_disambiguator.py`) for period classification rather than ad-hoc period logic. The framework utility handles annual vs quarterly vs point-in-time classification using date-span analysis.
- Every consumable spec MUST have a golden dataset (`governance/golden-datasets/{spec}-golden.json`) with at least 3 independently verifiable values before @staff-engineer review

# Session Logging

## Purpose
Every Claude Code session is logged for two reasons:
1. Open source transparency — anyone can see exactly how this project was built
2. Continuity — pick up where we left off between sessions

## Session Log Location
All session logs go in `docs/sessions/`

## At the START of Every Session

Create a new file: `docs/sessions/YYYY-MM-DD-HH-MM-session.md`

Write the following header immediately:

```markdown
# Session: [YYYY-MM-DD HH:MM]

## Prompt Provided
\`\`\`
[Paste the EXACT prompt you were given, verbatim, no edits]
\`\`\`

## Specs Referenced
- [List any spec files referenced in the prompt or during the session]

## Session Goal
[1-2 sentence summary of what this session is trying to accomplish]
```

## At the END of Every Session

Append the following to the same session log file:

```markdown
## Changes Made

### Files Created
| File | Purpose |
|------|---------|
| `path/to/file` | What it does |

### Files Modified
| File | What Changed |
|------|-------------|
| `path/to/file` | Summary of changes |

### Files Deleted
| File | Why |
|------|-----|
| `path/to/file` | Reason |

## Decisions Made
[List any judgment calls, trade-offs, or architectural decisions with rationale.]

## Problems Encountered
[Anything that didn't work the first time, workarounds, surprises in the data.]

## Current State
[What's working now that wasn't before this session]

## Next Steps
[What should the next session pick up on]

## Session Stats
- Duration: ~[X] minutes
- Files created: X
- Files modified: X
- DQ rules added: X (if applicable)
- Governance artifacts produced: [list] (if applicable)
```

## Rules
- The verbatim prompt capture is non-negotiable — copy it exactly as received, including typos
- Be honest in Problems Encountered — the failures are better content than the successes
- Decisions Made should capture the WHY, not just the WHAT
- If a session spans multiple specs, log all of them
- Don't sanitize or polish — raw is better for transparency
- Session logs are NEVER deleted, only appended to
- If you need to reference a previous session, check `docs/sessions/` first
