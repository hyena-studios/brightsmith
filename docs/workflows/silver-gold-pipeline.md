# Silver & Gold Zone Pipeline

Silver and Gold zones include data modeling gates. The pipeline auto-detects **greenfield** vs **backfill** mode.

- **Greenfield** (tables don't exist yet): models are proposed BEFORE implementation
- **Backfill** (tables already exist): models are reverse-engineered FROM existing tables/code

## Greenfield Mode (new tables)

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

## Backfill Mode (existing tables, missing models)

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

## Mode Detection

@semantic-modeler determines the mode automatically:
- If the spec's target tables exist in the Iceberg catalog AND source code exists in `src/` → **backfill**
- If the spec's target tables do not exist → **greenfield**
- If a spec modifies existing tables (schema evolution) → **greenfield** for the new/changed parts

The human approval gates are controlled by `REQUIRE_HUMAN_APPROVAL` in `src/config.py`. When False (dev/demo mode), models auto-advance but all three artifacts are still produced in `governance/models/`.

Model artifacts are stored in `governance/models/` as `[spec-name]-conceptual.md`, `[spec-name]-logical.md`, `[spec-name]-physical.md`.

## Concept Normalization Step (Silver Zone, after @data-steward)

If `governance/domain-context.md` contains a "Canonical Concept Map" section with status CONFIRMED or PROPOSED:

1. @primary-agent generates concept mapping config files in `domain/concept-mappings/` from the domain context's concept map
2. @primary-agent creates a `base.concept_map` table using `ConceptNormalizer` that maps raw classification codes to canonical business concepts
3. The concept map table becomes a dimension that joins to the silver zone fact table (e.g., `base.financial_facts` or equivalent)
4. @dq-rule-writer writes coverage rules: what % of raw codes map to a canonical concept? (P1 rule, threshold from EDA)
5. Unmapped codes are preserved in the base table (no data loss) but flagged

This step is SKIPPABLE only if the @principal-data-architect explicitly approves skipping it at the zone transition review (e.g., the data has no classification codes to normalize).

## Silver/Gold-Specific Rules

- Silver/Gold tables require approved business terms → conceptual → logical → physical models before implementation
- Business terms from recognized external standards are auto-approved; project-specific terms require human approval
- Schema modifications to existing Silver/Gold tables with active contracts trigger @cab-agent review — classifies changes as PATCH/MINOR/MAJOR, maps blast radius, proposes fork for MAJOR changes. CAB decisions are stored at `governance/cab-decisions/` as structured JSON.
- When model files in `governance/models/` are created or modified, update the corresponding Mermaid diagrams in the "Data Models" section of `README.md` (all three levels: conceptual, logical, AND physical — full details live in governance/models/)
- When `governance/business-glossary.json` is modified, update the "Business Glossary" section of `README.md` (term counts, key terms tables)
- Data models store governance metadata as **IDs only** (`BT-XXX`) for business terms — never inline definitions. `is_cde` and `is_pii` are direct annotations on physical data elements (columns in contracts and models), not derived from business terms. Authoritative source for terms: `governance/business-glossary.json`. Authoritative source for CDE/PII flags: `governance/data-contracts/*.yaml`. Documentation (README) dereferences term IDs into human-readable names.
- All model levels include `Business Term`, `Is CDE`, `Is PII` columns: conceptual on entity tables, logical on attribute tables, physical on column tables. CDE and PII flags are direct annotations on the physical column, set by @cde-tagger on data contracts. Models mirror contract values.
- Concept normalization uses a tiered matching engine (exact → prefix → pattern → heuristic) with discovery mode when no mappings exist. Returns `NormalizationResult` with confidence scores (1.0=exact, 0.7=prefix, 0.6=pattern, 0.3=heuristic, 0.0=unmapped). Mappings below `CONFIDENCE_FLOOR` (0.7) require human approval.
- Collision resolution rules must be defined at `governance/concept-normalization/collision-rules.json` when multiple source codes map to the same canonical concept. @data-steward produces these; approval required before gold spec implementation.
- Business glossary terms require 11 fields per term (see @data-steward agent definition). Validate with `python3 -m brightsmith.infra.glossary_validator validate`. CDE/PII flags are not glossary fields — they live on physical data elements in data contracts.
- Silver/Gold specs involving temporal data MUST use PeriodDisambiguator (`src/brightsmith/infra/period_disambiguator.py`) for period classification rather than ad-hoc period logic. The framework utility handles annual vs quarterly vs point-in-time classification using date-span analysis.
- Every gold spec MUST have a golden dataset (`governance/golden-datasets/{spec}-golden.json`) with at least 3 independently verifiable values before @staff-engineer review
- Consumable and MCP zone tables MUST have a data contract at `governance/data-contracts/{table-name}.yaml`. @doc-generator generates it from the actual Iceberg table schema.
- Data contracts are machine-readable YAML with schema, quality, lineage, and consumer sections. Verify with `python3 -m brightsmith.infra.contract verify {name}`.
- Breaking schema changes (column removed/renamed/type changed/grain changed) require a major version bump. Non-breaking changes (column added) require minor bump.
- Contract lifecycle: DRAFT (generated) → ACTIVE (staff-engineer approved) → DEPRECATED (superseded). Active contracts are enforced on every pipeline run.
