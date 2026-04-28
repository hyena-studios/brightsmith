## Staff Engineer Review

### Date: 2026-03-29
### Reviewer: @staff-engineer
### Status: CHANGES REQUESTED

### Verdict

This spec is directionally correct and solves a real problem. The dual-write pattern is technical debt, and making Iceberg authoritative is the right call. The governance reviewer did a thorough job forcing completeness on the first pass, and the v2 spec addresses those gaps. However, there are structural issues with the spec itself that will cause implementation pain. The biggest problem is scope -- this is not one spec, it is three, and pretending otherwise will produce a messy, hard-to-review, hard-to-test implementation. I also have concerns about the `dq_rules` grain design, the `pipeline_gate.py` migration strategy, and the complete absence of a test plan for a change that touches the governance system's nervous system.

I would not approve this for implementation as-is. The issues below are fixable without rewriting the spec -- mostly splitting it and adding specificity where there is hand-waving.

### Architectural Assessment

**Iceberg as sole source of truth: Sound.** The schema designs are reasonable. The `documents` catch-all table is fine -- it is a document store, not a relational model, and `doc_type` filtering is adequate. The sentinel value approach for deprecated columns (`iceberg://governance.contract_metadata`) avoids schema evolution headaches while making deprecation explicit. The two-phase migration with rollback is the correct approach.

**Grain/dedup strategy: Mostly sound, one real problem.**

- `cab_decisions` grain `[decision_id]` -- correct, immutable identifier.
- `run_history` grain `[run_id]` -- correct.
- `chaos_manifests` grain `[run_id]` -- correct.
- `documents` grain `[doc_type, doc_name, version]` -- correct, version allows append-only history.
- `golden_datasets` grain `[spec_name, column_name, filters]` -- the deterministic serialization note is good. Enforcing sorted keys at the write function is the right place.
- `dq_rules` grain `[spec_name, rule_id, updated_at]` -- **problematic.** See Issue #1.

### Code Quality

Not applicable -- this is a spec review, not a code review. But I have read all 13 modules being migrated and the existing `governance_db.py` implementation. The current code is clean, the `_write_records()` / `promote()` pattern works, and extending it to 6 new tables is mechanical. The `_query_table()` pattern (load to Arrow, query with DuckDB) is adequate for governance-scale data.

### Test Quality

**There is no test plan.** The spec says "All existing tests pass + new tests for each new table" (success criteria line 473). That is not a test plan. For a change that touches 13 modules and 14 Iceberg tables -- including `pipeline_gate.py`, which is the single most critical module in the framework -- "new tests for each new table" is insufficient. See Issue #3.

### Spec Compliance

The spec is internally consistent after the v2 revisions. The governance reviewer was thorough. The three advisory findings (staging_proposal missing from doc_type enum, implicit data-dictionary migration, dense success criteria) are all accurate and should be addressed.

### Issues

| # | Severity | Category | Issue | Required Fix |
|---|----------|----------|-------|-------------|
| 1 | BLOCKING | Schema | **`dq_rules` grain with `updated_at` breaks idempotency for rule approval.** The grain is `[spec_name, rule_id, updated_at]`. When a rule is approved, the write function calls `datetime.now()` for `updated_at`, producing a unique grain hash every time. This means re-running approval writes a duplicate row every time. The spec says "promote() skips it" but that is false -- `updated_at` changes with every call, so the grain hash is always new. The "deduplicate by rule_id keeping latest updated_at" read pattern works for queries but does not prevent unbounded row growth on repeated writes. Either (a) make the grain `[spec_name, rule_id, status]` so that re-approving an already-approved rule is idempotent, or (b) use a `version` integer instead of `updated_at` in the grain so repeated writes with the same version are deduped. Option (b) is better because it also handles the case where a rule is modified without a status change. | Change grain to `[spec_name, rule_id, version]` where version is a monotonically increasing integer. Approval increments version. Re-running with the same version is a no-op via promote(). Document how the write function determines the next version (query max version for that rule_id, increment). |
| 2 | BLOCKING | Scope | **This spec must be split.** 13 module changes, 6 new tables, 4 schema evolutions, CLAUDE.md overhaul, 15 agent definition updates. The spec itself estimates 10 sub-tasks. A single PR for all of this is unreviewable. The two-phase migration already provides a natural split boundary. Proposed split: **Spec A** = new tables + schemas + write/query functions in `governance_db.py` + migration function. **Spec B** = switch all 13 module readers/writers to Iceberg, including `pipeline_gate.py`. **Spec C** = CLAUDE.md + agent definition documentation updates + `setup.py` cleanup + config deprecation. Each spec is independently testable, reviewable, and rollback-safe. Spec A can be validated without changing any module behavior. Spec B is where the risk is and gets the most scrutiny. Spec C is documentation-only. | Split into 3 specs. They can share this parent spec as the design document but must be implemented and reviewed independently. |
| 3 | BLOCKING | Testing | **No test strategy for the most critical module.** `pipeline_gate.py` has 1124+ lines of state machine logic. It reads JSON state files, reads DQ rule files, reads golden dataset files, reads CAB decision indexes, reads physical model files, and reads contract directories. Every one of these file reads must be replaced with an Iceberg query. The spec does not describe how to test that the replacement queries return identical results to the file reads. Required: (a) a golden test that captures current `validate()` output for a known spec state, (b) tests that the new Iceberg-backed `validate()` produces identical output, (c) tests for `check_zone_transition()` with Iceberg backend, (d) tests that `audit_report()` generates identical markdown from Iceberg queries. Without these, there is no way to verify the migration did not break the gate. | Add a test strategy section to the spec (or to Spec B if split). Minimum test requirements: 10 tests for `governance_db.py` new tables (write + query + idempotency per table), 15 tests for `pipeline_gate.py` migration (validate, check_transition, audit, each file read replaced), 5 tests for each remaining module migration (dq_runner, cab, contract, golden_dataset, chaos_monkey). Total minimum: ~65 tests. |
| 4 | HIGH | Migration | **`pipeline_gate.py` `_load()` and `_save()` are the riskiest change and need explicit design.** The current `PipelineGate` class loads state from a JSON file in `__init__` and saves on every mutation. The spec says "Replace `_load()` file read: Query Iceberg for current pipeline state." But the pipeline state is not a single table row -- it is a JSON document with nested `steps`, `skipped_steps`, and `approvals` dictionaries. Reconstructing this from `pipeline_events` requires aggregating all events for a spec, deduplicating by step_name, and assembling the nested structure. This is a non-trivial query that must be exactly right or the state machine breaks. The spec does not describe this reconstruction logic. | Add a "Pipeline State Reconstruction" subsection under `pipeline_gate.py` Module Changes. Document the exact query pattern: how `steps` dict is built from `pipeline_events`, how `skipped_steps` is derived, how `approvals` is derived, and how the `zone`/`mode`/`started` metadata is stored (currently in the JSON root, needs to go somewhere in Iceberg -- probably `spec_registry`). |
| 5 | HIGH | Completeness | **`pipeline_gate.py` validate function reads 5 different file paths that are not fully enumerated.** Lines 617-698 of `pipeline_gate.py` read: (1) `DQ_RULES_DIR/{spec}.json`, (2) `GOLDEN_DATASETS_DIR/{spec}-golden.json`, (3) `governance/models/{spec}-physical.md`, (4) `governance/data-contracts/*.yaml` via glob, (5) `governance/cab-decisions/index.json`. The spec's Module Changes for `pipeline_gate.py` mentions `validate()` but does not list these specific file reads or describe their Iceberg replacements. Each one is a different table with a different query pattern. | Enumerate all 5 file reads in the `pipeline_gate.py` Module Changes section and map each to its Iceberg replacement: (1) `governance.dq_rules WHERE spec_name = $1`, (2) `governance.golden_datasets WHERE spec_name = $1`, (3) `governance.documents WHERE doc_type='model' AND doc_name = '{spec}-physical'`, (4) `governance.contract_metadata WHERE zone = $1`, (5) `governance.cab_decisions WHERE spec_name = $1 AND decision = 'PENDING'`. |
| 6 | MEDIUM | Schema | **`governance.dq_rules` is missing `table_name` in the read pattern documentation.** The spec says "To get current rules for a spec, query `WHERE spec_name = $1` and deduplicate by `rule_id`." But `dq_runner.py` loads rules by spec name AND filters by table name (line 54: reads `governance/dq-rules/{spec}.json` where the JSON contains rules scoped to a specific table). The Iceberg query needs `WHERE spec_name = $1 AND table_name = $2` for the common case. Minor, but the read pattern doc should be accurate. | Update read pattern to include table_name filter option. |
| 7 | MEDIUM | Completeness | **`glossary_validator.py` keeps reading the file, but `contract.py` switches to Iceberg for glossary lookups.** This means two code paths for glossary data. When the glossary file is updated, `contract.py` will not see the change until someone syncs the file to Iceberg. The spec acknowledges this tension (Decision #6) but does not describe the sync trigger. Currently, `governance_db.sync_glossary_term()` exists but is called manually. After migration, when does the sync happen? On every `contract verify`? On every pipeline run? If the file is authoritative, the sync must be automatic and reliable. | Document the sync trigger: either (a) `contract.py` reads the file directly (same as glossary_validator), or (b) there is an automatic sync step at the start of every pipeline run. Option (a) is simpler and avoids the staleness problem. |
| 8 | MEDIUM | Design | **`--from-files` fallback only covers 3 CLI commands.** The spec lists `dq_runner status`, `pipeline_gate status`, and `contract list`. But `pipeline_gate validate` and `pipeline_gate check-transition` are equally critical and have no fallback. If the governance catalog is corrupted mid-pipeline, you cannot validate or transition zones. These are the commands you need most when things are broken. | Add `--from-files` to `pipeline_gate validate` and `pipeline_gate check-transition`. |
| 9 | LOW | Completeness | **Advisory A from v2 review not addressed: `staging_proposal` missing from `doc_type` enum.** The `documents` table description (line 200) lists 8 doc_types but `staging_proposal` is not among them. The `staging.py` Module Changes reference it. Internal inconsistency. | Add `staging_proposal` to the doc_type list on line 200. |
| 10 | LOW | Design | **`dq_rule_results` acknowledged fields should use a separate grain.** Adding `acknowledged`, `acknowledged_by`, `acknowledged_reason` to `dq_rule_results` means that acknowledging a failure modifies an existing row. But the grain is `[run_id, rule_id]` and the row already exists. Acknowledging is a mutation, not an append. With promote()'s dedup, you cannot update an existing row -- you can only skip it. The spec does not address how acknowledgment works with immutable Iceberg appends. | Either (a) make acknowledgment a new row with a different grain (add `acknowledged` to grain fields), or (b) use a separate `dq_acknowledgments` table with grain `[run_id, rule_id]`, or (c) document that acknowledgment uses Iceberg overwrite_rows instead of promote(). Option (b) is cleanest. |

### What is Acceptable

- The two-phase migration design is correct and was a good fix from the v1 review.
- The `documents` catch-all table is pragmatic. Separate tables per doc_type would be over-engineering.
- Sentinel values for deprecated columns are the right approach for Iceberg schema evolution.
- The Brightforge query surface table is useful and complete.
- Keeping `business-glossary.json` and `collision-rules.json` as files is the right call.
- The governance reviewer caught real issues on the first pass and the spec author addressed them seriously.

### Implementation Order Recommendation (if split into 3 specs)

1. **Spec A: governance_db.py expansion** -- Add all 6 new schemas, table configs, write/query functions. Add schema evolution for existing tables. Add `migrate_files_to_iceberg()`. Write tests for every new write/query function. Validate migration against existing project data. This spec is low-risk and provides the foundation.

2. **Spec B: Module migration** -- Switch all 13 modules. Start with the lowest-risk modules (lineage.py, staging.py, chaos_monkey/manifest.py, run.py) to build confidence. Then dq_runner.py and dq_scorecard.py. Then cab.py and contract.py. Then golden_dataset.py. Save pipeline_gate.py for last -- it is the most complex and most critical. Test each module individually before moving to the next.

3. **Spec C: Documentation and cleanup** -- CLAUDE.md, 15 agent definitions, setup.py, config.py. This is the least risky but most tedious. Do it last so the documentation reflects the actual final state, not a planned future state.

### Note on Spec Status

This is a DRAFT infrastructure spec. It has not been implemented. My review is a pre-implementation design review, not a code review. The issues above must be resolved in the spec before implementation begins. Once the spec is updated (or split), I will re-review.
