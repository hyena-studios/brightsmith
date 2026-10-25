# Spec: consumer-audit-phase0-silent-failures

**Status:** COMPLETE (2026-10-24 — WP-0.1 through WP-0.9 and the code half of WP-0.10 implemented; @governance-reviewer and @staff-engineer both APPROVED on re-review after 6 blocking findings were fixed. Merged to `main` and tagged `v0.5.0` on 2026-10-24.)
**Zone:** Infrastructure (cross-cutting)
**Created:** 2026-10-24
**Source:** `docs/technical-audit-2026-09-08.md` §10 Phase 0 (items 1–10), with evidence in §2, §3, §6, §7
**Related Specs:** `audit-remediation-open-source-readiness.md`, `idempotent-promote-pattern.md`, `lineage-maturity.md`

---

## Problem

The 2026-09-08 consumer-side audit found that the framework's worst defects are silent: a
wrong result is reported exactly like a correct one. Phase 0 of its plan is the set of
small fixes that make each of those failures loud. Nothing in this spec adds new
infrastructure; Phases 1–3 of the audit are out of scope.

## Work packages

| WP | Audit item | Change | Behaviour after |
|---|---|---|---|
| 0.1 | 1 | Relocation guard flags an absolute `metadata_location` outside the current project root / warehouse even when the file still exists | A clone whose original still exists raises `WarehouseRelocationError` instead of writing into the original |
| 0.2 | 2 | `promote()` hashes the non-grain payload; returns `{promoted, skipped, conflicts, snapshot_id}`; `on_conflict="error"\|"skip"\|"replace"`, default `error` | A corrected value for an existing grain raises `PromoteConflictError` (or is counted / replaced when asked) — never a silent skip |
| 0.3 | 3 | `dedup_grain` required (non-empty) at manifest load; `filter_existing_records` rejects a null id | Missing grain is a load-time `ValueError`; a null `record_id` is a `ValueError` |
| 0.4 | 4 | Promote lineage uses `table.name()`, writes into the table's own catalog, raises when `spec_name` is given; emitters accept `catalog=` and `strict=`; ingest lineage also targets the ingest catalog | Promote lineage events exist and round-trip; tests stop writing lineage into the project catalog |
| 0.5 | 5 | Golden MISMATCH / verification error sets a non-zero exit (`EXIT_GOLDEN_FAILURE = 5`); golden verification runs inside error handling | README's "blocks completion on mismatch" is true; a golden crash is a recorded failure, not a traceback |
| 0.6 | 6 | Scorecard reads `priority` from each result row; `dq_runs`/`dq_rule_results` gain `mode` (`real`/`shadow`/`imported`); latest-results / registry reads ignore non-`real`; synced JSON never defaults `p0_passed` to True | Shadow (chaos) and hand-written results can no longer overwrite the real DQ score or print a false P0 PASS |
| 0.7 | 7 | Read paths (`governance_db status`, `pipeline_gate validate`, `lineage verify`) never create tables | A read-only command leaves the consumer's catalog byte-identical |
| 0.8 | 8 | No-swallow meta-test catches tuples containing `Exception`, `BaseException`, dotted and aliased names; live instance fixed; promote lineage swallow removed from allowlist; DQ view-registration failure recorded on each affected rule instead of `pass` | The guard has no known bypass forms |
| 0.9 | 9 | Committed fixture warehouse with a foreign baked prefix + test that `relocate --check` exits 1 on it | The relocation detector is provably exercised in CI |
| 0.10 | 10 | `brightsmith.__version__` + a test that pyproject, plugin.json, marketplace.json and CHANGELOG agree; merge to `main` and tag `v0.5.0` after human sign-off | Version drift fails CI |

Also folded in (audit §7 P1): the uncommitted MCP `allowed_directories` widening gets a
test, catches `NoSuchTableError`, and a CHANGELOG line.

## Decisions

- **D1 — `on_conflict` defaults to `error`.** The audit's recommendation. This is
  BREAKING for any caller that relied on corrections being dropped; that reliance is the
  bug. Bronze `BaseIngestor` keeps grain-only dedup (append-only raw history) and is not
  changed by WP-0.2.
- **D2 — payload hash excludes framework columns.** `record_id`, `promoted_at`,
  `ingested_at`, `load_date` and any column named in `ignore_fields` are excluded so a
  re-run with a new timestamp is still a no-op.
- **D3 — `replace` is delete-then-append in one transaction** using PyIceberg's
  transaction API, so readers never see the grain missing.
- **D4 — golden failures get their own exit code (5)** rather than reusing DQ (1), so CI
  can tell them apart.

## Success criteria

- [x] Each WP has a test that fails before the change and passes after it
- [x] `ruff check src tests`, `pyright`, `pytest tests/ --cov-fail-under=75` all green (953 passed, 77.5%)
- [x] CHANGELOG lists every behaviour change, BREAKING items marked (under the untagged `[0.5.0]`)
- [x] @governance-reviewer and @staff-engineer reviews recorded (`governance/reviews/consumer-audit-phase0-silent-failures-{post,staff}-review.md`)

## Implementation log

2026-10-24. Every new test below was run against the pre-change code and failed for the documented reason, except where noted.

- **WP-0.1** `iceberg_setup._is_foreign` / `_allowed_roots`: foreign = absolute and under none of project root, the catalog's data directory, or the warehouse being opened. Tests: `test_relocate.py` copied-warehouse cases (load raises, original's files untouched, blanket scan, apply repairs). `relocate --check` already detected copies; only the runtime guard was wrong.
- **WP-0.2** `promote.py` rewritten: payload hash over the table-typed record (`records_to_arrow`, shared with `append_data`), `PromoteConflictError`, `on_conflict`, `ignore_fields`, single-transaction replace. Governance writers pass `on_conflict="skip"` (D1 does not extend to governance rows; see Discussion). Tests: `test_promote.py` (9 new).
- **WP-0.3** `domain_loader._resolve_dedup_grain` (top-level or per-table under a `tables:` mapping); `iceberg_setup._require_ids`. futureproof-data's 8 source files all satisfy the new rule. Tests: `TestDedupGrainRequired`, `test_null_record_id_is_rejected`.
- **WP-0.4** `lineage._emit` with `catalog=`/`strict=`; `_get_lineage_table(catalog)` keeps the project catalog's governance warehouse and gives other catalogs their own table. `BaseIngestor` passes its table's catalog. Tests: promote lineage round-trip, never-touches-project-catalog, raises-when-spec-named; ingest-into-scratch-catalog. Two ingestor attribution tests changed to ingest into the project catalog — they had relied on the cross-catalog leak.
- **WP-0.5** `run.py`: `EXIT_GOLDEN_FAILURE = 5`, `GoldenResult.error`, golden verification inside `try`. README exit-code line updated. Tests: 4 in `test_run_coverage.py`.
- **WP-0.6** `mode` column on `dq_runs` (field 20) and `dq_rule_results` (field 15), added to old tables on first write by `add_missing_columns`; `get_latest_dq_run` skips non-real; registry update only for real runs; sync writes `imported` and no longer defaults `p0_passed` to True; result rows carry `priority`; scorecard `_priority`/`_severity`. Tests: `test_dq_run_modes.py` (9). `test_result_has_all_fields` updated for the new `priority` key.
- **WP-0.7** `load_table_if_exists`; `_find_governance_table` / `_find_enterprise_table` / `_read_lineage_arrow` for reads. Also restored `governance_db`'s `__main__` (found while testing: the documented CLI was a no-op). Tests: `test_read_only_commands.py` drives 6 commands as subprocesses and compares catalog bytes.
- **WP-0.8** Meta-test detects tuple, `BaseException`, dotted and aliased handlers; promote/lineage allowlist entries removed; `concept_normalization/config.py` narrowed to `FileNotFoundError` (+ `tests/silver/test_concept_mappings_dir.py`); `dq_runner` records table-load errors on each affected rule (`_errored_result`) rather than raising — one unloadable table should fail its own rules, not abort every other rule's evidence.
- **WP-0.9** `tests/fixtures/relocated_project/` (built by its `build_fixture.py`, baked at `/srv/original-project`), `.gitignore` exception, CI step requires exit 1. The audit's mutant (`is_foreign_absolute` → `False`) now fails 3 tests.
- **WP-0.10** `brightsmith.__version__`, `tests/test_version_agreement.py`. Merge to `main` and the `v0.5.0` tag await sign-off.
- **MCP** `NoSuchTableError` caught alongside `OSError`; 3 tests in `TestQueryAllowlistAcrossWarehouses` (2 fail on the committed version).

Gate: ruff clean, pyright 0 errors, 944 passed, coverage 77.4%.

**Review round 1 (both CHANGES REQUESTED), fixed:**
- Staff 1: MCP allowlist admitted any loaded table's directory, so a crafted catalog row could expose project files to client SQL → `BaseMCPServer._trusted_table_root` (strictly inside the data or governance warehouse).
- Staff 2: `catalog_path.parent.parent` as an allowed root reopened the sibling-clone hole and could be `/` → roots are project root, catalog dir, configured warehouses; `/` and project ancestors dropped; relative `..` escapes are foreign.
- Staff 3: re-run materialized every matched row in Python (1.26 GB on 200k×30) → digests computed in DuckDB over a streamed scan (2.5 s, ~280 MB). Counts now: `conflicts` = distinct grain ids, `replaced` ⊆ conflicts.
- Gov 1: `_enrich_spec_registry` still read result files with the old P0 default → takes DQ fields from `get_latest_dq_run`.
- Gov 2: tests wrote into the repo's `data/` → autouse scratch-warehouse fixture + session-end guard; root cause in `config.py` (assigning a scalar re-derived and discarded path overrides).
- Gov 3: `docs/workflows/mcp-pipeline.md` exit code 5.

Final gate: ruff clean, pyright 0 errors, 953 passed, coverage 77.5%, repo `data/` untouched.

## Discussion

- **Governance rows keep first-write-wins.** `_write_records` and the enterprise writer pass `on_conflict="skip"`, so an edited glossary term or contract re-synced from files is still dropped, now counted under `conflicts`. Changing that is the governance-store redesign (audit Phase 3 item 26, and the open question of moving governance fully into the database), not a Phase 0 fix.
- **Docs describing result files as authoritative** (governance review, non-blocking) — fixed: `agents/governance-reviewer.md`, `skills/status/SKILL.md`, `agents/dq-engineer.md`, CLAUDE.md now point at `dq_runner results --spec`; `agents/lineage-tracker.md` states promote lineage needs `spec_name`.
- **Open non-blocking review notes:** promote lineage failing after the commit is not retried on re-run; `get_latest_dq_run` scans the last 100 runs; a `--zone bronze` run exits 5 on a stale gold golden mismatch.
- **CHANGELOG date.** Set to 2026-10-24 when `v0.5.0` was tagged.
