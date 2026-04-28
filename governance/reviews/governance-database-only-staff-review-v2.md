## Staff Engineer Review (v2)

### Date: 2026-03-29
### Reviewer: @staff-engineer
### Status: APPROVED

### Verdict

The spec addresses all 10 issues from my first review. The three-spec split is correct. The pipeline state reconstruction section is detailed enough to implement without guessing. The dq_rules version-based grain fixes the idempotency problem. The dq_acknowledgments table is the right call -- append-only semantics preserved, no row mutation gymnastics. The test strategy with 70 minimum tests and explicit parity testing for pipeline_gate.py's 5 file reads is what I asked for.

This is a sound design document. I would put my name on it as an architecture spec. The real test is implementation -- Spec B (module migration) is where the risk lives, and I will review that sub-spec with extreme scrutiny when it arrives.

### Original Issue Resolution

| # | Severity | Issue | Status | Notes |
|---|----------|-------|--------|-------|
| 1 | BLOCKING | `dq_rules` grain with `updated_at` breaks idempotency | RESOLVED | Grain changed to `[spec_name, rule_id, version]` with integer version. Write function queries `MAX(version)` and increments. Re-running same version is a no-op. Lines 95-108 document the read pattern correctly. |
| 2 | BLOCKING | Spec must be split | RESOLVED | Split into parent design doc + 3 sub-specs (A: db expansion, B: module migration, C: docs cleanup). Each has independent success criteria at lines 627-665. Sub-spec files don't exist yet -- that's expected, they'll be created at implementation time. |
| 3 | BLOCKING | No test strategy | RESOLVED | Test Strategy section at lines 561-625. 25 tests for Spec A, 45 for Spec B, ~70 total. pipeline_gate.py gets 15 tests including state reconstruction fidelity and validate() parity for all 5 file reads. The per-module breakdown is specific enough to hold the implementer accountable. |
| 4 | HIGH | Pipeline state reconstruction not designed | RESOLVED | Full "Pipeline State Reconstruction" section at lines 291-330. Maps every JSON field to its Iceberg source with exact query patterns. The 6-step `_load()` reconstruction algorithm is clear. The note that this is the riskiest migration (line 330) is honest. |
| 5 | HIGH | validate() file reads not enumerated | RESOLVED | Table at lines 332-344 enumerates all 5 file reads with their Iceberg replacements. Each maps to a specific table, specific WHERE clause, and specific pass/fail condition. |
| 6 | MEDIUM | dq_rules read pattern missing table_name | RESOLVED | Lines 109-110: "To get current rules for a specific table: add `AND table_name = $2` to both the outer and inner query." Also reflected in dq_runner.py module changes at line 354. |
| 7 | MEDIUM | Glossary sync trigger undefined | RESOLVED | Decision #6 (line 73) clarified: contract.py reads file directly. Line 441: automatic sync at pipeline start in run.py. glossary_validator.py unchanged (line 427). Three clear paths: file-direct for validators, Iceberg for Brightforge, auto-sync at pipeline entry. No staleness window during a single pipeline run. |
| 8 | MEDIUM | --from-files only covers 3 CLI commands | RESOLVED | Line 77-83: 5 commands now listed (dq_runner status, pipeline_gate status, pipeline_gate validate, pipeline_gate check-transition, contract list). Line 379 confirms validate and check-transition get the flag. |
| 9 | LOW | staging_proposal missing from doc_type enum | RESOLVED | Line 248: doc_type list now includes `staging_proposal`. |
| 10 | LOW | dq_rule_results acknowledgment breaks append-only | RESOLVED | Separate `governance.dq_acknowledgments` table at lines 131-147 with grain `[run_id, rule_id]`. Design Decision #8 (line 88) explains the rationale. LEFT JOIN pattern documented. Clean solution. |

### New Issues

None. The updates are clean and don't introduce new problems. Two observations that are advisory, not blocking:

1. **spec_registry grain includes updated_at (line 204 of governance_db.py).** The existing `spec_registry` grain is `[spec_name, status, updated_at]`, which has the same timestamp-in-grain problem I flagged for dq_rules. This predates this spec and is out of scope, but it should be addressed in Spec A as a low-priority schema evolution. The spec author should be aware.

2. **Sub-spec files need to exist before implementation begins.** The parent doc references `governance-db-expansion.md`, `governance-module-migration.md`, and `governance-docs-cleanup.md` but they don't exist yet. The implementer should create these as lightweight specs (referencing this parent for the design) before starting work, so each sub-spec goes through its own review cycle.

### Implementation Order Recommendation

Confirmed: A then B then C, as described in the spec's Migration Path section (lines 496-526).

Within Spec B, the pipeline_gate.py migration should be last, with the low-risk modules first (lineage.py, staging.py, chaos_monkey/manifest.py, run.py) to build confidence in the pattern before touching the state machine.

### Approval Conditions

This approval is for the **parent design document only**. Each sub-spec (A, B, C) will require its own implementation review when code is written. Spec B in particular will get a thorough code review -- the pipeline state reconstruction logic and the 5 validate() replacements are the kind of thing that looks right on paper and breaks in practice.
