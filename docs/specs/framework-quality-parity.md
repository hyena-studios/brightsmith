# Framework Spec: Governance & Quality Parity

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-19

## Context

Field-testing the Grist framework against a manually-built domain-specific pipeline (same data source, same domain) revealed systematic quality gaps. The gaps are not domain-specific — they're framework-level problems that every domain project inherits. A project built with Grist should produce governance, normalization, and verification quality comparable to a hand-built pipeline.

**Observed gaps (framework-caused, domain-agnostic):**

| Dimension | Hand-built | Grist-built | Root Cause |
|-----------|-----------|-------------|------------|
| DQ rules | 128 | 31 | Consumable zone DQ not enforced |
| Business glossary fields/term | 12 | 5 | Glossary schema too thin |
| Data models | 21 | 6 | No consumable model enforcement |
| Lineage events | 14 (runtime) | 2 (static) | No runtime capture |
| Test files | 71 | 1 | @setup scaffolds minimal tests |
| Verification checks | 88 | 0 | No verification tooling exists |
| Normalization tiers | 4 | 2 | No confidence scoring, no collision resolution |
| Golden datasets | Yes | Empty | No tooling to create/verify/enforce |

## Root Causes

1. **ConceptNormalizer returns bare strings, not scored proposals.** Domain projects bypass it because there's no confidence metadata to drive approval gates or DQ rules.

2. **No collision resolution governance artifact.** When multiple source classifications map to the same canonical concept, there's no framework mechanism to declare which is primary. First-encountered wins silently.

3. **Consumable DQ rules are skippable.** The pipeline gate allows `@dq-rule-writer` to be marked complete without producing rules when fast-tracked.

4. **Business glossary schema lacks relationship metadata.** No synonyms, related terms, or model usage tracking — preventing impact analysis and stewardship. (Note: CDE/PII flags have been moved from the glossary to physical data elements on data contracts per the cde-pii-governance-refactor spec.)

5. **Lineage is static templates, not runtime capture.** No actual snapshot IDs, row counts, DQ metrics, or agent attribution.

6. **Golden dataset tooling doesn't exist.** CLAUDE.md mandates them but there's no CLI, no format spec, and no pipeline gate check.

7. **No verification framework.** DQ rules validate structural properties but can't answer "is this number actually correct?"

8. **@setup scaffolds almost no tests.** Projects ship with 1 test file covering schema validation only.

---

## Change 1: Confidence Scoring in ConceptNormalizer

**Files:**
- `src/grist/base/concept_normalization/normalize.py`
- `src/grist/base/concept_normalization/config.py`

**Problem:** The framework's ConceptNormalizer returns bare concept names. Domain projects need confidence scores to drive approval gates, prioritize manual review, and produce DQ rules that validate mapping quality.

**Changes:**

### 1a. `NormalizationResult` dataclass

Every normalization call returns a structured result:

```python
@dataclass
class NormalizationResult:
    source_key: str           # the raw classification code/tag/identifier
    canonical_concept: str | None
    business_term_id: str | None
    tier: int                 # 1=exact, 2=prefix, 3=pattern, 4=heuristic, 0=unmapped
    confidence: float         # 1.0, 0.7, 0.6, 0.3, 0.0
    match_method: str         # "exact_match", "prefix_match", "pattern_match", "heuristic", "unmapped"
    matched_rule: str | None  # which rule triggered the match
    requires_approval: bool   # True if confidence < CONFIDENCE_FLOOR
```

This is domain-agnostic — works for XBRL tags, ICD-10 codes, product SKUs, or any classification system.

### 1b. Confidence floor enforcement

Add `CONFIDENCE_FLOOR = 0.7` to `grist/config.py`. Mappings below this floor:
- Set `requires_approval = True`
- Cannot be promoted to canonical status without human approval (respects `REQUIRE_HUMAN_APPROVAL`)
- Are flagged in the discovery output

### 1c. Tier-specific confidence assignment

| Tier | Method | Confidence | Requires Approval |
|------|--------|-----------|-------------------|
| 1 | Exact match (curated mapping dict) | 1.0 | No |
| 2 | Prefix/key match (pattern rules) | 0.7 | No (at floor) |
| 3 | Regex/pattern match | 0.6 | Yes |
| 4 | Heuristic/category fallback | 0.3 | Yes |
| 0 | Unmapped | 0.0 | N/A |

### 1d. DQ rule template for normalization

Add to `governance/dq-rule-templates/normalization-patterns.json`:

```json
[
  {
    "pattern_id": "NORM-CONFIDENCE-RANGE",
    "category": "Validity",
    "priority": "P0",
    "description": "All confidence scores must be in [0.0, 1.0]",
    "sql_template": "SELECT COUNT(*) FROM {table} WHERE confidence < 0.0 OR confidence > 1.0",
    "threshold": "result = 0"
  },
  {
    "pattern_id": "NORM-COVERAGE-FLOOR",
    "category": "Coverage",
    "priority": "P1",
    "description": "Mapped concepts must cover >= {coverage_pct}% of source rows",
    "sql_template": "SELECT CAST(COUNT(*) FILTER (WHERE canonical_concept IS NOT NULL) AS DOUBLE) * 100.0 / COUNT(*) FROM {table}",
    "threshold": "result >= {coverage_pct}"
  },
  {
    "pattern_id": "NORM-TIER1-APPROVED",
    "category": "Completeness",
    "priority": "P0",
    "description": "All Tier 1 (exact match) concepts must have an approved business term",
    "sql_template": "SELECT COUNT(*) FROM {table} WHERE tier = 1 AND (business_term_id IS NULL OR approval_status != 'approved')",
    "threshold": "result = 0"
  }
]
```

### 1e. Tests

- `tests/base/test_concept_normalization.py`:
  - `test_exact_match_returns_confidence_1_0`
  - `test_prefix_match_returns_confidence_0_7`
  - `test_unmapped_returns_confidence_0_0`
  - `test_below_floor_requires_approval`
  - `test_result_dataclass_fields_complete`

---

## Change 2: Collision Resolution Governance Artifact

**Files:**
- `src/grist/base/concept_normalization/collision.py` (new)
- `.claude/agents/data-steward.md` (update)
- `governance/dq-rule-templates/collision-patterns.json` (new)

**Problem:** When multiple source classifications map to the same canonical concept, there's no mechanism to declare which is primary. This is domain-agnostic: it happens with ICD-10 codes (multiple codes → same condition), product taxonomies (multiple SKUs → same product category), and any classification system with synonyms or versioned codes.

**Changes:**

### 2a. Standard artifact format

Define `governance/concept-normalization/collision-rules.json`:

```json
{
  "version": "1.0",
  "description": "Primary concept preference rules for collision resolution",
  "rules": {
    "<canonical_concept>": {
      "primary_sources": ["<preferred_code_1>", "<preferred_code_2>", "..."],
      "primary_unit": "<unit_if_applicable>",
      "resolution_strategy": "prefer_primary_order",
      "rationale": "Why this ordering was chosen",
      "approved_by": null,
      "approved_at": null
    }
  }
}
```

The `primary_sources` array is ordered: first match wins. If entity reports the first code, use it. Otherwise fall back to second, etc.

### 2b. `@data-steward` produces collision rules

Update agent definition: after concept normalization discovery, `@data-steward` must:
- Identify all canonical concepts with >1 source mapping
- Propose a primary ordering based on frequency (most common source first)
- Produce collision rules artifact
- Gate: collision rules must be approved before consumable spec implementation

### 2c. Pipeline gate check

At the base→consumable transition, verify:
- `governance/concept-normalization/collision-rules.json` exists
- Every canonical concept with >1 source tag has a collision rule
- All rules have `approved_by` set (or auto-approved if `REQUIRE_HUMAN_APPROVAL=false`)

### 2d. Collision DQ rule template

Add to `governance/dq-rule-templates/collision-patterns.json`:

```json
[
  {
    "pattern_id": "COLLISION-UNIQUE-PER-GRAIN",
    "category": "Uniqueness",
    "priority": "P0",
    "description": "After collision resolution, each canonical concept must appear at most once per entity-period grain",
    "sql_template": "SELECT COUNT(*) FROM (SELECT {entity_id}, canonical_concept, {period_fields}, COUNT(*) AS cnt FROM {table} GROUP BY {entity_id}, canonical_concept, {period_fields} HAVING cnt > 1)",
    "threshold": "result = 0"
  },
  {
    "pattern_id": "COLLISION-RULES-APPLIED",
    "category": "Consistency",
    "priority": "P1",
    "description": "Source tags used in conformed data should match primary_sources ordering from collision rules",
    "note": "Implemented as a programmatic check, not pure SQL"
  }
]
```

### 2e. Tests

- `tests/base/test_collision_resolution.py`:
  - `test_primary_concept_wins_over_secondary`
  - `test_fallback_to_secondary_when_primary_absent`
  - `test_collision_rules_cover_all_multi_source_concepts`
  - `test_resolution_produces_unique_grain`

---

## Change 3: Consumable Zone DQ Enforcement

**Files:**
- `governance/dq-rule-templates/consumable-patterns.json` (update)
- `src/grist/infra/pipeline_gate.py` (update)
- `.claude/agents/dq-rule-writer.md` (update)

**Problem:** Consumable specs can be fast-tracked with zero DQ rules. The pipeline gate allows `@dq-rule-writer` completion without validating that rules were actually produced.

**Changes:**

### 3a. Pipeline gate: require DQ rules file exists

In `pipeline_gate.py`, add a validation check for consumable and ai_ready zones: when `@dq-rule-writer` is marked complete, verify that `governance/dq-rules/{spec}.json` exists AND contains at least 1 rule. Reject completion otherwise.

### 3b. Mandatory consumable DQ patterns (6 total)

Update `governance/dq-rule-templates/consumable-patterns.json`:

| Pattern | Priority | Description | Skippable? |
|---------|----------|-------------|------------|
| CONS-GRAIN-UNIQUE | P0 | One value per declared grain | No |
| CONS-IMPOSSIBLE-VALUE | P0 | Values within domain constraints | No |
| CONS-GOLDEN-DATASET | P0 | Output matches golden dataset values | No |
| CONS-COLLISION-RESOLVED | P0 | No duplicate concepts per entity-period | No |
| CONS-CROSS-TABLE | P1 | Cross-table referential integrity | Yes (document why) |
| CONS-COVERAGE-FLOOR | P1 | Mapped concepts cover >= threshold of base rows | Yes (document why) |

### 3c. `@dq-rule-writer` pattern evaluation is mandatory

Update agent definition: `@dq-rule-writer` MUST read `governance/dq-rule-templates/consumable-patterns.json` before writing consumable rules. For each pattern, either write a rule OR document in the audit trail why it doesn't apply (with pattern ID reference). The audit entry must be verified by `@governance-reviewer`.

---

## Change 4: Business Glossary Schema Enrichment

**Files:**
- `.claude/agents/data-steward.md` (update)
- `src/grist/infra/glossary_loader.py` (update)

**Problem:** 5 fields per term is insufficient for impact analysis, stewardship tracking, and cross-referencing. Every domain project produces a thin glossary.

**Changes:**

### 4a. Required fields per business term

> **SUPERSEDED:** The `is_cde`, `cde_rationale`, `is_pii`, and `pii_rationale` fields have been moved from the business glossary to physical data elements on data contracts. See `docs/specs/cde-pii-governance-refactor.md`. The glossary now has 11 required fields (pure semantic definitions).

```json
{
  "term_id": "BT-001",
  "name": "...",
  "definition": "...",
  "source": "external-standard | domain-standard | project-specific",
  "source_reference": "URL or document citation",
  "synonyms": ["alias1", "alias2"],
  "related_terms": ["BT-002", "BT-005"],
  "category": "entity | classification | measurement | temporal | regulatory | derived",
  "owner": "Data Governance | domain-specific role",
  "used_in_models": ["model-name-1", "model-name-2"],
  "approval_status": "proposed | approved | auto-approved"
}
```

### 4b. Glossary validation module

Add `src/grist/infra/glossary_validator.py`:
- Validate all required fields present (11 fields)
- `related_terms` reference valid term IDs within the glossary
- `used_in_models` reference files that exist in `governance/models/`
- CLI: `python -m grist.infra.glossary_validator validate`

### 4c. Tests

- `tests/infra/test_glossary_validator.py`:
  - `test_missing_required_field_fails`
  - `test_invalid_related_term_ref_fails`
  - `test_term_without_cde_pii_fields_passes`
  - `test_valid_glossary_passes`

---

## Change 5: Runtime Lineage

**Files:**
- `src/grist/infra/lineage.py` (update)
- `src/grist/raw/base_ingestor.py` (update)

**Problem:** Lineage events are static templates with estimated values. No runtime metadata.

**Changes:**

### 5a. `emit_lineage_event()` function

Add to `src/grist/infra/lineage.py`:

```python
def emit_lineage_event(
    job_name: str,
    spec_file: str,
    agent_id: str,
    inputs: list[dict],
    outputs: list[dict],
    row_count: int,
    snapshot_id: int | None = None,
    dq_passed: int | None = None,
    dq_total: int | None = None,
    duration_ms: int | None = None,
) -> Path:
    """Emit an OpenLineage event with runtime metadata. Returns path to event file."""
```

### 5b. Auto-emit in `BaseIngestor.ingest()`

After `ingest()` completes, call `emit_lineage_event()` with actual results. Domain projects get lineage for free — no agent needed for raw zone.

### 5c. `@lineage-tracker` becomes a verifier

Update agent definition: instead of writing lineage events, verify:
- Every spec's transformation has at least one lineage event
- Events have non-zero row counts
- Events reference valid spec files
- Input/output table names match the spec

### 5d. Tests

- `tests/infra/test_lineage.py`:
  - `test_emit_creates_valid_openlineage_event`
  - `test_event_contains_runtime_metrics`
  - `test_event_references_spec_file`

---

## Change 6: Golden Dataset Tooling

**Files:**
- `src/grist/infra/golden_dataset.py` (new)
- `src/grist/infra/pipeline_gate.py` (update)

**Problem:** Golden datasets are mandated but unenforced. No tooling exists.

**Changes:**

### 6a. Golden dataset format (domain-agnostic)

```json
{
  "spec": "<spec-name>",
  "table": "<namespace.table>",
  "created_at": "ISO-8601",
  "created_by": "<agent or human>",
  "source_description": "How these expected values were determined",
  "values": [
    {
      "description": "Human-readable description of what this checks",
      "filters": {"<column>": "<value>", "...": "..."},
      "column": "<column_to_check>",
      "expected_value": 12345,
      "tolerance_pct": 1.0,
      "source": "External reference or manual calculation"
    }
  ]
}
```

The `filters` dict is applied as WHERE clauses. The `column` value is compared to `expected_value` with the given tolerance. Works for any domain — financial data, healthcare claims, IoT readings, anything.

### 6b. CLI

```
python -m grist.infra.golden_dataset verify --spec {spec}    # Verify against Iceberg
python -m grist.infra.golden_dataset list                    # List all golden datasets
python -m grist.infra.golden_dataset summary                 # Pass/fail summary
```

### 6c. Pipeline gate enforcement

Before `@staff-engineer` can review a consumable spec:
- `governance/golden-datasets/{spec}-golden.json` must exist
- Must contain at least 3 values
- `python -m grist.infra.golden_dataset verify --spec {spec}` must pass

### 6d. Tests

- `tests/infra/test_golden_dataset.py`:
  - `test_verify_matching_value_passes`
  - `test_verify_within_tolerance_passes`
  - `test_verify_outside_tolerance_fails`
  - `test_missing_golden_dataset_detected`
  - `test_filter_application_correct`

---

## Change 7: Verification Framework

**Files:**
- `src/grist/infra/verification.py` (new)

**Problem:** DQ rules validate structural properties. Verification validates correctness: "is this number actually right?" This is the gap between "the column isn't null" and "the value matches the real world."

**Changes:**

### 7a. Verification runner

Wraps golden dataset verification with reporting:

```
python -m grist.infra.verification run [--spec SPEC] [--tolerance 1.0]
```

Output:
```
[MATCH]     value_1: 12345 vs 12345 (0.00% diff)
[CLOSE]     value_2: 12400 vs 12345 (0.45% diff)
[MISMATCH]  value_3: 15000 vs 12345 (21.5% diff)

Results: 2 pass, 0 close, 1 mismatch
Pass rate: 66.7% (threshold: 80%)
FAIL
```

### 7b. Tolerance levels

| Status | Diff | Meaning |
|--------|------|---------|
| MATCH | < 1% | Rounding differences only |
| CLOSE | 1-5% | Acceptable variance (e.g., different period interpretation) |
| MISMATCH | > 5% | Likely error |

### 7c. Pipeline gate integration

AI-Ready zone `@staff-engineer` review requires `python -m grist.infra.verification run` pass rate >= 80%.

---

## Change 8: Test Scaffolding in @setup

**Files:**
- `src/grist/setup.py` (update)
- `.claude/agents/setup.md` (update)

**Problem:** @setup scaffolds 1 test file. Projects ship untested.

**Changes:**

### 8a. Scaffold test directories per zone

`@setup` creates empty test modules with docstrings explaining what tests belong there:

```
tests/
├── raw/
│   ├── __init__.py
│   └── test_{source}_ingestor.py     # Schema + flatten (existing)
├── base/
│   ├── __init__.py
│   └── test_transformer.py           # Skeleton: supersession, normalization, temporal
├── consumable/
│   ├── __init__.py
│   └── test_transformer.py           # Skeleton: pivoting, aggregation, derivation
├── ai_ready/
│   ├── __init__.py
│   └── test_tools.py                 # Skeleton: each tool function
├── integration/
│   ├── __init__.py
│   └── test_golden_datasets.py       # Skeleton: verify golden datasets
└── conftest.py
```

### 8b. Minimum test targets in CLAUDE.md

Add rule: `@staff-engineer` verifies minimum test counts per zone before approving.

| Zone | Minimum | What They Must Validate |
|------|---------|------------------------|
| Raw | 10 | Schema correctness, flatten logic, fetch error handling, dedup |
| Base | 15 | Supersession, normalization confidence, collision resolution, temporal type |
| Consumable | 15 | Grain uniqueness, aggregation correctness, derived value computation, golden dataset match |
| AI-Ready | 10 | Each tool returns valid structure, handles missing data, handles unknown entities |
| Integration | 5 | End-to-end row counts, golden dataset verification |

These are domain-agnostic requirements — they describe WHAT to test (supersession, normalization, derivation) not domain-specific values.

### 8c. `@staff-engineer` enforcement

Update agent definition: if a zone has fewer tests than the minimum, issue CHANGES REQUESTED. No exceptions.

---

## Change 9: Data Model Coverage Enforcement

**Files:**
- `.claude/agents/semantic-modeler.md` (update)
- `src/grist/infra/pipeline_gate.py` (update)

**Problem:** Consumable zone produces tables with no corresponding data models.

**Changes:**

### 9a. Pipeline gate: model file must exist

For consumable greenfield specs, `semantic-modeler-physical` cannot be marked complete unless `governance/models/{spec}-physical.md` exists.

### 9b. Model-implementation consistency

`@governance-reviewer` post-implementation checklist: verify the implemented schema matches the approved physical model columns. Mismatches → CHANGES REQUESTED.

---

## Implementation Order

| Phase | Changes | Impact |
|-------|---------|--------|
| 1 | Change 1 (confidence scoring) + Change 2 (collision resolution) | Normalization quality |
| 2 | Change 6 (golden datasets) + Change 7 (verification) | Correctness assurance |
| 3 | Change 3 (consumable DQ enforcement) + Change 4 (glossary enrichment) | Governance depth |
| 4 | Change 5 (runtime lineage) | Traceability |
| 5 | Change 8 (test scaffolding) + Change 9 (model enforcement) | Structural quality |

## Acceptance Criteria

A new domain project scaffolded by `@setup` and run through the full pipeline should produce:
- Concept normalization with confidence scores per mapping and collision resolution rules
- Golden datasets verified against independently sourced reference values
- DQ rules for every consumable table, with all 6 mandatory patterns evaluated
- Business glossary with 14 fields per term including relationship metadata
- Runtime lineage with actual row counts, snapshot IDs, and DQ metrics
- 55+ tests across all zones (domain-agnostic structure, domain-specific assertions)
- 3-stage data models for every table across all zones
- Verification pass rate >= 80%

The framework enforces the structure and minimums. Domain projects fill in the domain-specific content.
