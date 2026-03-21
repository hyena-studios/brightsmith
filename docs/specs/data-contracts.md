# Framework Spec: Machine-Readable Data Contracts

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-19

## Problem Statement

Grist specs are markdown files. They're good for human review ("here's what we're going to build") but useless for machine enforcement ("is what we built still valid?"). Once a spec is approved and implementation is done, the markdown sits in `docs/specs/` and nothing ever checks it again. If someone changes a column type, removes a field, or breaks the grain — nothing catches it until a consumer complains.

The `governance/data-contracts/` directory exists in every scaffolded project but is always empty.

A real data contract is:
- **Machine-readable** — YAML, parseable by the pipeline
- **Generated from implementation** — not hand-written, reflects what was actually built
- **Continuously enforced** — `python -m grist contract verify` runs on every pipeline execution
- **Versioned** — breaking changes require a version bump
- **Consumer-facing** — describes what you GET, not how it was built

## How It Works

```
Human writes spec (markdown)
        │
        ▼
Human approves spec
        │
        ▼
@primary-agent implements
        │
        ▼
@doc-generator generates contract (YAML) from:
  - actual Iceberg table schema
  - DQ rules file
  - golden dataset
  - spec metadata
        │
        ▼
Pipeline enforces contract on every run
  - schema matches table
  - DQ P0 passes
  - golden dataset verifies
  - freshness within SLA
```

The spec is the **proposal**. The contract is the **guarantee**. The spec can be aspirational — the contract must match reality.

## Success Criteria

- [ ] Contract YAML format defined with schema, quality, lineage, and consumer sections
- [ ] `@doc-generator` auto-generates contract from implemented table + governance artifacts
- [ ] `python -m grist.infra.contract verify {contract}` validates contract against live Iceberg table
- [ ] `python -m grist.infra.contract diff {contract}` detects schema drift
- [ ] Pipeline gate requires contract verification PASS before `@staff-engineer` review
- [ ] Breaking change detection with semantic versioning enforcement
- [ ] Contract status lifecycle: DRAFT → ACTIVE → DEPRECATED
- [ ] All new code has tests

## Technical Design

### 1. Contract Format

**File:** `governance/data-contracts/{table-name}.yaml`

One contract per Iceberg table (not per spec — a table may be built by one spec but consumed by many).

```yaml
apiVersion: grist/v1
kind: DataContract
metadata:
  name: company-financials
  version: "1.0.0"
  status: active           # draft | active | deprecated
  owner: "@data-steward"
  domain: ""               # filled by domain project
  created: "2026-03-19"
  spec: docs/specs/consumable-company-financials.md

# What the table contains
schema:
  table: consumable.company_financials
  namespace: consumable
  grain:
    columns: [cik, fy, fp]
    description: One row per company per fiscal period
  columns:
    - name: cik
      type: integer
      required: true
      business_term: BT-001
      is_cde: true
      description: Entity identifier
    - name: fy
      type: integer
      required: true
      description: Fiscal year
    # ... all columns with types, nullability, business term refs

# Quality guarantees — machine-checked
quality:
  freshness:
    max_staleness_hours: 24
    measured_by: ingested_at
  completeness:
    min_row_count: 1
    required_columns: [cik, fy, fp]
  accuracy:
    golden_dataset: governance/golden-datasets/{spec}-golden.json
    min_pass_rate_pct: 80
  uniqueness:
    grain_unique: true
  dq_rules:
    rules_file: governance/dq-rules/{spec}.json
    p0_pass_required: true

# Where the data comes from
lineage:
  sources:
    - table: base.financial_facts
      relationship: transformed_from
    - table: base.tag_metadata
      relationship: joined_with

# Who uses this table
consumers: []
  # Populated as consumers register:
  # - name: ai-ready-chat-agent
  #   tool: lookup_financials
  #   usage: Point lookups by company and period

# Versioning
compatibility:
  breaking_changes: [column_removed, column_type_changed, grain_changed]
  non_breaking_changes: [column_added, description_changed, consumer_added]
  deprecation_notice_days: 30
```

### 2. Contract Generator

**File:** `src/grist/infra/contract.py`

`@doc-generator` calls this after implementation to produce the contract:

```python
def generate_contract(
    spec_path: Path,
    table_name: str,
    dq_rules_path: Path | None = None,
    golden_dataset_path: Path | None = None,
    business_glossary_path: Path | None = None,
) -> dict:
    """Generate a data contract from an implemented Iceberg table.

    Reads the actual table schema from the Iceberg catalog,
    cross-references business glossary for term IDs,
    and assembles the contract YAML.
    """
```

The generator:
1. Loads the Iceberg table and reads its schema (actual columns, types, nullability)
2. Reads the spec for metadata (name, description, grain)
3. Reads the business glossary to attach `business_term` and `is_cde` per column
4. Reads the DQ rules file path
5. Reads the golden dataset path
6. Produces the contract YAML

**This means the contract always reflects reality** — it's generated from the table, not written by hand.

### 3. Contract Verifier

**File:** `src/grist/infra/contract.py`

```
python -m grist.infra.contract verify {contract-name}
python -m grist.infra.contract verify --all
python -m grist.infra.contract diff {contract-name}
python -m grist.infra.contract list
```

#### `verify` checks:

| Check | What It Does | Pass/Fail |
|-------|-------------|-----------|
| Schema match | Compare contract columns to actual Iceberg table schema | FAIL if column missing, type mismatch, or nullability mismatch |
| Grain unique | Query table for duplicate grains | FAIL if duplicates exist |
| Freshness | Check `max(ingested_at)` against `max_staleness_hours` | FAIL if stale |
| Row count | Check `COUNT(*)` against `min_row_count` | FAIL if below minimum |
| Required columns | Check that required columns have zero nulls | FAIL if nulls found |
| DQ P0 | Run DQ rules, check P0 gate | FAIL if P0 failures |
| Golden dataset | Run golden dataset verification | FAIL if below `min_pass_rate_pct` |

Output:
```
Contract: company-financials v1.0.0
  Schema match:      PASS (19/19 columns)
  Grain unique:      PASS (0 duplicates)
  Freshness:         PASS (2h ago, max 24h)
  Row count:         PASS (297 rows, min 1)
  Required columns:  PASS (0 nulls in required)
  DQ P0 gate:        PASS (12/12 rules)
  Golden dataset:    PASS (12/12 values, 100%)

  Status: VALID
```

#### `diff` detects drift:

Compares the contract's schema section to the current Iceberg table. Reports:
- Columns in contract but missing from table (BREAKING)
- Columns in table but missing from contract (contract needs update)
- Type mismatches (BREAKING)
- Nullability mismatches (potentially breaking)

```
Contract: company-financials v1.0.0
  BREAKING: Column 'revenue' type changed: double → float
  NEW:      Column 'ebitda' exists in table but not in contract

  Action required: bump version to 2.0.0 for breaking change
```

### 4. Breaking Change Detection

When `@doc-generator` regenerates a contract and detects a schema change:

| Change Type | Classification | Required Action |
|-------------|---------------|-----------------|
| Column removed | BREAKING | Major version bump (1.0.0 → 2.0.0) |
| Column type changed | BREAKING | Major version bump |
| Grain changed | BREAKING | Major version bump |
| Column renamed | BREAKING | Major version bump |
| Column added | NON-BREAKING | Minor version bump (1.0.0 → 1.1.0) |
| Description changed | NON-BREAKING | Patch version bump (1.0.0 → 1.0.1) |
| Consumer added | NON-BREAKING | Patch version bump |
| Quality threshold changed | POTENTIALLY BREAKING | Review required |

The pipeline gate blocks breaking changes unless the version is bumped.

### 5. Pipeline Enforcement (Build-Time + Runtime)

Contract verification happens at **three points** — not just when the spec is first built.

**File:** `src/grist/infra/pipeline_gate.py` (update)
**File:** `src/grist/infra/contract.py` (new)

#### 5a. Build-time: during spec implementation

At `@doc-generator` completion (consumable + ai_ready zones):
- Contract YAML must exist at `governance/data-contracts/{table-name}.yaml`
- Contract must have `status: active` or `status: draft`

At `@staff-engineer` review:
- `python -m grist.infra.contract verify {contract}` must PASS
- If contract existed previously and schema changed: version must be bumped appropriately

#### 5b. Runtime: after every data refresh

When any zone's transformation runs (ingest, base transform, consumable transform), verify all ACTIVE contracts for tables that were written to:

```python
# In BaseIngestor.ingest(), after data is written:
from grist.infra.contract import verify_contracts_for_table

results = verify_contracts_for_table("raw.company_facts")
if results.has_failures:
    logger.error("Contract violations after ingest: %s", results.summary)
    # Write violation to governance/contract-violations/{timestamp}.json
    # Do NOT silently proceed
```

This catches:
- New data that breaks grain uniqueness (duplicate entity-period from a new filing)
- New data that pushes a value outside golden dataset tolerance
- Schema evolution that wasn't coordinated with a contract version bump
- DQ P0 failures introduced by new data

The framework does this automatically — domain projects don't need to add contract checks to their code.

#### 5c. Pre-query: before AI-Ready tools serve data

The chat agent's tools query consumable tables. Before the first query in a session, verify all contracts for tables the tools depend on:

```python
# In chat agent startup or first tool call:
from grist.infra.contract import verify_all_active_contracts

results = verify_all_active_contracts()
if results.has_failures:
    # Add to system prompt: "WARNING: Data quality issues detected.
    # The following contracts are failing: ..."
    # Agent can still answer but must disclose quality caveats
```

This prevents the chat agent from confidently serving data that violates its own quality guarantees.

#### 5d. Contract violation tracking

When a contract verification fails at runtime, write a violation record:

```json
// governance/contract-violations/2026-03-19T04-30-00Z.json
{
  "contract": "company-financials",
  "version": "1.0.0",
  "verified_at": "2026-03-19T04:30:00Z",
  "trigger": "runtime_post_transform",
  "checks": {
    "schema_match": {"status": "PASS"},
    "grain_unique": {"status": "FAIL", "detail": "3 duplicate grains found"},
    "freshness": {"status": "PASS"},
    "dq_p0": {"status": "PASS"},
    "golden_dataset": {"status": "PASS"}
  },
  "overall": "FAIL"
}
```

`@governance-reviewer` and `@staff-engineer` can review violation history during post-implementation checks. Repeated violations indicate a systemic issue, not a one-time data anomaly.

#### 5e. CLI for all enforcement modes

```bash
# Build-time (explicit, during spec implementation)
python -m grist.infra.contract verify {contract}
python -m grist.infra.contract verify --all

# Runtime (called automatically by framework after transforms)
python -m grist.infra.contract verify --all --mode runtime

# Pre-query (called automatically by chat agent)
python -m grist.infra.contract verify --all --mode pre-query

# Violation history
python -m grist.infra.contract violations [--contract NAME] [--since DATE]
```

All three modes run the same checks. The difference is **when** they run and **what happens on failure**:

| Mode | When | On Failure |
|------|------|-----------|
| Build-time | During spec implementation | Blocks `@staff-engineer` review |
| Runtime | After every transform | Logs violation, continues (non-blocking but tracked) |
| Pre-query | Before chat agent serves data | Adds quality caveat to agent system prompt |

Runtime is non-blocking because you don't want a stale freshness check to prevent re-ingestion of new data — that would be a deadlock. But violations are tracked and visible.

### 6. Contract Lifecycle

```
DRAFT ──────► ACTIVE ──────► DEPRECATED
  │              │                │
  │  (staff-     │  (new version  │  (table dropped
  │   engineer   │   replaces     │   or superseded)
  │   approves)  │   this one)    │
  ▼              ▼                ▼
Generated     Enforced on      Consumers warned,
by @doc-      every pipeline   contract retained
generator     run              for audit
```

- **DRAFT**: Generated but not yet reviewed. Not enforced.
- **ACTIVE**: Approved. Enforced on every pipeline run. Breaking changes blocked without version bump.
- **DEPRECATED**: Superseded by a newer version. Consumers have `deprecation_notice_days` to migrate.

### 7. Agent Changes

| Agent | Current Role | New Role |
|-------|-------------|----------|
| `@doc-generator` | Writes data dictionary JSON | Also generates contract YAML from implemented table |
| `@governance-reviewer` (post) | Checks artifacts exist | Also verifies contract exists and passes |
| `@staff-engineer` | Reviews code + governance | Also requires `contract verify` PASS |
| `@data-steward` | Produces business glossary | Also registers consumers in contracts when new specs reference existing tables |

### 8. @setup Scaffolding

Update `src/grist/setup.py` and `.claude/agents/setup.md`:

- Create `governance/data-contracts/` directory (already exists)
- Add contract generation step to the pipeline documentation in scaffolded CLAUDE.md
- Add `grist.infra.contract` to the framework's module list

## Tests

- `tests/infra/test_contract.py`:
  - `test_generate_contract_from_iceberg_table` — generates valid YAML with correct columns/types
  - `test_verify_valid_contract_passes` — all checks pass for conforming table
  - `test_verify_schema_mismatch_fails` — missing column detected
  - `test_verify_type_mismatch_fails` — wrong type detected
  - `test_verify_freshness_stale_fails` — old data detected
  - `test_verify_grain_duplicate_fails` — duplicate grains detected
  - `test_diff_detects_breaking_change` — column removal flagged
  - `test_diff_detects_non_breaking_change` — column addition flagged
  - `test_version_bump_required_for_breaking` — pipeline rejects unbumped breaking change
  - `test_contract_lifecycle_transitions` — DRAFT → ACTIVE → DEPRECATED
  - `test_runtime_verify_after_transform` — contract checked automatically after data write
  - `test_runtime_violation_logged` — failed runtime check produces violation JSON
  - `test_prequery_verify_adds_caveat` — failing contract adds warning to agent context
  - `test_violation_history_queryable` — `contract violations` CLI returns past failures

## Relationship to Other Specs

This spec depends on:
- **Change 6 (Golden Datasets)** from `framework-quality-parity.md` — contracts reference golden datasets for accuracy verification
- **Change 3 (Consumable DQ Enforcement)** — contracts reference DQ rules files for P0 gate

This spec is independent of:
- Changes 1-2 (normalization) — contracts work regardless of normalization approach
- Change 5 (runtime lineage) — contracts capture lineage sources statically

## Implementation Order

1. Contract format + parser (the YAML schema)
2. Contract generator (reads Iceberg table → produces YAML)
3. Contract verifier (reads YAML + queries table → pass/fail)
4. Contract diff (compares YAML to table → drift detection)
5. Pipeline gate integration
6. Agent definition updates
7. @setup scaffolding update
