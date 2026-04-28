## Governance Review: governance-contract-columns
**Review Type:** Pre-Implementation (retroactive)
**Reviewer:** @governance-reviewer
**Date:** 2026-03-29
**Verdict:** APPROVED

### Context

This is a retroactive pre-implementation review. The spec and implementation already exist. Reviewing the spec for completeness, design soundness, and adherence to Brightsmith conventions.

This is an Infrastructure (cross-cutting) spec. It does not produce zone data, so data contracts, DQ rules, lineage events, CDE tags, and data models are not expected for this spec itself.

### Pre-Implementation Checklist Results

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 1 | Clear problem statement and success criteria | PASS | Problem statement identifies 3 concrete gaps (YAML-only column data, JSON contract gap, broken business term resolution). 7 success criteria defined. |
| 2 | Input data sources identified with paths | PASS | `governance/data-contracts/*.yaml` and `*.json` as source contracts |
| 3 | Output artifacts defined with paths and formats | PASS | `governance.contract_columns` Iceberg table, schema fully specified |
| 4 | Transformations described (what changes, why) | PASS | `sync_contract()` extension clearly described with code |
| 5 | Zone assignment correct | PASS | Infrastructure (cross-cutting) -- correct, this is governance metadata plumbing |
| 6 | Primary implementation agent identified | PASS | @governance-reviewer (unusual but acceptable for infra/governance tooling) |
| 7 | DQ rule categories specified or acknowledged | N/A | Infrastructure spec, not a zone table |
| 8 | CDE mapping impact assessed | N/A | Infrastructure spec |
| 9 | Lineage scope defined | N/A | Infrastructure spec |
| 10 | Breaking changes to existing schemas flagged | PASS | Spec explicitly states "Existing contract_metadata behavior unchanged" |
| 11 | Testing approach defined | PASS | Tests enumerated in File Changes table; success criteria include idempotency and round-trip verification |

### Design Assessment

**Decision 1: Separate table vs. expanding contract_metadata** -- SOUND. One-to-many relationships belong in separate tables. The grain `[contract_name, column_name, version]` is clean and unambiguous. Mixing table-level and column-level data in a single table would violate normalization and complicate queries.

**Decision 2: All columns, not just CDE/PII** -- SOUND. This is the right call. Filtering to only flagged columns was the root cause of the business term resolution bug described in the problem statement. Storing all columns provides a complete picture for the data model viewer and dictionary.

**Decision 3: Same append-only promote pattern** -- SOUND. Uses `_write_records()` which calls `promote()` and `compute_grain_id()` -- the standard Brightsmith idempotent write pattern. Grain fields match what the spec defined. No special-casing.

**Decision 4: Sync all contract formats** -- SOUND. The YAML/JSON gap is acknowledged. The spec correctly separates "the sync infrastructure handles both formats" from "the JSON contracts need enrichment by governance agents," deferring the latter as a follow-up pipeline concern rather than scope-creeping this spec.

### Implementation Alignment

The implementation in `governance_db.py` matches the spec exactly:

- `CONTRACT_COLUMNS_SCHEMA` at line 153 matches the spec's schema definition (16 fields, same types, same nullability)
- `_TABLE_CONFIGS` registration at line 209 uses the specified grain fields `["contract_name", "column_name", "version"]`
- `sync_contract()` at line 423 writes table-level record first, then iterates columns -- matching the spec's pseudocode
- `get_contract_columns()` query function exists for downstream consumers
- Three tests exist: basic sync, CDE/PII/business_term round-trip, and idempotency verification

### Issues Found

| # | Severity | Description | Resolution Required |
|---|----------|-------------|---------------------|
| 1 | ADVISORY | The `is_cde` and `is_pii` fields default to `False` when absent from the contract column definition (line 464-466). This is correct behavior -- absence of a flag means "not flagged" -- but worth noting that JSON contracts without these fields will show `False` rather than `NULL`. The distinction between "not a CDE" and "not yet assessed" is lost. | No action required for this spec. Consider adding an `assessment_status` field in a future iteration if the distinction matters. |
| 2 | ADVISORY | The spec notes JSON contracts need enrichment by @cde-tagger and @data-steward but does not create a follow-up spec or tracking issue. This is a known gap that could be forgotten. | Recommend creating a tracking item or follow-up spec for JSON contract enrichment. |

### Decision Rationale

APPROVED. The spec is complete, well-structured, and implementation-ready. It follows Brightsmith conventions:

- Uses the `promote()` / `compute_grain_id()` idempotent write pattern
- Clean grain definition with deterministic `record_id`
- Registered in `_TABLE_CONFIGS` like all other governance tables
- Extends existing `sync_contract()` rather than creating parallel code paths
- `sync_all()` provides automatic backfill with no migration needed

The two ADVISORY findings are minor -- neither represents a governance gap or a blocking issue. The design decisions are sound and well-justified in the spec.
