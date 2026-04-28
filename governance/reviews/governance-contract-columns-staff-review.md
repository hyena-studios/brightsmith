## Staff Engineer Review

### Date: 2026-03-29
### Reviewer: @staff-engineer
### Status: APPROVED

### Verdict

This is clean, minimal infrastructure work. The implementation matches the spec exactly -- 16 columns, correct types, correct nullability, correct grain. It reuses the existing `_write_records` / `promote` / `compute_grain_id` pattern without inventing any new abstractions. The tests have real assertions on specific values, not hand-wavy existence checks. I would put my name on this.

### Code Quality

**`src/brightsmith/infra/governance_db.py`**

Schema definition (lines 153-170): Exact match to spec. 16 fields, field_ids sequential, types correct, nullability matches. Fine.

`_TABLE_CONFIGS` registration (line 209): Grain fields `["contract_name", "column_name", "version"]` match spec. Fine.

`sync_contract()` extension (lines 448-479): Builds column records from the contract dict's `schema.columns` list. Uses the same `_write_records` call as everything else. The column-write is additive -- the existing table-level write at line 446 is untouched. The `if col_records:` guard at line 474 handles the empty-columns edge case by simply not writing. Minor inconsistency: when columns are empty, `result` lacks `columns_promoted`/`columns_skipped` keys. Not blocking -- callers use `.get()` -- but the API would be cleaner if it always returned those keys.

`get_contract_columns()` (lines 658-669): Two paths -- filtered by contract_name, or unfiltered. Both order by `ordinal_position`. Parameterized query, no injection risk. Fine.

Default values for `is_cde`/`is_pii` (line 464-465): Defaults to `False` when absent from the contract column definition. The pre-review noted this loses the "not assessed" vs "not a CDE" distinction. I agree that's worth tracking but it matches the spec's design, so it's not a defect in this implementation. Future concern.

**No security issues.** All SQL uses parameterized `$N` placeholders. The one f-string in `get_agent_activity` builds WHERE clauses from hardcoded strings, not user input.

### Test Quality

15 tests total, all pass (0.90s). Three tests are directly for the new feature:

1. `test_sync_contract` (line 237): Syncs a contract with 3 columns, verifies `get_contract_columns` returns 3 rows. Extended from the original to cover the new feature. Assertions check specific values (`contract_name == "test-contract"`, `column_count == 3`).

2. `test_sync_contract_writes_columns` (line 275): The real test. Creates a contract with CDE=True, cde_rationale="Primary entity identifier", business_term="BT-001" on column "cik". Queries back and asserts on 12 specific field values: `is_cde`, `cde_rationale`, `is_pii`, `business_term`, `data_type`, `is_nullable`, `ordinal_position`, `table_name`, `zone`, `version`, plus default verification on column "period" (is_cde=False, is_pii=False, business_term=None). Also verifies ordering (`col_names == ["cik", "revenue", "period"]`). This is thorough.

3. `test_sync_contract_columns_idempotent` (line 352): Syncs same contract twice. First call: `columns_promoted == 2`. Second call: `columns_promoted == 0`, `columns_skipped == 2`. Total query: `len(cols) == 2`. This validates the grain-based dedup is working, not just that the function doesn't crash on re-run.

**These are real tests.** The assertions validate specific values, specific counts, specific field names. No `assert True`, no `assert len > 0`.

**Minor gap:** No test for `get_contract_columns()` without a filter (the unfiltered path). No test for the empty-columns edge case. Neither is blocking for an infra spec.

### Spec Compliance

All 7 success criteria verified:

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | `governance.contract_columns` table exists with spec schema | PASS | Schema exact match (16/16 fields) |
| 2 | `sync_contract()` writes one row per column per version | PASS | Lines 448-479, verified by test |
| 3 | CDE/PII/business_term round-trip | PASS | `test_sync_contract_writes_columns` asserts 12 field values |
| 4 | `sync_all()` backfills all YAML contracts | PASS | `sync_all()` calls `sync_contract()` which now writes columns |
| 5 | Idempotent re-sync writes 0 new rows | PASS | `test_sync_contract_columns_idempotent` proves it |
| 6 | Existing contract_metadata unchanged | PASS | `test_sync_contract` still passes, original write untouched |
| 7 | All existing tests pass + new tests | PASS | 15/15 pass, 3 new/extended tests |

### Data Correctness Spot-Check

N/A -- this is an infrastructure spec that adds a governance metadata table. It does not produce domain data in Base or Gold zones. The correctness of the data it stores is validated by the round-trip tests (write specific values, query them back, assert they match). No golden dataset is expected or applicable.

### Issues

| # | Severity | File | Issue | Required Fix |
|---|----------|------|-------|-------------|
| 1 | NIT | governance_db.py:474-479 | When `col_records` is empty, `result` lacks `columns_promoted`/`columns_skipped` keys. Inconsistent API surface. | None required. Callers use `.get()`. |
| 2 | NIT | test_governance_db.py | No test for `get_contract_columns()` unfiltered path (line 666-669). | None required for this spec. |

### What's Acceptable

The implementation is minimal and correct. It reuses existing patterns without inventing anything new. The test assertions are specific and meaningful. The spec is well-written with clear success criteria. The governance reviews are substantive, not boilerplate -- they noted the False-vs-NULL distinction and the JSON contract gap as advisories, which shows actual analysis happened.
