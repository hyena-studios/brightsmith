# Framework Spec: Adversarial DQ Hardening

**Status:** DRAFT
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-19

## Problem Statement

The chaos monkey and DQ rule writer are in an arms race — but the chaos monkey is bringing a water pistol. It does per-cell value corruption (null, truncation, extreme values) but misses the corruptions that actually break production data pipelines: duplicate rows, orphan keys, swapped columns, missing time periods, impossible business logic, and distribution shifts.

Meanwhile, `@dq-rule-writer` only writes rules based on EDA observations ("val is never null") rather than reasoning about what COULD go wrong ("what if two rows have the same grain?"). The rules are reactive, not adversarial.

Result: the chaos monkey injects garbage values, DQ catches the garbage, both declare victory. Meanwhile, a duplicate row or a column swap would sail through undetected.

## Success Criteria

- [ ] Chaos monkey covers 10 corruption dimensions (currently 5)
- [ ] Cross-row corruptions implemented (duplicates, orphans, missing entities, missing periods)
- [ ] Semantic corruptions implemented (column swaps, entity mixing, temporal shifts)
- [ ] Distribution corruptions implemented (value spikes, sign flips, temporal gaps)
- [ ] `@dq-rule-writer` uses adversarial reasoning, not just EDA observations
- [ ] DQ rule templates include cross-row and semantic patterns
- [ ] After-Action Reports categorize gaps by corruption class, not just dimension
- [ ] All new code has tests

## Technical Design

### 1. Expand Corruption Strategies

**File:** `src/grist/infra/chaos_monkey/injector.py` (update)

Current strategies operate on **one cell at a time**. Add three new corruption classes that operate on **multiple rows** or **across columns**:

#### 1a. Row-Level Corruptions (new)

```python
class RowCorruptor:
    """Corruptions that affect whole rows or row relationships."""

    def duplicate_rows(self, records, rate, rng):
        """Insert exact duplicate rows. Tests uniqueness rules."""
        n = max(1, int(len(records) * rate))
        indices = rng.sample(range(len(records)), n)
        duplicates = [dict(records[i]) for i in indices]
        return records + duplicates, [
            CorruptionRecord(row_index=len(records) + i,
                           column="*", strategy="exact_duplicate",
                           dimension="Uniqueness")
            for i in range(len(duplicates))
        ]

    def near_duplicate_rows(self, records, rate, rng, grain_fields):
        """Insert rows that differ in one non-grain field.
        Tests whether DQ rules check grain vs non-grain correctly."""
        n = max(1, int(len(records) * rate * 0.5))
        indices = rng.sample(range(len(records)), n)
        near_dupes = []
        for i in indices:
            row = dict(records[i])
            # Change a non-grain field to make it look like a different fact
            non_grain = [k for k in row if k not in grain_fields]
            if non_grain:
                col = rng.choice(non_grain)
                row[col] = None  # subtly different
            near_dupes.append(row)
        return records + near_dupes, [...]

    def orphan_foreign_keys(self, records, rate, rng, fk_field, valid_values):
        """Set FK fields to values that don't exist in the parent table.
        Tests referential integrity rules."""
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), n))
        for i in indices:
            records[i][fk_field] = f"ORPHAN_{rng.randint(100000, 999999)}"
        return records, [...]

    def remove_entity(self, records, rng, entity_field):
        """Remove all rows for one entity. Tests coverage/volume rules."""
        entities = list(set(r[entity_field] for r in records))
        victim = rng.choice(entities)
        filtered = [r for r in records if r[entity_field] != victim]
        return filtered, [CorruptionRecord(
            column=entity_field, strategy="entity_removal",
            dimension="Coverage",
            original_value=str(victim),
        )]

    def remove_time_period(self, records, rng, period_field):
        """Remove all rows for one time period. Tests temporal coverage."""
        periods = list(set(r.get(period_field) for r in records if r.get(period_field)))
        victim = rng.choice(periods)
        filtered = [r for r in records if r.get(period_field) != victim]
        return filtered, [...]
```

#### 1b. Semantic Corruptions (new)

```python
class SemanticCorruptor:
    """Corruptions that are structurally valid but semantically wrong."""

    def swap_columns(self, records, rate, rng, column_pairs):
        """Swap values between related columns.
        E.g., swap revenue and net_income — structurally valid (both doubles)
        but semantically wrong."""
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), n))
        col_a, col_b = rng.choice(column_pairs)
        for i in indices:
            records[i][col_a], records[i][col_b] = records[i][col_b], records[i][col_a]
        return records, [...]

    def mix_entities(self, records, rate, rng, entity_field, value_fields):
        """Assign one entity's values to another entity.
        Apple gets Microsoft's revenue — structurally valid, semantically wrong."""
        entities = list(set(r[entity_field] for r in records))
        if len(entities) < 2:
            return records, []
        source, target = rng.sample(entities, 2)
        source_rows = [r for r in records if r[entity_field] == source]
        n = max(1, int(len(source_rows) * rate))
        for i, row in enumerate(records):
            if row[entity_field] == target and i < n:
                donor = rng.choice(source_rows)
                for vf in value_fields:
                    row[vf] = donor.get(vf)
        return records, [...]

    def shift_temporal(self, records, rate, rng, date_fields, shift_days):
        """Shift dates by a fixed amount. Makes temporal joins fail."""
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), n))
        from datetime import timedelta
        for i in indices:
            for df in date_fields:
                if records[i].get(df) and hasattr(records[i][df], 'year'):
                    records[i][df] = records[i][df] + timedelta(days=shift_days)
        return records, [...]
```

#### 1c. Distribution Corruptions (new)

```python
class DistributionCorruptor:
    """Corruptions that change statistical properties without
    obviously corrupting individual values."""

    def spike_values(self, records, rate, rng, value_field, spike_value):
        """Set many values to the same number. Kills variance.
        Tests distribution-based DQ rules."""
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), n))
        for i in indices:
            records[i][value_field] = spike_value
        return records, [...]

    def flip_signs(self, records, rate, rng, value_field):
        """Negate values. Revenue becomes negative revenue.
        Tests domain-specific validity rules."""
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), n))
        for i in indices:
            v = records[i].get(value_field)
            if v is not None and isinstance(v, (int, float)):
                records[i][value_field] = -v
        return records, [...]

    def uniform_dates(self, records, rate, rng, date_field):
        """Set all dates to the same day. Kills temporal distribution."""
        from datetime import date
        target_date = date(2020, 1, 1)
        n = max(1, int(len(records) * rate))
        indices = set(rng.sample(range(len(records)), n))
        for i in indices:
            records[i][date_field] = target_date
        return records, [...]
```

### 2. Schema-Aware Corruption Selection

**File:** `src/grist/infra/chaos_monkey/injector.py` (update)

The current injector randomly picks columns and strategies. The new injector should be **schema-aware**:

```python
class SchemaAwareInjector:
    """Selects corruption strategies based on table semantics, not just types."""

    def __init__(self, schema, grain_fields, fk_fields=None, value_fields=None):
        self.schema = schema
        self.grain_fields = grain_fields      # for duplicate/near-duplicate injection
        self.fk_fields = fk_fields or []      # for orphan key injection
        self.value_fields = value_fields or [] # for column swap, sign flip

    def plan_corruptions(self, records, config):
        """Plan a balanced set of corruptions across all 10 dimensions."""
        plan = []
        # Ensure every dimension is covered:
        # 1. Completeness — null injection (existing)
        # 2. Validity — type-inappropriate values (existing)
        # 3. Accuracy — truncation, subtle errors (existing)
        # 4. Reasonableness — extreme values (existing)
        # 5. Freshness — temporal corruption (existing)
        # 6. Uniqueness — duplicate rows (NEW)
        # 7. Referential Integrity — orphan FKs (NEW)
        # 8. Coverage — entity/period removal (NEW)
        # 9. Consistency — column swaps, entity mixing (NEW)
        # 10. Distribution — value spikes, sign flips (NEW)
        return plan
```

The injector reads the table's grain fields (from the data contract or grain definition) to know WHAT to duplicate, WHICH foreign keys to orphan, and WHICH columns can be meaningfully swapped.

### 3. Adversarial DQ Rule Writing

**File:** `.claude/agents/dq-rule-writer.md` (update)

Current approach: "EDA says val is never null → write a null check."

New approach: **reason about failure modes, not just observations.**

#### 3a. Adversarial Rule Categories

Add to the agent definition:

```markdown
## Adversarial Rule Writing Protocol

Before writing rules, answer these questions for each table:

### Structural Integrity
- What is the declared grain? Write a uniqueness rule for it.
- What foreign keys exist? Write a referential integrity rule for each.
- What columns are derived from other columns? Write a consistency rule.

### Semantic Validity
- What values are impossible in this domain? (e.g., negative revenue for a non-loss scenario)
- What cross-column relationships must hold? (e.g., total = sum of parts)
- What temporal ordering is required? (e.g., start < end, filed > period_end)

### Distribution Expectations
- What is the expected row count range per entity?
- What is the expected value distribution? (min, max, median from EDA)
- What temporal coverage is expected? (every entity has data for every period?)

### Coverage Guarantees
- Are all expected entities present?
- Are all expected time periods covered?
- Are all expected metrics/concepts populated?

For each question, write a DQ rule OR document why it doesn't apply.
This is NOT optional — every question must be addressed in the audit trail.
```

#### 3b. DQ Rule Templates: Cross-Row and Semantic Patterns

Add to `governance/dq-rule-templates/adversarial-patterns.json`:

```json
[
  {
    "pattern_id": "ADV-GRAIN-UNIQUE",
    "category": "Uniqueness",
    "priority": "P0",
    "description": "No duplicate rows at the declared grain",
    "sql_template": "SELECT COUNT(*) FROM (SELECT {grain_fields}, COUNT(*) AS cnt FROM {table} GROUP BY {grain_fields} HAVING cnt > 1)",
    "threshold": "result = 0",
    "mandatory": true
  },
  {
    "pattern_id": "ADV-FK-VALID",
    "category": "Referential Integrity",
    "priority": "P0",
    "description": "All FK references resolve to existing parent rows",
    "sql_template": "SELECT COUNT(*) FROM {child_table} c LEFT JOIN {parent_table} p ON {join_condition} WHERE p.{parent_key} IS NULL",
    "threshold": "result = 0",
    "mandatory_when": "table has foreign key relationships"
  },
  {
    "pattern_id": "ADV-TEMPORAL-ORDER",
    "category": "Consistency",
    "priority": "P0",
    "description": "Start date must be before or equal to end date",
    "sql_template": "SELECT COUNT(*) FROM {table} WHERE {start_col} > {end_col}",
    "threshold": "result = 0",
    "mandatory_when": "table has start/end date columns"
  },
  {
    "pattern_id": "ADV-ENTITY-COVERAGE",
    "category": "Coverage",
    "priority": "P1",
    "description": "All expected entities are present",
    "sql_template": "SELECT COUNT(DISTINCT {entity_field}) FROM {table}",
    "threshold": "result >= {expected_entity_count}",
    "mandatory": true
  },
  {
    "pattern_id": "ADV-PERIOD-COVERAGE",
    "category": "Coverage",
    "priority": "P1",
    "description": "All expected time periods are present per entity",
    "sql_template": "SELECT COUNT(*) FROM (SELECT {entity_field}, COUNT(DISTINCT {period_field}) AS periods FROM {table} GROUP BY {entity_field} HAVING periods < {min_periods})",
    "threshold": "result = 0",
    "mandatory_when": "table has temporal grain"
  },
  {
    "pattern_id": "ADV-VALUE-RANGE",
    "category": "Reasonableness",
    "priority": "P1",
    "description": "Values must be within expected range (from EDA)",
    "sql_template": "SELECT COUNT(*) FROM {table} WHERE {value_col} < {eda_min} OR {value_col} > {eda_max}",
    "threshold": "result = 0"
  },
  {
    "pattern_id": "ADV-DISTRIBUTION-VARIANCE",
    "category": "Reasonableness",
    "priority": "P2",
    "description": "Value variance must be non-zero (detect value spiking)",
    "sql_template": "SELECT VARIANCE({value_col}) FROM {table}",
    "threshold": "result > 0",
    "mandatory_when": "table has numeric value columns"
  },
  {
    "pattern_id": "ADV-CROSS-COLUMN",
    "category": "Consistency",
    "priority": "P1",
    "description": "Cross-column business rules hold (domain-specific)",
    "note": "Domain project defines specific cross-column rules (e.g., total = sum of parts). Template reminds @dq-rule-writer to consider these.",
    "mandatory_when": "table has derived or related columns"
  }
]
```

### 4. Chaos Monkey → DQ Rule Writer Feedback Loop

**File:** `.claude/agents/chaos-monkey.md` (update)

The After-Action Report currently says "Accuracy dimension MISSED." That's not actionable enough. The new report should include **specific rule recommendations**:

```markdown
## Gaps Found

### Uniqueness (MISSED)
- Injected 50 exact duplicate rows
- No DQ rule checks grain uniqueness
- **Recommended rule:** `SELECT COUNT(*) FROM (SELECT {grain}, COUNT(*) FROM {table} GROUP BY {grain} HAVING COUNT(*) > 1)`

### Referential Integrity (MISSED)
- Injected 30 orphan foreign keys in column 'tag'
- FK references base.tag_metadata but no DQ rule validates this
- **Recommended rule:** `SELECT COUNT(*) FROM {table} f LEFT JOIN base.tag_metadata t ON f.taxonomy = t.taxonomy AND f.tag = t.tag WHERE t.taxonomy IS NULL`

### Coverage (MISSED)
- Removed all rows for entity CIK=320193
- No DQ rule checks entity completeness
- **Recommended rule:** `SELECT COUNT(DISTINCT cik) FROM {table}` with threshold >= {expected}
```

This makes the feedback loop concrete: the chaos monkey doesn't just say "you missed this dimension" — it gives the `@dq-rule-writer` a ready-to-use SQL rule.

### 5. Updated 10-Dimension Coverage

| # | Dimension | Current | New |
|---|-----------|---------|-----|
| 1 | Completeness | Null injection | Null injection (unchanged) |
| 2 | Validity | Invalid values (unicode, negative, NaN) | Invalid values (unchanged) |
| 3 | Accuracy | Truncation, zero values | Truncation, zero values (unchanged) |
| 4 | Reasonableness | Extreme values | Extreme values + distribution spikes |
| 5 | Freshness | Future/past dates | Future/past dates + temporal shifts |
| 6 | **Uniqueness** | **Not tested** | **Exact duplicates, near-duplicates** |
| 7 | **Referential Integrity** | **Not tested** | **Orphan foreign keys** |
| 8 | **Coverage** | **Not tested** | **Entity removal, period removal** |
| 9 | **Consistency** | **Not tested** | **Column swaps, entity mixing** |
| 10 | **Distribution** | **Not tested** | **Value spikes, sign flips, uniform dates** |

The current chaos monkey tests 5 of 10 dimensions. The new one tests all 10.

### 6. Configuration: Grain and Relationship Awareness

The chaos monkey needs to know the table's grain and relationships to inject meaningful corruptions. This comes from the **data contract** (from `data-contracts.md` spec):

```python
# Chaos monkey reads the contract to plan corruptions:
contract = load_contract("consumable-company-financials")
grain_fields = contract["schema"]["grain"]["columns"]  # [cik, fy, fp]
fk_relationships = contract["lineage"]["sources"]       # base.financial_facts, base.tag_metadata

injector = SchemaAwareInjector(
    schema=table.schema(),
    grain_fields=grain_fields,
    fk_fields=["taxonomy", "tag"],  # from contract lineage
    value_fields=["revenue", "net_income", "total_assets"],  # from contract schema
)
```

If no contract exists (raw zone), fall back to the current type-based corruption.

## Tests

- `tests/infra/test_row_corruptor.py`:
  - `test_duplicate_rows_adds_exact_copies`
  - `test_near_duplicate_differs_in_non_grain_field`
  - `test_orphan_fk_creates_invalid_references`
  - `test_entity_removal_drops_all_rows_for_one_entity`
  - `test_period_removal_drops_all_rows_for_one_period`

- `tests/infra/test_semantic_corruptor.py`:
  - `test_column_swap_exchanges_values`
  - `test_entity_mix_assigns_wrong_values`
  - `test_temporal_shift_moves_dates`

- `tests/infra/test_distribution_corruptor.py`:
  - `test_value_spike_sets_uniform_values`
  - `test_sign_flip_negates_values`
  - `test_uniform_dates_kills_temporal_variance`

- `tests/infra/test_schema_aware_injector.py`:
  - `test_covers_all_10_dimensions`
  - `test_uses_grain_fields_for_duplicates`
  - `test_uses_fk_fields_for_orphans`
  - `test_falls_back_to_type_based_without_contract`

## Relationship to Other Specs

- **data-contracts.md**: Chaos monkey reads contracts for grain fields and FK relationships
- **framework-quality-parity.md (Change 3)**: Adversarial DQ patterns feed the mandatory consumable DQ templates
- **idempotent-promote-pattern.md**: Grain definitions used by both promote dedup and chaos monkey duplicate injection

## Implementation Order

1. Row-level corruptors (duplicates, orphans, entity/period removal)
2. Semantic corruptors (column swap, entity mix, temporal shift)
3. Distribution corruptors (spike, sign flip, uniform dates)
4. SchemaAwareInjector (reads contracts for grain/FK awareness)
5. Adversarial DQ rule templates
6. Updated After-Action Report with specific rule recommendations
7. Updated `@dq-rule-writer` adversarial protocol
