# Spec: CDE/PII Governance Refactor — Physical Element Flags Over Glossary Decoration

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** N/A (framework change)
**Created:** 2026-03-26

## Problem Statement

CDEs (Critical Data Elements) and PII flags are currently attached to business terms in the glossary, but criticality and sensitivity are properties of **where data physically lives**, not of the abstract concept it represents. The term "Revenue" is a semantic definition; whether a specific `revenue` column in `consumable.company_financials` is critical to regulatory reporting depends on the table's downstream consumers, not on the abstract definition of revenue.

Three specific problems:

1. **Dual naming system.** The CDE catalog (`governance/cde-catalog.json`) maintains its own IDs (`CDE-001`), names, definitions, and categories — duplicating what business terms already do. The `@cde-tagger` agent creates and maintains this parallel semantic system instead of simply flagging physical columns as critical.

2. **Context-free flags on glossary terms.** `is_cde`, `cde_rationale`, `is_pii`, `pii_rationale`, and `cde_reference` live on business terms in the glossary. But the same business term (e.g., "Revenue") could be a CDE in one table and not in another. The glossary cannot express "Revenue is a CDE in the gold table but not in bronze."

3. **Industry misalignment.** BCBS 239 defines CDEs as physical data elements critical to risk reporting and management decisions. DAMA-DMBOK ties CDEs to business process analysis at the physical level. Our current design treats CDEs as semantic classifications, which contradicts both standards.

## How It Works

### Before (Current State)

```
Business Glossary (governance/business-glossary.json)
  +-- BT-001: Revenue
       +-- is_cde: true                    <-- flag on abstract concept
       +-- cde_rationale: "Regulatory..."
       +-- is_pii: false
       +-- cde_reference: "CDE-001"        <-- cross-ref to parallel catalog
       +-- ... (14 required fields total)

CDE Catalog (governance/cde-catalog.json)  <-- REDUNDANT parallel system
  +-- CDE-001: Revenue
       +-- name, definition, category       <-- DUPLICATES glossary
       +-- mappings: [{table, field, ...}]

Data Contract (governance/data-contracts/*.yaml)
  +-- columns:
       +-- revenue:
            +-- business_term: BT-001
            +-- is_cde: true               <-- DERIVED from glossary lookup

Physical Model (governance/models/*-physical.md)
  +-- Is CDE, Is PII columns              <-- DERIVED from business term
```

### After (Target State)

```
Business Glossary (governance/business-glossary.json)
  +-- BT-001: Revenue
       +-- definition, source, synonyms...  <-- pure semantics
       +-- (11 required fields, NO governance flags)

CDE Catalog: DELETED

Data Contract (governance/data-contracts/*.yaml)
  +-- columns:
       +-- revenue:
            +-- business_term: BT-001       <-- semantic link (unchanged)
            +-- is_cde: true                <-- SET DIRECTLY on column
            +-- cde_rationale: "Feeds..."   <-- SET DIRECTLY on column
            +-- is_pii: false               <-- SET DIRECTLY on column
            +-- pii_rationale: ""           <-- SET DIRECTLY on column

Physical Model (governance/models/*-physical.md)
  +-- Is CDE, Is PII columns              <-- DIRECT annotation, mirrors contract
```

The key shift: **CDE/PII are properties of physical data elements (columns in contracts and models), not of abstract business terms.** The "CDE list" becomes a query: all columns across all contracts where `is_cde = true`.

### Industry Alignment

| Standard | What It Says | How We Align |
|----------|-------------|--------------|
| BCBS 239 | CDEs are physical data elements critical to risk reporting and management decisions | CDE flag lives on physical columns in contracts |
| DAMA-DMBOK | CDEs are identified through business process analysis at the physical level | @cde-tagger evaluates downstream criticality per column |
| EDM Council / DCAM | CDE identification tied to stakeholder analysis and business criticality | Rationale captures the "why" per column instance |

### No Backward Propagation

Marking a gold column as CDE does not propagate to bronze or silver. Each layer's CDE flags are independent. Lineage (not CDE flags) traces data origin across layers.

### Where CDEs Can Exist

CDEs exist wherever business terms are applied — a consequence, not a layer-specific rule. In practice this means silver and above, since bronze has no business terms. If a future edge case requires business terms at bronze, CDEs could follow without a rule change.

## Success Criteria

- [ ] `ColumnContract` dataclass has `is_cde`, `cde_rationale`, `is_pii`, `pii_rationale` as direct fields
- [ ] Contract generation (`generate_contract()`) no longer cross-references glossary for CDE/PII flags
- [ ] `GlossaryTerm` dataclass has no `is_cde` or `is_pii` fields
- [ ] `REQUIRED_FIELDS` in glossary_validator drops to 11 (removing `is_cde`, `is_pii`)
- [ ] CDE/PII rationale validation logic removed from glossary_validator
- [ ] `@cde-tagger` agent flags columns directly in data contracts, does not maintain `governance/cde-catalog.json`
- [ ] `@data-steward` glossary term schema has 11 required fields (removing `is_cde`, `cde_rationale`, `is_pii`, `pii_rationale`, `cde_reference`)
- [ ] No reference to `governance/cde-catalog.json` anywhere in the codebase
- [ ] No reference to `cde_reference` field anywhere in the codebase
- [ ] All existing tests updated and passing
- [ ] CLAUDE.md rules updated to reflect new field counts and governance model
- [ ] All `.claude/agents/` changes mirrored to `agents/`

## Technical Design

### 1. ColumnContract Dataclass Enhancement

**File:** `src/brightsmith/infra/contract.py` (line 70-79)

Current:
```python
@dataclass
class ColumnContract:
    name: str
    type: str
    required: bool
    business_term: str | None = None
    is_cde: bool = False
    description: str = ""
```

New:
```python
@dataclass
class ColumnContract:
    name: str
    type: str
    required: bool
    business_term: str | None = None
    is_cde: bool = False
    cde_rationale: str = ""
    is_pii: bool = False
    pii_rationale: str = ""
    description: str = ""
```

### 2. Contract Generation — Remove Glossary CDE/PII Derivation

**File:** `src/brightsmith/infra/contract.py` (lines 255-280)

**Column dict template** (line 255-262) — add new fields, default to empty:
```python
columns.append({
    "name": iceberg_field.name,
    "type": _iceberg_type_to_str(iceberg_field.field_type),
    "required": iceberg_field.required,
    "business_term": None,
    "is_cde": False,
    "cde_rationale": "",
    "is_pii": False,
    "pii_rationale": "",
    "description": "",
})
```

**Glossary cross-reference** (lines 266-280) — keep business_term lookup, remove CDE derivation:
```python
# Cross-reference business glossary for term IDs (semantic link only)
for col in columns:
    match = term_lookup.get(col["name"].lower())
    if match:
        col["business_term"] = match.get("term_id")
        # REMOVED: col["is_cde"] = match.get("is_cde", False)
        # CDE/PII flags are set by @cde-tagger, not derived from glossary
```

### 3. Glossary Validator — Remove CDE/PII Fields

**File:** `src/brightsmith/infra/glossary_validator.py`

**REQUIRED_FIELDS** (line 20-25) — remove `is_cde`, `is_pii`:
```python
REQUIRED_FIELDS = {
    "term_id", "name", "definition", "source", "source_reference",
    "synonyms", "related_terms", "category", "owner",
    "used_in_models", "approval_status",
}
```

**Remove validation blocks** (lines 84-92):
- Delete: CDE rationale check (`if term.get("is_cde") is True` block)
- Delete: PII rationale check (`if term.get("is_pii") is True` block)

**Update module docstring** (line 3): Remove "and consistent CDE/PII rationale"

### 4. Glossary Loader — Remove CDE/PII from GlossaryTerm

**File:** `src/brightsmith/infra/glossary_loader.py`

**GlossaryTerm dataclass** (lines 32-48) — remove `is_cde` and `is_pii` fields:
```python
@dataclass
class GlossaryTerm:
    term_id: str
    term: str
    definition: str
    source: str
    source_tier: int
    upstream_term_id: str | None
    read_only: bool
    category: str | None = None
    synonyms: list[str] = field(default_factory=list)
    related_terms: list[str] = field(default_factory=list)
    status: str = "approved"
```

**load_standard_glossary()** (lines 156-157) — remove `is_cde` and `is_pii` from constructor call

**load_project_glossary()** (lines 198-199) — remove `is_cde` and `is_pii` from constructor call

### 5. CAB Module — Update CDE Change Description

**File:** `src/brightsmith/infra/cab.py` (line 57)

No code change needed — `CDE_CHANGED` enum value is still valid. The detection source shifts from glossary diffs to contract column diffs, which is already where `diff_contract()` operates.

Update the agent definition wording only (see Section 7b).

### 6. Agent Definition Changes — Major Rewrites

#### 6a. @cde-tagger (`.claude/agents/cde-tagger.md`)

**Full rewrite.** New responsibilities:
- Read `governance/domain-context.md` for domain understanding (unchanged)
- Read data contracts at `governance/data-contracts/*.yaml` for column lists
- For each column in silver+ contracts: determine if it is a CDE based on downstream criticality, regulatory requirements, and business process importance
- Set `is_cde: true/false` and `cde_rationale` directly on columns in contract YAML
- Set `is_pii: true/false` and `pii_rationale` where applicable
- Produce a tagging report per spec listing flagged columns with rationale

**Removed:**
- All references to `governance/cde-catalog.json`
- CDE IDs (`CDE-001`), CDE names, CDE definitions, CDE categories
- The entire CDE catalog JSON structure
- Any "mapping" concept — this is flagging, not mapping

**Key Paths change:**
- Remove: `governance/cde-catalog.json | Read/Write`
- Add: `governance/data-contracts/ | Read/Write — flag columns with CDE/PII`

#### 6b. @data-steward (`.claude/agents/data-steward.md`)

**Glossary term JSON example** (lines 23-39) — remove `cde_reference` field:
```json
{
  "term_id": "BT-001",
  "term": "Term Name",
  "definition": "Plain-English definition of the business concept.",
  "source": "external-standard | domain-standard | project-specific",
  "source_reference": "Reference to authoritative source (if external)",
  "synonyms": ["Alias 1", "Alias 2"],
  "related_terms": ["BT-002", "BT-003"],
  "category": "domain category",
  "owner": "Ownership area",
  "used_in_models": ["spec-name-1", "spec-name-2"],
  "approval_status": "approved | proposed | auto-approved"
}
```

**Responsibilities** (line 59) — remove #4: "Map terms to CDEs — where a business term corresponds to a CDE, link them via `cde_reference`"

**Required Glossary Fields table** (lines 107-124) — remove rows:
- `is_cde` (Always, Boolean)
- `cde_rationale` (When is_cde=true)
- `is_pii` (Always, Boolean)
- `pii_rationale` (When is_pii=true)

Result: 11 required fields (term_id, name, definition, source, source_reference, synonyms, related_terms, category, owner, used_in_models, approval_status)

**Key Paths** (line 158) — remove: `governance/cde-catalog.json | Read — cross-reference CDEs`

#### 6c. @semantic-modeler (`.claude/agents/semantic-modeler.md`)

**Model cross-reference section** (line 80) — change from:
> "The authoritative definitions live in `governance/business-glossary.json` (business terms) and `governance/cde-catalog.json` (CDEs)."

To:
> "The authoritative definitions for business terms live in `governance/business-glossary.json`. CDE and PII flags are direct annotations on physical columns, set by @cde-tagger on data contracts at `governance/data-contracts/`. Models mirror contract values."

**CDE/PII derivation rule** — change from "derived from the referenced business term" to "direct annotation on the physical column, mirroring data contract values."

#### 6d. @governance-reviewer (`.claude/agents/governance-reviewer.md`)

**Post-implementation checklist** (line 81) — change:
- From: `CDE Tags: New or modified fields are tagged in governance/cde-catalog.json`
- To: `CDE/PII Tags: New or modified fields have is_cde/is_pii flags set in their data contracts`

**Key Paths** (line 165) — change:
- From: `governance/cde-catalog.json | Read — verify CDE tags exist`
- To: `governance/data-contracts/ | Read — verify CDE/PII flags set on columns`

#### 6e. @doc-generator (`.claude/agents/doc-generator.md`)

**Data dictionary field format** (line 41) — change:
- From: `"cde_reference": "CDE-001 (Name)"`
- To: `"is_cde": true, "cde_rationale": "Feeds quarterly regulatory filing"`

**"What To Look For" section** — move CDE/PII review from business term review to physical model/contract review:
- "Are the right columns flagged as CDE?"
- "Are the right columns flagged as PII?"
- "Do the rationales explain WHY, not just repeat the flag?"

**Key Paths** (line 225) — remove: `governance/cde-catalog.json | Read — cross-reference CDE tags`

#### 6f. @insight-manager (`.claude/agents/insight-manager.md`)

**Line 160** — change "CDE catalog" reference to "data contracts"

**Key Paths** (line 190) — change:
- From: `governance/cde-catalog.json | Read | Understand mapped CDEs`
- To: `governance/data-contracts/ | Read | CDE/PII flags on columns`

### 7. Agent Definition Changes — Minor Updates

Only one of these 12 agents needs a real wording change:

**@cab-agent** (`.claude/agents/cab-agent.md`, line 46):
- From: `CDE mapping altered`
- To: `CDE flag changed on active contract column`

The remaining 11 agents use "CDE tags" in scope boundary statements (e.g., "don't create CDE tags"). These are still correct — the concept of CDE tagging still exists, the mechanism changed. **No changes needed** for: domain-context, lineage-tracker, mcp-engineer, temporal-modeler, setup, policy-engineer, pii-scanner, entity-resolver, dq-rule-writer, dq-engineer, data-analyst.

### 8. CLAUDE.md Rule Updates

**File:** `CLAUDE.md`

**Key Paths section** — remove `governance/cde-catalog.json` line if present; ensure no reference to it remains

**Line 186** (ID storage rule) — change to:
> Data models store governance metadata as **IDs only** (`BT-XXX`) for business terms — never inline definitions. `is_cde` and `is_pii` are direct annotations on physical data elements (columns in contracts and models), not derived from business terms. Authoritative source for terms: `governance/business-glossary.json`. Authoritative source for CDE/PII flags: `governance/data-contracts/*.yaml`.

**Line 187** (model columns rule) — change to:
> All model levels include `Business Term`, `Is CDE`, `Is PII` columns: conceptual on entity tables, logical on attribute tables, physical on column tables. CDE and PII flags are direct annotations on the physical column, set by @cde-tagger on data contracts. Models mirror contract values.

**Line 196** (glossary fields rule) — change to:
> Business glossary terms require 11 fields per term (see @data-steward agent definition). Validate with `python3 -m brightsmith.infra.glossary_validator validate`. CDE/PII flags are not glossary fields — they live on physical data elements in data contracts.

### 9. Existing Spec Updates

**`docs/specs/data-contracts.md`** — update contract schema example (lines 88-98):
```yaml
columns:
  - name: cik
    type: integer
    required: true
    business_term: BT-001
    is_cde: true
    cde_rationale: "Entity identifier required for all regulatory filings per BCBS 239"
    is_pii: false
    pii_rationale: ""
    description: Entity identifier
```

Update contract generation section (lines 139-169): note that generator no longer cross-references glossary for CDE/PII. Those are set by @cde-tagger post-generation.

**`docs/specs/framework-gap-closure.md`** (line 274) — change cde-tagger output from `governance/cde-catalog.json` to `governance/data-contracts/*.yaml`

**`docs/specs/framework-quality-parity.md`** — update any references to glossary CDE rationale gaps (the gap is resolved differently now — CDE rationale lives on contracts, not glossary)

## Tests

### Tests to Modify

| File | Change | Details |
|------|--------|---------|
| `tests/infra/test_glossary_validator.py` | Remove 3 tests | `test_cde_without_rationale_fails`, `test_pii_without_rationale_fails`, `test_cde_with_rationale_passes` |
| `tests/infra/test_glossary_validator.py` | Update helper | Remove `is_cde`, `is_pii` from `_make_valid_term()` |
| `tests/infra/test_glossary_validator.py` | Add 1 test | `test_term_without_cde_pii_fields_passes` (positive case for new schema) |
| `tests/infra/test_glossary_loader.py` | Update fixtures | Remove `is_cde`, `is_pii` from all term JSON fixtures |
| `tests/infra/test_glossary_loader.py` | Update assertions | Remove `is_cde`/`is_pii` attribute checks on `GlossaryTerm` |
| `tests/infra/test_contract.py` | Update helper | Add `cde_rationale`, `is_pii`, `pii_rationale` to `_make_contract()` column dicts |
| `tests/infra/test_contract.py` | Add 2 tests | `test_column_contract_cde_pii_fields` (dataclass has all 4 fields), `test_contract_cde_pii_roundtrip` (save/load preserves values) |
| `tests/infra/test_contract.py` | Add 1 test | `test_contract_generation_no_glossary_cde_derivation` (glossary with is_cde=true does NOT set is_cde on generated contract) |

### Tests Unaffected

- `tests/infra/test_cab.py` — `CDE_CHANGED` change type still works; operates on contract diffs
- All zone-level tests (bronze, silver, gold) — no CDE/PII logic in zone transformers

## Migration Strategy for Existing Domain Projects

For projects that already have `governance/cde-catalog.json`:

1. Read the CDE catalog's mappings (each mapping has `table`, `field`, `rationale`)
2. For each mapping, find the corresponding column in the data contract for that table
3. Set `is_cde: true` and `cde_rationale: <rationale from catalog>` on the column
4. After all mappings are applied, delete `governance/cde-catalog.json`
5. For glossary terms that had `is_cde`, `is_pii`, `cde_rationale`, `pii_rationale`, or `cde_reference`: strip those fields
6. Re-validate glossary: `python3 -m brightsmith.infra.glossary_validator validate`

This is documented as a manual procedure. No automated migration script in scope for this spec.

## Relationship to Other Specs

| Spec | Relationship |
|------|-------------|
| `data-contracts.md` | Direct dependency — contract column schema changes |
| `framework-gap-closure.md` | Pipeline checklist references cde-catalog.json — must update |
| `framework-quality-parity.md` | Glossary field count changes — must update |
| `cab-agent.md` | CDE_CHANGED detection wording — minor update |
| `adversarial-dq-hardening.md` | No impact |
| `lineage-maturity.md` | No impact |
| `domain-source-assignment.md` | No impact |
| `ai-ready-mcp-server.md` | No impact |

## Implementation Order

1. **Phase 1: Infrastructure code (Python)**
   1. `contract.py` — add `cde_rationale`, `is_pii`, `pii_rationale` to `ColumnContract`; add to column dict template; remove glossary CDE derivation
   2. `glossary_loader.py` — remove `is_cde`, `is_pii` from `GlossaryTerm`; remove from `load_standard_glossary()` and `load_project_glossary()`
   3. `glossary_validator.py` — remove `is_cde`, `is_pii` from `REQUIRED_FIELDS`; remove CDE/PII rationale validation blocks; update docstring

2. **Phase 2: Tests**
   1. `test_glossary_validator.py` — remove 3 tests, update helper, add 1 test
   2. `test_glossary_loader.py` — remove `is_cde`/`is_pii` from fixtures and assertions
   3. `test_contract.py` — update helper, add 3 tests
   4. Run full test suite to verify green

3. **Phase 3: Agent definitions**
   1. Rewrite `.claude/agents/cde-tagger.md`
   2. Update `.claude/agents/data-steward.md`
   3. Update `.claude/agents/semantic-modeler.md`
   4. Update `.claude/agents/governance-reviewer.md`
   5. Update `.claude/agents/doc-generator.md`
   6. Update `.claude/agents/insight-manager.md`
   7. Update `.claude/agents/cab-agent.md` (minor wording)
   8. Mirror all changes to `agents/` directory

4. **Phase 4: Documentation**
   1. Update `CLAUDE.md` (3 rule changes + Key Paths cleanup)
   2. Update `docs/specs/data-contracts.md`
   3. Update `docs/specs/framework-gap-closure.md`
   4. Update `docs/specs/framework-quality-parity.md`

5. **Phase 5: Verification**
   1. `python -m pytest tests/infra/` — all pass
   2. Grep for `cde-catalog.json` across codebase — zero hits
   3. Grep for `cde_reference` across codebase — zero hits
