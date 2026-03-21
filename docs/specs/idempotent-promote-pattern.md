# Framework Spec: Idempotent Promote Pattern

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-19

## Problem Statement

Grist pipelines are not idempotent. The raw zone has grain-based dedup in `BaseIngestor`, but base and consumable zone transformers use bare `append_data()` — re-running them doubles the data. This means:

- You can't safely re-run the pipeline to pick up new source data
- You can't recover from a partial failure by re-running
- You can't add a new entity (e.g., a new company) and re-run without corrupting existing tables
- Iceberg snapshot history accumulates duplicate data instead of tracking real changes

A field-tested domain-specific pipeline solved this with **deterministic grain hashing + append-only dedup**: every row gets a deterministic ID computed from its business grain, and promotes skip rows that already exist. Re-running with the same data produces 0 new rows. Re-running with new data appends only the delta.

## Success Criteria

- [ ] Every zone transformer uses the promote pattern (no bare `append_data()` for derived tables)
- [ ] Re-running the full pipeline with the same source data produces 0 new rows across all zones
- [ ] Re-running after adding a new entity produces only new-entity rows (existing data untouched)
- [ ] Iceberg snapshot history reflects actual changes, not duplicated appends
- [ ] Lineage events capture rows promoted vs rows skipped per run
- [ ] `BaseIngestor.ingest()` pattern is extended (not replaced) — raw zone already works
- [ ] All new code has tests

## Technical Design

### 1. Grain Hash Utility

**File:** `src/grist/infra/grain.py` (new)

```python
import hashlib

def compute_grain_id(row: dict, grain_fields: list[str], prefix: str = "") -> str:
    """Compute a deterministic ID from a row's grain fields.

    Args:
        row: The data row.
        grain_fields: Ordered list of column names that define uniqueness.
        prefix: Optional prefix for readability (e.g., "FF" for financial facts).

    Returns:
        First 16 chars of SHA-256 hex digest. Deterministic: same input → same output.
    """
    grain_values = "|".join(str(row.get(f, "")) for f in grain_fields)
    hash_hex = hashlib.sha256(grain_values.encode()).hexdigest()[:16]
    return f"{prefix}-{hash_hex}" if prefix else hash_hex
```

**Why SHA-256 truncated to 16 chars:**
- Deterministic (same grain → same hash every time)
- Collision-resistant (16 hex chars = 64 bits = sufficient for tables under 1B rows)
- Human-readable in logs and queries
- Matches the pattern proven in production (EDGAIR uses this exact approach)

### 2. Filter Existing Records (DuckDB Anti-Join)

**File:** `src/grist/infra/iceberg_setup.py` (add function)

```python
def filter_existing_records(
    table: Table,
    records: list[dict],
    id_field: str = "record_id",
) -> tuple[list[dict], int]:
    """Filter out records that already exist in an Iceberg table.

    Uses a DuckDB anti-join for scalability — reads only the ID column
    from the existing table, not all columns.

    Args:
        table: Target Iceberg table.
        records: New records to filter.
        id_field: Column name containing the deterministic grain ID.

    Returns:
        (new_records, skipped_count) — records not already in the table.
    """
    import duckdb
    import pyarrow as pa

    if not records:
        return [], 0

    # Read only the ID column from existing table (cheap)
    try:
        existing_arrow = table.scan(selected_fields=(id_field,)).to_arrow()
    except Exception:
        return records, 0  # Table empty or doesn't exist yet

    if len(existing_arrow) == 0:
        return records, 0

    new_arrow = pa.Table.from_pylist(records)
    con = duckdb.connect()
    con.register("new_records", new_arrow)
    con.register("existing_ids", existing_arrow)

    result = con.execute(f"""
        SELECT n.*
        FROM new_records n
        LEFT JOIN existing_ids e ON n.{id_field} = e.{id_field}
        WHERE e.{id_field} IS NULL
    """).fetch_arrow_table()

    new_records = result.to_pylist()
    skipped = len(records) - len(new_records)
    return new_records, skipped
```

**Why anti-join instead of Python set:**
- Column-selective scan (reads only IDs, not full rows)
- DuckDB handles the join efficiently even for large tables
- Works with Arrow zero-copy (no serialization overhead)
- Scales to millions of rows where Python set lookup would OOM

### 3. Promote Function

**File:** `src/grist/infra/promote.py` (new)

The standard promote pattern that every zone transformer uses:

```python
def promote(
    table: Table,
    records: list[dict],
    id_field: str = "record_id",
    spec_name: str = "",
    agent_name: str = "",
) -> dict:
    """Idempotent promote: append only records not already in the table.

    Args:
        table: Target Iceberg table.
        records: Records to promote (must include id_field column).
        id_field: Column containing the deterministic grain ID.
        spec_name: For lineage tracking.
        agent_name: For lineage tracking.

    Returns:
        {"promoted": N, "skipped": M, "snapshot_id": X}
    """
    records, skipped = filter_existing_records(table, records, id_field)

    if not records:
        return {"promoted": 0, "skipped": skipped, "snapshot_id": None}

    snapshot_id = append_data(table, records)
    return {"promoted": len(records), "skipped": skipped, "snapshot_id": snapshot_id}
```

### 4. Zone Transformer Integration

Every zone transformer follows this pattern:

```python
# 1. Read from source zone
source_rows = read_with_duckdb(source_table)

# 2. Transform (domain-specific logic)
output_rows = transform(source_rows)

# 3. Compute grain IDs
for row in output_rows:
    row["record_id"] = compute_grain_id(row, GRAIN_FIELDS, prefix="CF")

# 4. Promote (idempotent)
result = promote(target_table, output_rows, id_field="record_id")
# result = {"promoted": 25, "skipped": 275, "snapshot_id": 12345}
```

**No truncate. No overwrite. No duplicate risk.** Same input → same hashes → dedup skips them → 0 rows promoted.

### 5. Grain Definitions Per Table

Each table declares its grain explicitly (this also feeds data contracts and DQ rules):

```python
# In each zone's config or transformer:

# Base zone
FINANCIAL_FACTS_GRAIN = ["cik", "taxonomy", "tag", "unit", "start", "end"]
FINANCIAL_FACTS_ID_PREFIX = "FF"

# Consumable zone
COMPANY_FINANCIALS_GRAIN = ["cik", "fy", "fp"]
COMPANY_FINANCIALS_ID_PREFIX = "CF"

TIME_SERIES_GRAIN = ["cik", "canonical_concept", "unit", "fy", "fp"]
TIME_SERIES_ID_PREFIX = "TS"
```

The grain fields are the same ones used for:
- DQ uniqueness rules (`SELECT ... GROUP BY grain HAVING COUNT(*) > 1`)
- Data contract schema (`grain: [cik, fy, fp]`)
- Golden dataset filters (`{"cik": 320193, "fy": 2024, "fp": "FY"}`)

**One source of truth for grain** — defined once, used everywhere.

### 6. Schema Changes: Add `record_id` Column

Every Iceberg table gets a `record_id` (or table-specific equivalent) as its first column:

| Table | ID Column | Grain Fields |
|-------|-----------|-------------|
| raw.* | (existing dedup grain, no change) | Per source config |
| base.financial_facts | fact_id | cik, taxonomy, tag, unit, start, end |
| base.tag_metadata | tag_id | taxonomy, tag |
| consumable.company_financials | record_id | cik, fy, fp |
| consumable.financial_time_series | record_id | cik, canonical_concept, unit, fy, fp |
| consumable.company_comparison | record_id | canonical_concept, unit, fy, fp, cik |
| consumable.filing_activity | record_id | cik, fy, fp, form |
| consumable.concept_coverage | record_id | canonical_concept, cik |

### 7. Special Case: Conformed/Collision-Resolved Tables

Some tables are **fully recomputed** on each run because their output depends on the complete input (e.g., collision resolution needs to see all competing concepts to pick the winner). For these:

```python
# Pattern: delete-then-promote (still idempotent via grain IDs)
from pyiceberg.expressions import AlwaysTrue

table.delete(AlwaysTrue())  # Wipe existing
result = promote(table, new_rows, id_field="record_id")
# New snapshot contains the full recomputed table
# Iceberg preserves old snapshot for time-travel
```

This is acceptable when:
- The table is small (< 100K rows)
- The transformation is non-incremental (needs full input to produce correct output)
- Iceberg time-travel preserves history regardless

### 8. `BaseIngestor` — No Changes Needed

The raw zone ingestor already implements grain-based dedup via `_build_existing_grains()` and `_make_grain()`. The new promote pattern is consistent with this — it just provides the same capability for non-raw zones.

The only addition: after `ingest()` completes, emit a lineage event with `promoted` vs `skipped` counts (Change 5 from `framework-quality-parity.md`).

## Tests

- `tests/infra/test_grain.py`:
  - `test_same_input_produces_same_hash` — deterministic
  - `test_different_input_produces_different_hash` — no collisions for distinct grains
  - `test_null_fields_handled` — None/empty values don't crash
  - `test_prefix_included_in_id` — prefix appears in output
  - `test_field_order_matters` — (a, b) != (b, a)

- `tests/infra/test_promote.py`:
  - `test_first_promote_appends_all` — empty table gets all rows
  - `test_second_promote_skips_duplicates` — same data → 0 promoted
  - `test_promote_appends_only_new` — mix of new and existing → only new promoted
  - `test_promote_returns_snapshot_id` — snapshot captured
  - `test_promote_returns_skip_count` — skipped count accurate
  - `test_filter_existing_records_column_selective` — only reads ID column

- `tests/infra/test_idempotency.py` (integration):
  - `test_full_pipeline_rerun_produces_zero_new_rows`
  - `test_pipeline_with_new_entity_appends_only_new`
  - `test_iceberg_snapshots_accumulate_correctly`

## Relationship to Other Specs

- **data-contracts.md**: Grain definitions feed the `schema.grain` section of contracts. The `record_id` column enables contract `uniqueness.grain_unique` checks.
- **framework-quality-parity.md (Change 5)**: Runtime lineage captures `promoted` and `skipped` counts from the promote pattern.
- **framework-quality-parity.md (Change 6)**: Golden dataset filters use the same grain fields as promote.

## Implementation Order

1. `grain.py` — grain hash utility (no dependencies)
2. `iceberg_setup.py` — add `filter_existing_records()` (depends on #1)
3. `promote.py` — promote function (depends on #2)
4. Update base zone transformers to use promote pattern
5. Update consumable zone transformers to use promote pattern
6. Add `record_id` / `fact_id` columns to all non-raw schemas
7. Integration test: re-run pipeline, verify 0 new rows
