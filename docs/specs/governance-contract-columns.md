# Spec: Governance Contract Columns Table

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @governance-reviewer
**Created:** 2026-03-29

## Problem Statement

The governance Iceberg database (`governance-admin-database` spec) stores contract metadata at the **table level** only — `governance.contract_metadata` has `column_count`, `grain_columns`, and boolean flags, but no per-column data.

Column-level detail — CDE flags, PII flags, business term mappings, data types, nullability — still lives exclusively in YAML contract files (`governance/data-contracts/*.yaml`). This means:

1. **Brightforge still parses YAML files** for CDE catalog, business term overlays in the data model viewer, and column-level governance detail. The goal of the governance database was to eliminate file parsing as the source of truth.

2. **JSON contracts have no column data at all** — the 3 JSON-format contracts (`gold-derived-metrics-contract.json`, `gold-financial-summary-contract.json`, `gold-fiscal-calendar-contract.json`) were never enriched with CDE/PII/business_term fields, so those tables have zero governance coverage in the UI.

3. **Business term resolution is broken** — Brightforge's data model viewer builds a business term lookup only from CDE-flagged columns. Non-CDE columns with business terms (which is most columns) are invisible. This is partly a Brightforge bug (fixed separately), but fundamentally the data needs to be in the database.

## Solution

Add `governance.contract_columns` — an 8th Iceberg table in the governance namespace that stores one row per column per contract version. Extend `sync_contract()` to write column records alongside the existing table-level metadata.

## Design Decisions

### 1. New table, not column expansion on `contract_metadata`

`contract_metadata` has one row per contract version. Column data is one-to-many (10-30 columns per contract). A separate table avoids schema bloat and keeps the grain clean.

### 2. All columns, not just CDE/PII

Every column in every contract gets a row — not just flagged ones. Business terms, data types, descriptions, and nullability are useful for all columns in the data model viewer and data dictionary.

### 3. Same append-only pattern

Uses the same `promote()` / `compute_grain_id()` pattern as all other governance tables. Grain: `[contract_name, column_name, version]`. Re-syncing the same contract version is idempotent.

### 4. Sync all contract formats

Both YAML and JSON contracts get synced. The `sync_contract()` function already receives parsed contract dicts — just needs to iterate `schema.columns` and write to the new table.

## Table Schema

### `governance.contract_columns`

One row per column per contract version.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| record_id | string | yes | Grain hash |
| contract_name | string | yes | FK to contract_metadata.contract_name |
| table_name | string | yes | Fully qualified table name (e.g. `silver.fact_filings`) |
| zone | string | yes | Namespace/zone |
| column_name | string | yes | Column name |
| ordinal_position | int | yes | Column order (0-based) |
| data_type | string | no | Column data type |
| is_nullable | boolean | no | Whether column is nullable |
| is_cde | boolean | no | Critical Data Element flag |
| cde_rationale | string | no | Why this column is a CDE |
| is_pii | boolean | no | Personally Identifiable Information flag |
| pii_rationale | string | no | Why this column is PII |
| business_term | string | no | Business term ID (e.g. `BT-001`) |
| description | string | no | Column description/definition |
| version | string | yes | Contract version (matches contract_metadata) |
| updated_at | timestamptz | yes | When synced |

**Grain fields:** `[contract_name, column_name, version]`

## Implementation

### File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/brightsmith/infra/governance_db.py` | Modify | Add `CONTRACT_COLUMNS_SCHEMA`, register in `_TABLE_CONFIGS`, extend `sync_contract()` to write column rows |
| `tests/test_governance_db.py` | Modify | Add test for column sync, verify CDE/PII/business_term fields round-trip |

### Code Changes

#### 1. Add schema (in `governance_db.py`)

```python
CONTRACT_COLUMNS_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="contract_name", field_type=StringType(), required=True),
    NestedField(field_id=3, name="table_name", field_type=StringType(), required=True),
    NestedField(field_id=4, name="zone", field_type=StringType(), required=True),
    NestedField(field_id=5, name="column_name", field_type=StringType(), required=True),
    NestedField(field_id=6, name="ordinal_position", field_type=IntegerType(), required=True),
    NestedField(field_id=7, name="data_type", field_type=StringType(), required=False),
    NestedField(field_id=8, name="is_nullable", field_type=BooleanType(), required=False),
    NestedField(field_id=9, name="is_cde", field_type=BooleanType(), required=False),
    NestedField(field_id=10, name="cde_rationale", field_type=StringType(), required=False),
    NestedField(field_id=11, name="is_pii", field_type=BooleanType(), required=False),
    NestedField(field_id=12, name="pii_rationale", field_type=StringType(), required=False),
    NestedField(field_id=13, name="business_term", field_type=StringType(), required=False),
    NestedField(field_id=14, name="description", field_type=StringType(), required=False),
    NestedField(field_id=15, name="version", field_type=StringType(), required=True),
    NestedField(field_id=16, name="updated_at", field_type=TimestamptzType(), required=True),
)
```

Register in `_TABLE_CONFIGS`:
```python
"contract_columns": (CONTRACT_COLUMNS_SCHEMA, ["contract_name", "column_name", "version"]),
```

#### 2. Extend `sync_contract()` (in `governance_db.py`)

After writing the table-level record, iterate columns:

```python
def sync_contract(contract: dict, contract_file_path: str) -> dict:
    # ... existing table-level write ...
    result = _write_records("contract_metadata", [record])

    # Write column-level records
    schema = contract.get("schema", {})
    columns = schema.get("columns", [])
    table_name = schema.get("table", "")
    namespace = schema.get("namespace", table_name.split(".")[0] if "." in table_name else "")
    version = meta.get("version", "1.0.0")
    contract_name = meta.get("name", "")

    col_records = []
    for i, col in enumerate(columns):
        col_records.append({
            "contract_name": contract_name,
            "table_name": table_name,
            "zone": namespace,
            "column_name": col.get("name", ""),
            "ordinal_position": i,
            "data_type": col.get("type"),
            "is_nullable": col.get("nullable", True),
            "is_cde": col.get("is_cde", False),
            "cde_rationale": col.get("cde_rationale"),
            "is_pii": col.get("is_pii", False),
            "pii_rationale": col.get("pii_rationale"),
            "business_term": col.get("business_term"),
            "description": col.get("description"),
            "version": version,
            "updated_at": datetime.now(timezone.utc),
        })

    if col_records:
        col_result = _write_records("contract_columns", col_records)
        result["columns_promoted"] = col_result.get("promoted", 0)
        result["columns_skipped"] = col_result.get("skipped", 0)

    return result
```

#### 3. Backfill during `sync_all()`

`sync_all()` already calls `sync_contract()` for every contract file. Once `sync_contract()` writes columns, a re-run of `sync_all()` backfills all existing contracts. No separate migration needed.

### JSON contract gap

The 3 JSON-format contracts (`gold-*-contract.json`) need to be enriched with column-level CDE/PII/business_term fields by the appropriate governance agent (`@cde-tagger`, `@data-steward`). This is a separate pipeline concern — the sync infrastructure will pick them up once enriched.

## Success Criteria

- [ ] `governance.contract_columns` table exists with the schema above
- [ ] `sync_contract()` writes one row per column per contract version
- [ ] CDE, PII, and business_term fields round-trip correctly (write → query → verify)
- [ ] `sync_all()` backfills all existing YAML contracts into contract_columns
- [ ] Idempotent: re-syncing the same contract writes 0 new rows
- [ ] Existing `contract_metadata` behavior unchanged
- [ ] All existing tests pass + new tests for column sync

## Follow-up (Brightforge)

Once this spec is COMPLETE, Brightforge needs a follow-up spec to:

1. Add `get_cde_mappings()` and `get_cde_catalog()` overrides to `IcebergGovernanceStore` — query `contract_columns` instead of parsing YAML
2. Build business term lookup from `contract_columns` directly (all columns, not just CDEs)
3. Remove the CDE-only filter from the data model viewer's business term resolution
