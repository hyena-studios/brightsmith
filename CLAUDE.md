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

## Project Bootstrapping

New domain projects are scaffolded by @setup — the first agent a user interacts with. It creates the full project structure (domain pack, governance directories, ingestor skeleton, first spec, CLAUDE.md, pyproject.toml with brightsmith dependency) from a few questions about the data source. After @setup finishes, the normal spec-driven pipeline takes over.

## Domain Discovery

Brightsmith does not assume domain knowledge upfront. The discovery process works as follows:

1. **Domain pack provides raw access** — `domain/manifest.yaml` and `domain/sources/*.yaml` define HOW to get data (URLs, API endpoints, file paths, fetch methods), not what it means
2. **Bronze zone lands data as-is** — no interpretation, just storage with metadata
3. **@data-analyst discovers context** — after raw ingestion, the data analyst profiles the data to determine: what entities exist, what the grain is, what fields mean, what patterns emerge, what the domain vocabulary looks like
4. **@domain-context synthesizes domain knowledge** — takes the data analyst's EDA findings and produces `governance/domain-context.md`, the **canonical domain context document**. This replaces the hardcoded domain knowledge that would exist in a domain-specific pipeline. It covers: domain vocabulary, entity types, temporal patterns, applicable regulations, taxonomy/classification systems, edge cases, concept mapping guidance, and PII expectations.
5. **All downstream agents reference domain context** — @data-steward, @cde-tagger, @entity-resolver, @dq-rule-writer, @pii-scanner, @temporal-modeler, @insight-manager, @bcbs239-auditor, @adversarial-auditor, @content-strategist, @principal-data-architect, @doc-generator, @cab-agent, and @mcp-engineer all read `governance/domain-context.md` as their source of domain knowledge. No agent independently invents domain assumptions.

This is the key difference from a domain-specific pipeline: specs for Silver and Gold zones may be written AFTER discovery, not before. The domain context document is the bridge between "we don't know what this data is" and "every agent operates with full domain awareness."

## Agent Workflow

### Bronze Zone Pipeline (physical-only, quick and dirty)
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

At **every** zone boundary (bronze-to-silver, silver-to-gold, gold-to-mcp), after all specs in a zone are complete:

1. @principal-data-architect — **Architecture review of the completed zone**
   - Reviews all code, tests, governance artifacts, DQ results, and data in the completed zone
   - Assesses: architecture decisions, data quality trust, governance proportionality, domain context accuracy, code quality
   - Produces zone transition review: `governance/reviews/[zone]-architecture-review.md`
   - Can flag risks that block progression to the next zone
   - This is a checkpoint — catching structural issues is cheaper here than after the next zone is built

2. @insight-manager — **Strategic analysis** (silver-to-gold and gold-to-mcp only, NOT bronze-to-silver)
   - Queries real Iceberg tables (not just schemas)
   - Builds on existing EDA reports, DQ scorecards, CDE catalog
   - Recommends data products ranked by value/feasibility
   - Identifies external data combination opportunities
   - Recommends MCP server design (questions users will ask, tools needed, grounding context)
   - Each recommendation includes **Verification Criteria** (what DQ rule confirms implementation)
   - Suggests spec order for the next zone
   - Output: `governance/insights/[source-zone]-to-[target-zone]-insights.md`

@principal-data-architect runs at ALL transitions (including bronze-to-silver). @insight-manager runs at silver-to-gold and gold-to-mcp only.

The Insight Report is the primary input for spec writing in the next zone. No spec should be written without it. The pipeline always produces a **MCP server** as the MCP zone deliverable — insight reports at the gold-to-mcp transition should focus on MCP server design.

### Silver Base & Gold Zone Gold Zone Pipeline (with data modeling gates)

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
9. @cab-agent — Schema change review (skipped for new tables; classifies PATCH/MINOR/MAJOR, maps blast radius, proposes fork for MAJOR)
10. @dq-engineer — Execute all rules against real data, produce scorecard
11. @chaos-monkey — 5-cycle adversarial hardening:
    a. Inject corruptions into shadow copy (rates: 5%, 6%, 7%, 8%, 10%)
    b. Run DQ rules against shadow tables (`--shadow` flag)
    c. @chaos-monkey generates After-Action Report
    d. If gaps found: @dq-rule-writer patches rules, return to (a)
    e. After 5 cycles or no new gaps for 2 consecutive cycles: proceed
12. @lineage-tracker — OpenLineage capture
13. @cde-tagger — CDE mapping update
14. @doc-generator — Dictionary + contracts update
15. @governance-reviewer — Post-implementation completeness check (verifies models match, verifies CAB decision exists if applicable)
16. @staff-engineer — Final quality review (LAST gate before completion)

#### Backfill Mode (existing tables, missing models)
1. @semantic-modeler — Reverse-engineer **physical model** from existing tables/code
2. @semantic-modeler — Abstract **logical model** from physical → HUMAN APPROVAL GATE
3. @data-analyst — EDA on existing base data (profile actual data state)
4. @dq-rule-writer — Write base DQ rules from EDA report + logical model
5. @dq-engineer — Execute rules, produce scorecard
6. @chaos-monkey — 5-cycle adversarial hardening (same protocol as Greenfield)
7. @cab-agent — Schema change review (skipped for new tables; runs after DQ for backfill since implementation already exists)
8. @semantic-modeler — Abstract **conceptual model** from logical → HUMAN APPROVAL GATE
9. @data-steward — Extract **business terms** from conceptual model → HUMAN APPROVAL GATE (project-specific terms only)
10. @governance-reviewer — Post-backfill completeness check (verifies models and glossary are consistent with existing implementation, verifies CAB decision if applicable)
11. @staff-engineer — Final review

#### Mode Detection
@semantic-modeler determines the mode automatically:
- If the spec's target tables exist in the Iceberg catalog AND source code exists in `src/` → **backfill**
- If the spec's target tables do not exist → **greenfield**
- If a spec modifies existing tables (schema evolution) → **greenfield** for the new/changed parts

The human approval gates are controlled by `REQUIRE_HUMAN_APPROVAL` in `src/config.py`. When False (dev/demo mode), models auto-advance but all three artifacts are still produced in `governance/models/`.

Model artifacts are stored in `governance/models/` as `[spec-name]-conceptual.md`, `[spec-name]-logical.md`, `[spec-name]-physical.md`.

### Concept Normalization Step (Silver Zone, after @data-steward)

If `governance/domain-context.md` contains a "Canonical Concept Map" section with status CONFIRMED or PROPOSED:

1. @primary-agent generates concept mapping config files in `domain/concept-mappings/` from the domain context's concept map
2. @primary-agent creates a `base.concept_map` table using `ConceptNormalizer` that maps raw classification codes to canonical business concepts
3. The concept map table becomes a dimension that joins to the silver zone fact table (e.g., `base.financial_facts` or equivalent)
4. @dq-rule-writer writes coverage rules: what % of raw codes map to a canonical concept? (P1 rule, threshold from EDA)
5. Unmapped codes are preserved in the base table (no data loss) but flagged

This step is SKIPPABLE only if the @principal-data-architect explicitly approves skipping it at the zone transition review (e.g., the data has no classification codes to normalize).

## Rules
- Specs are the source of truth — if it's not in the spec, it doesn't get built
- Every transformation produces governance artifacts (lineage, DQ rules, business term mappings, audit trail)
- DQ rules validate real data, never placeholders
- Every agent logs its reasoning, not just outputs
- No changes to data schemas without a spec
- Silver/Gold tables require approved business terms → conceptual → logical → physical models before implementation
- Business terms from recognized external standards are auto-approved; project-specific terms require human approval
- `REQUIRE_HUMAN_APPROVAL` in `src/config.py` is the single global toggle for all human-in-the-loop gates (exception: MAJOR schema changes always require human approval via @cab-agent regardless of this toggle)
- @domain-context MUST write `domain.name` to `manifest.yaml` after synthesizing domain knowledge (`python3 -m brightsmith.domain_loader assign-domain --name "..." --confidence "..."`). Brightforge reads this for sidebar display.
- Schema modifications to existing Silver/Gold tables with active contracts trigger @cab-agent review — classifies changes as PATCH/MINOR/MAJOR, maps blast radius, proposes fork for MAJOR changes. CAB decisions are stored at `governance/cab-decisions/` as structured JSON.
- @staff-engineer reviews last — no spec is marked complete until he approves
- @staff-engineer can send work back to any agent for fixes
- Test theater (tests that don't validate real behavior) is a rejection
- When model files in `governance/models/` are created or modified, update the corresponding Mermaid diagrams in the "Data Models" section of `README.md` (all three levels: conceptual, logical, AND physical — full details live in governance/models/)
- When `governance/business-glossary.json` is modified, update the "Business Glossary" section of `README.md` (term counts, key terms tables)
- Data models store governance metadata as **IDs only** (`BT-XXX`) for business terms — never inline definitions. `is_cde` and `is_pii` are direct annotations on physical data elements (columns in contracts and models), not derived from business terms. Authoritative source for terms: `governance/business-glossary.json`. Authoritative source for CDE/PII flags: `governance/data-contracts/*.yaml`. Documentation (README) dereferences term IDs into human-readable names.
- All model levels include `Business Term`, `Is CDE`, `Is PII` columns: conceptual on entity tables, logical on attribute tables, physical on column tables. CDE and PII flags are direct annotations on the physical column, set by @cde-tagger on data contracts. Models mirror contract values.
- DQ has three agents with distinct roles: @data-analyst (profiles data, produces EDA reports), @dq-rule-writer (writes rules from EDA evidence), @dq-engineer (executes rules, produces scorecards). No agent does another's job.
- DQ rules follow a lifecycle: `PROPOSED → APPROVED → ACTIVE`. Rules must be executed against real Iceberg data via `python -m brightsmith.infra.dq_runner run`. P0 failures block spec completion.
- DQ rule approval respects `REQUIRE_HUMAN_APPROVAL` — when False, proposed rules auto-advance to approved
- DQ scorecards must be generated from real execution results (`python -m brightsmith.infra.dq_runner scorecard`), not test results
- @governance-reviewer post-implementation check verifies: DQ rules exist, rules have been executed (results file exists), no P0 failures in latest results
- Domain packs extend `BaseIngestor` and implement `fetch()`, `flatten()`, and `get_schema()` — the framework handles Iceberg table management, dedup, metadata enrichment, and snapshot management
- Concept normalization uses a tiered matching engine (exact → prefix → pattern → heuristic) with discovery mode when no mappings exist. Returns `NormalizationResult` with confidence scores (1.0=exact, 0.7=prefix, 0.6=pattern, 0.3=heuristic, 0.0=unmapped). Mappings below `CONFIDENCE_FLOOR` (0.7) require human approval.
- Collision resolution rules must be defined at `governance/concept-normalization/collision-rules.json` when multiple source codes map to the same canonical concept. @data-steward produces these; approval required before gold spec implementation.
- Business glossary terms require 11 fields per term (see @data-steward agent definition). Validate with `python3 -m brightsmith.infra.glossary_validator validate`. CDE/PII flags are not glossary fields — they live on physical data elements in data contracts.
- `BaseIngestor.ingest()` auto-emits runtime lineage events — no manual lineage creation needed for bronze zone. Base/consumable/MCP zone transformations should call `emit_start()`/`emit_complete()` from `brightsmith.infra.lineage`.
- @lineage-tracker is a verifier, not a creator — it checks that lineage events exist with runtime metadata (snapshot IDs, row counts, DQ metrics), not static templates.
- Golden datasets are verified with `python3 -m brightsmith.infra.golden_dataset verify --spec {spec}`. Pipeline gate enforces existence + minimum 3 values for gold specs.
- Verification framework (`python3 -m brightsmith.infra.verification run`) validates correctness: "is this number right?" not just "is this column non-null?". Pass rate >= 80% required for MCP zone.
- @staff-engineer enforces minimum test counts per zone: Raw=10, Base=15, Consumable=15, AI-Ready=10, Integration=5. Specs below minimum get CHANGES REQUESTED.
- Consumable and MCP zone tables MUST have a data contract at `governance/data-contracts/{table-name}.yaml`. @doc-generator generates it from the actual Iceberg table schema.
- Data contracts are machine-readable YAML with schema, quality, lineage, and consumer sections. Verify with `python3 -m brightsmith.infra.contract verify {name}`.
- Breaking schema changes (column removed/renamed/type changed/grain changed) require a major version bump. Non-breaking changes (column added) require minor bump.
- Contract lifecycle: DRAFT (generated) → ACTIVE (staff-engineer approved) → DEPRECATED (superseded). Active contracts are enforced on every pipeline run.
- Zone transformers MUST use the idempotent promote pattern (`from brightsmith.infra.promote import promote`) — no bare `append_data()` for derived tables. Re-running with the same data must produce 0 new rows.
- Every derived table row gets a deterministic `record_id` via `compute_grain_id(row, grain_fields, prefix)` from `brightsmith.infra.grain`. Same input → same hash → dedup skips it.
- Grain fields are defined once per table and used everywhere: promote dedup, DQ uniqueness rules, data contracts, golden dataset filters.
- `BaseIngestor` (bronze zone) already has grain-based dedup — the promote pattern extends this to silver/gold/MCP zones.
- Headless pipeline runner: `python -m brightsmith.run` executes the full pipeline without AI agents. Supports `--zone`, `--validate-only`, `--dry-run`, `--output json`. Exit codes: 0=success, 1=DQ failure, 2=transform error, 3=contract violation, 4=config error.
- DQ gates between zones: if P0 fails after raw, base doesn't run. Contract verification between zones.
- Run history logged to `governance/run-history/{timestamp}.json` for audit trail.
- Headless readiness check: `python -m brightsmith.run --headless-ready` verifies all specs complete, contracts valid, golden datasets pass, no LLM imports in zone code.
- Zone transformers register via `domain/manifest.yaml` under `pipeline.zones.{zone}.module` and `pipeline.zones.{zone}.function`.
- Silver/Gold specs involving temporal data MUST use PeriodDisambiguator (`src/brightsmith/infra/period_disambiguator.py`) for period classification rather than ad-hoc period logic. The framework utility handles annual vs quarterly vs point-in-time classification using date-span analysis.
- Every gold spec MUST have a golden dataset (`governance/golden-datasets/{spec}-golden.json`) with at least 3 independently verifiable values before @staff-engineer review
- MCP zone specs MUST include an evaluation set (`data/ai_ready/eval/{spec}-eval.json`) with at least 50 mechanically verifiable Q&A cases before @staff-engineer review
- Eval cases must span at least 5 categories: point lookup, comparison, ranking, trend, and edge case
- Every eval case must include: question, expected_answer, source_table, source_filters, source_column — so answers can be verified programmatically against consumable tables
- The eval set is a DQ artifact — @dq-engineer validates that all expected answers match pipeline output
- Every pipeline agent MUST be either executed or explicitly skipped with documented justification — silent omission is not allowed
- Skip justifications must reference a specific governance artifact (e.g., "domain-context.md PII section says 'No personal data expected'")
- Pipeline execution is tracked by `src/brightsmith/infra/pipeline_gate.py` — every spec gets a state file at `governance/pipeline-state/{spec}-pipeline.json`
- Before any agent runs: `python3 -m brightsmith.infra.pipeline_gate check {spec} {step}` — if BLOCKED, stop
- After any agent completes: `python3 -m brightsmith.infra.pipeline_gate complete {spec} {step} --output {path}`
- Before marking a spec COMPLETE: `python3 -m brightsmith.infra.pipeline_gate validate {spec}` must PASS
- Zone transitions require: `python3 -m brightsmith.infra.pipeline_gate check-transition {from} {to}` must PASS
- Never hardcode entity-specific data (CIK lists, fiscal year end months, company names, ticker symbols, sector mappings, entity counts) in Python source code. All entity-specific values must come from governance artifacts (`governance/entity-registry.json`, `domain/sources/*.yaml`, `governance/business-glossary.json`) or be derived from source data at runtime. Adding a new entity must never require a code change — only a config/registry update and pipeline re-run.
- Hardcoded entity patterns include: Python dicts keyed by CIK/ticker/entity name with literal values, if/elif chains that branch on entity identifiers, list literals containing specific entity IDs, and any constant that would need updating when a new entity is added. These are governance violations — entity data belongs in governance artifacts, not source code.
- The litmus test for entity hardcoding: "If a user adds a new entity to entity-registry.json and re-runs the pipeline, does the new entity flow through correctly without any code changes?" If the answer is no, the implementation violates this rule.
- Before a spec can be marked COMPLETE, the pipeline must have executed end-to-end into the persistent Iceberg warehouse producing queryable tables. "Tests pass" and "DQ rules pass against ephemeral data" are not sufficient — the actual pipeline entry points (registered in `domain/manifest.yaml`) must have run successfully, writing data to the project's warehouse at `data/`. The staff engineer must verify that tables exist in the catalog with expected row counts before approving.
- DQ rules must be executed against the persistent project warehouse (`data/` directory), not ephemeral or session-scoped catalogs. If the pipeline entry points haven't populated the warehouse yet (no tables exist), the DQ engineer must flag this as a blocker rather than building ad-hoc data loading. If it didn't write to the warehouse, it didn't happen.

## Human Approval Gates

When `REQUIRE_HUMAN_APPROVAL = True` (the default), certain pipeline steps require explicit human approval before proceeding. Every approval gate follows the same protocol:

### Protocol

1. **The producing agent** (e.g., @data-steward, @semantic-modeler, @dq-rule-writer) creates the artifact as usual
2. **@doc-generator is invoked** to produce a **Human Approval Document** — a plain-English markdown file that explains WHAT is being proposed, WHY, and what the human should look for
3. The approval document is saved to `governance/approvals/{spec}-{artifact-type}-approval.md`
4. The user is given the file path so they can review it (e.g., in Typora or their editor)
5. **AskUserQuestion is used** to collect the approval decision

### Collecting Approval via AskUserQuestion

After @doc-generator produces the approval document, use AskUserQuestion:

**Question:** "Review the {artifact type} approval document at `governance/approvals/{filename}`. What's your decision?"
**Options:**
- "Approved — looks good" → Mark artifact as APPROVED, proceed
- "Approved with notes" → User adds notes via free text, mark APPROVED, log notes in audit trail
- "Changes requested" → User specifies what to change (via free text), return artifact to producing agent for revision, re-run approval flow
- "Need more info — let me review the document first" → Pause pipeline, remind user of the file path, wait for them to come back

### When Multiple Artifacts Need Approval in Sequence

For Silver/Gold greenfield specs, approvals happen in order:
1. Business terms → approval document → AskUserQuestion
2. Conceptual model → approval document → AskUserQuestion
3. Logical model → approval document → AskUserQuestion

Each approval is independent. Rejection of an earlier artifact blocks later ones (e.g., rejecting business terms blocks conceptual model since it references those terms).

### Recording Approvals

Every approval decision is recorded in TWO places:

1. **Pipeline gate state file** via:
   ```bash
   python3 -m brightsmith.infra.pipeline_gate approve {spec} {artifact} --decision APPROVED --by human:{name} --notes "..." --document governance/approvals/{filename}
   ```

2. **Audit trail** at `governance/audit-trail/{spec}-approvals.md`:

| Artifact | Agent | Decision | Decided By | Date | Notes |
|----------|-------|----------|-----------|------|-------|
| Business Glossary (5 terms) | @data-steward | APPROVED | human:jeff | 2026-03-18 | "Looks right" |

### When REQUIRE_HUMAN_APPROVAL = False

All approval documents are STILL produced (they're useful documentation regardless). But instead of AskUserQuestion, the pipeline:
1. Writes the approval document
2. Auto-marks the artifact as APPROVED via pipeline gate
3. Logs "auto-approved (REQUIRE_HUMAN_APPROVAL=False)" in the audit trail
4. Proceeds without pausing

### Cross-Referencing

Every approval decision appears in THREE places:
1. `governance/pipeline-state/{spec}-pipeline.json` — the programmatic state (machine-readable)
2. `governance/audit-trail/{spec}-approvals.md` — the governance artifact (permanent, spec-scoped)
3. `docs/sessions/{session}-session.md` → Human Input Log — the chronological record (session-scoped)

All three must agree. If they don't, the session log is authoritative (it captures what actually happened in real-time).

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

## Human Input Log

Every piece of human input during this session is recorded here in chronological order. This includes initial prompts, mid-session messages, AskUserQuestion responses, approval decisions, and any corrections or redirections.

| Timestamp | Type | Context | Input |
|-----------|------|---------|-------|
| HH:MM | prompt | Session start | [exact text] |
| HH:MM | message | [what was happening] | [exact text] |
| HH:MM | ask-response | [question asked] | [option selected + any free-text notes] |
| HH:MM | approval | [artifact being approved] | [decision + notes] |
| HH:MM | correction | [what was corrected] | [exact text] |
| HH:MM | redirect | [what changed direction] | [exact text] |

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
- Every user message is logged in the Human Input Log — no exceptions, no paraphrasing
- AskUserQuestion responses are logged with BOTH the question that was asked AND the option/text selected
- Approval decisions are logged with the artifact path, the decision (APPROVED/CHANGES REQUESTED/etc.), and any notes
- If the user gives a vague or deferring answer ("handle it", "whatever you think", "idk"), log it EXACTLY as said — these are the most important entries because they explain why downstream assumptions were made
- If the user asks a follow-up question back to an agent, log both the question and the agent's response summary
- If the user corrects or redirects mid-pipeline ("wait, not that", "actually do X instead"), log it as type 'correction' or 'redirect'
- The Human Input Log is the AUTHORITATIVE record of human involvement. If an auditor asks "did a human approve this?", the answer is in the session log. If it's not logged, it didn't happen.
