# Brightsmith — Consumer-Side Technical Audit (2026-09-08)

*Branch `audit-remediation` @ `19ca2d8` plus the uncommitted 0.5.0 working tree. Seven parallel reviewers plus direct verification; every load-bearing claim below was re-run by hand. No file in either repository was modified. This is the sixth audit of this repo and the first to judge it from the outside: through what happened to its only real consumer, FutureProof, whose data layer was audited the same day (`futureproof-data/reports/data-layer-audit-2026-09-08.md`).*

**The question asked:** FutureProof's governance layer drifted badly in five months. Did Brightsmith cause that, and is the framework sound enough to build a product on?

---

## Executive summary

**Overall: C−. Well built, wrongly shaped.** Engineering craft B; delivery on the README's promises D.

Brightsmith did not cause FutureProof's data problems, and it did cause most of its governance problems. Of seventeen framework-adjacent findings traced to their source, eight are framework defects still present at HEAD, three are fixed at HEAD but unreachable (FutureProof pins April-6 code plus a LICENSE, and every fix since June sits on the unmerged `audit-remediation` branch), four are traps the framework set, and two are FutureProof's own. The project's fault is narrower: it stopped feeding the framework in May and chose a regressive pin in August. Neither side would have got here alone.

The primitives are real and honestly tested. `promote` and grain hashing do what they say for an initial load and an idempotent re-run; the MCP surface is injection-hardened; the 889-test suite has zero skips and catches six of eight sabotage mutations; the remediation discipline across five prior audits is visible in the code. That craft is the reason this is not an F.

What is wrong sits underneath the pitch, and it is silent:

- A copied project whose original still exists **writes into the original's warehouse**, with no warning (§2, reproduced twice).
- A corrected value for an existing grain is **discarded and reported as a successful skip**, byte-identical to a healthy no-op (§2, reproduced twice).
- Promote lineage has **thrown and been swallowed on every call since March** because it reads an attribute pyiceberg does not have (§1, verified).
- The automatic DQ gate **evaluates zero rules and exits 0** for a consumer whose tables are not in the four medallion namespaces, which is every FutureProof table; five other routes report PASS on wrong data (§3).
- **No code path ever writes a spec status of COMPLETE**, and a spec validated COMPLETE stays COMPLETE after its table is dropped and recreated with different columns (§6).
- The operating model defines done as a hash-frozen pile of per-spec files, reports artifact counts as success, and has **no workflow for a source or a table changing** (§4). FutureProof's April-fitted rules under August-rebuilt tables were that model working as designed.
- Two CI gates announce protection they cannot provide: the relocation check cannot fail because nothing under `data/` is tracked, and a golden-dataset mismatch still exits 0 (§7).

The through-line is one habit and one design choice. The habit is catch-everything-and-warn, which turned lineage, the zone registry, and the spec registry into dashboards that stay green while empty. The design choice is that governance means artifacts whose existence is checked once, rather than checks that run on every write.

The fix is mostly routing and deletion, not new infrastructure. Phase 0 of §10 is a few days of one-function fixes that end the silent failures. Phase 1 puts DQ on the write path keyed by table and snapshot and makes `validate` look at the table. Phase 3 cuts 26 agents to eight and deletes the governance tables, the sync, and the state freeze that nothing reads. The three abstractions in Phase 2 (`ZoneTransformer`, `FetchHelper`, a query layer the consumer keeps) would move about a quarter of FutureProof's pipeline code into the framework and are what a second consumer would need to exist at all.

Grades by area: tests and gates B · domain-agnosticism B · reuse D+ · storage core D+ · DQ D · governance state D · operating model D.

---

## 1. Attribution: what Brightsmith did and did not cause

Seventeen framework-adjacent findings from the FutureProof audit were traced to their cause at the commit FutureProof pins (`6c5b120`) and at HEAD.

**The version story first, because it reframes everything.** `6c5b120` ("Create LICENSE", 2026-05-14) is `34bdbeb` (2026-04-06) plus a LICENSE file. All 17 newer commits, including v0.4.0 and v0.5.0, sit on the `audit-remediation` branch; `main` and `origin/main` are still at `3dce94b` (2026-05-14). Nothing installable from `main` carries any fix made since June. And the code FutureProof actually ran in April was neither of these: its April result files have the framework runner's exact schema with zero errored rules on `consumable.*` tables, which only works with the eight-namespace set that first appears in `c1f41f5`. April ran an unpublished sibling checkout. The pin chosen on 2026-08-31 regressed to a lineage that cannot execute any of FutureProof's 874 `raw.` / `base.` / `consumable.` rule references at all.

| # | FutureProof finding | Category | Evidence |
|---|---|---|---|
| 1 | `promote()` insert-only, no vintage in any grain, refresh keeps stale values | **A** defect at HEAD | `promote.py:47-59` identical at pin and HEAD; no `overwrite`/`vintage`/`source_version` anywhere in `src/`; `CLAUDE.md:71-72` mandates the pattern |
| 2 | Absolute paths baked into Iceberg metadata; project wrote its own rebase script | **B** fixed at HEAD, unreachable | `infra/relocate.py` added 2026-07-01, "generalized from futureproof-data's rebase_iceberg_paths.py"; `run.py:419-448` detects but never applies |
| 3 | 17 empty catalog files; per-zone catalogs | **C** pit | `get_catalog(warehouse, catalog)` mkdirs any pair it is handed and SqlCatalog lazily creates the `.db`; no guard against a catalog path other than `config.CATALOG_PATH`; 40 consumer call sites pass per-zone paths |
| 4 | Zero rows accepted; `source_method` overwritten; missing columns land as NULL | **A/C** | `base_ingestor.py:199`, `:220-222`; `iceberg_setup.py:354` None-fills missing keys while `:341-350` rejects extra ones |
| 5 | Zero promote events in `lineage_events` | **A** defect at HEAD | `promote.py:71` reads `table.identifier`, which does not exist on the locked pyiceberg 0.11.1 (verified: `hasattr(Table, "identifier")` is False); `:88-89` swallows to a warning; HEAD annotates it `# exists at runtime` (false). Shipping since March |
| 6 | DQ never runs on write; no snapshot id in results | **C/A** | `validate_after_write` has zero framework call sites; `promote()` returns the snapshot id and drops it; no `snapshot` string anywhere in `src/` |
| 7 | Two runner semantics (scalar vs row count) | **D** project | framework defines one semantics (`dq_runner.py:148-200`); the project reinvented it because at the pin `_KNOWN_NAMESPACES` could not run its rules at all (see 13) |
| 8 | Deactivated rules evaluated as P0 failures in August | **D** project | framework skipped deactivated rules at pin and HEAD; the runner that failed them is `fp:scripts/` |
| 9 | `verification run` crashes on `float('Low')`; `lineage verify` crashes | **A / B** | `golden_dataset.py:143` still `float(actual)` at HEAD and its parser reads `column`/`expected_value` while the consumer's files use `field`/`expected`; lineage crash fixed in 0.4.0 |
| 10 | `pipeline_gate validate` fails 23 of 24 specs | **A** defect at HEAD | `pipeline_gate.py:504-506`, `:649-656` hash each step's output and fail when the hash moved, no shared-artifact exemption; consumer state files name the dictionary in 13 specs, glossary in 12, domain-context in 7 |
| 11 | Spec registry all IN_PROGRESS; sync never ran; fixture events in the live catalog | **B / D / C** | `status="IN_PROGRESS"` hardcoded on every write at pin (fixed `b40d770`); sync is CLI-only at both (`governance/cli.py:74`) and the project stopped calling it 2026-05-07; `lineage.py:106-109` always writes to `config.CATALOG_PATH` while `ingest()` accepts an override it never passes through |
| 12 | Manifest unparseable, rebuild script bypasses it | **B** fixed at HEAD | `domain_loader.py:131` KeyError on `tables:`; HEAD `:148-183` accepts it, citing FutureProof's `onet.yaml` |
| 13 | 0.5.0 zone-alias removal breaks the consumer | **A** defect at HEAD | `scripts/migrate_zone_aliases.py:50-59` rewrites YAML/JSON text only, never `catalog.rename_table`; post-migration rules say `gold.x` while the catalog holds `consumable.x` and `_register_iceberg_views` fails on every table |
| 14 | 187 MB of dead parquet; no snapshot expiry | **A** defect at HEAD | no `expire_snapshots`/orphan code at pin or HEAD; CHANGELOG 0.5.0: "documentation only — no rotation code ships" |
| 15 | 10 of 259 result files carry a snapshot id | **A** defect at HEAD | `dq_runner.py:434-540` writes no snapshot id; scorecard per spec |
| 16 | Snapshot-fitted thresholds never re-fit; nothing detects drift | **C** pit | no baseline/trend in `dq_scorecard`; the only `check_drift` is `osi.py:455`, for the semantic model |
| 17 | Hardcoded row counts and vintages in scripts | **C** pit | `SourceConfig` has no version or expected-rows field; contracts honour only `min_row_count`; the consumer's 20 contracts declare `row_count_range`, which `verify_contract` silently ignores; the "no hardcoded entity data" rule points to `entity-registry.json`, a CIK/ticker construct |

**Counts:** A (defect still at HEAD) 8 · B (fixed at HEAD, unreachable from the pin or from `main`) 3 · C (design that makes the mistake likely) 4 · D (project's own) 2.

**Did the framework cause FutureProof's problems?** For the data, no: the mislabelled AURA copy, the draft RES weights, the marketing-ratio misreading, and the ERN prior are project decisions the framework never touched. For the governance layer, mostly yes. Eight findings are framework defects still present at HEAD, and the worst share one habit: "fault-tolerant" code that catches everything and logs a warning (`promote.py:87`, `run.py:194`, `dq_runner.py:505`), which turned lineage, the zone registry, and the spec registry into dashboards that stay green while empty. Three more are fixed at HEAD but unreachable, because the pin is April code and the fixes live on an unmerged branch. Four are pits the framework dug. The project's contribution is real but narrower: it stopped driving the framework's gate and runner after 2026-05-07, replaced them with about twenty scripts nobody re-runs, committed 17 empty catalogs, and chose the regressive pin itself. Brightsmith made the governance layer fragile and silent; FutureProof stopped feeding it and froze on a bad commit. Neither side would have got here alone.

---

## 2. Storage and ingest core — grade D+

This reviewer ran the code rather than reading it. Scratch experiments are reproducible (`exp_cross.py`, `exp_iceberg.py`, `exp_conc.py`, `exp_grain.py`, `exp_rebuild.py`, `exp_read2.py`); the two P0s were re-run by hand and reproduce exactly.

- **P0 — A clone whose original still exists reads and writes the original's warehouse, silently.** The relocation guard's only trigger is `if os.path.exists(meta_loc): return` (`iceberg_setup.py:237-238`, `:89-90`); the prefix check `_path_under` (`:42`) is written but never reached when the old path still exists. Copying a project, repointing config at the copy, and calling `promote()` landed the new parquet file in the original (verified: original 8 → 9 files, clone 8 → 8). Two checkouts on one laptop, a Docker build over a source tree, or a `cp -r` backup all silently share one physical table.
- **P0 — `promote()` silently discards value corrections and reports success.** The anti-join is on `record_id` only (`promote.py:50` → `iceberg_setup.py:437-453`). Promoting the same five grains with a corrected earnings value returned `{'promoted': 0, 'skipped': 5, 'snapshot_id': None}` and the table kept the old value. That return is byte-identical to a healthy idempotent no-op, so no caller can distinguish "nothing to do" from "your correction was thrown away". For a product whose sources restate prior-year values annually, this is silent wrong data with a green pipeline.
- **P0 — Omitting `dedup_grain` disables dedup entirely, silently.** `domain_loader.py:212` defaults it to `[]`; `base_ingestor.py:114-116` returns an empty set; `:208` skips the dedup block; every re-run appends the full source again. Same shape in `promote`: a record without `record_id` gets `None` and SQL `NULL = NULL` never matches, so one null-id row promoted three times grew the table by three.
- **P1 — The DQ gate runs after the write and there is no rollback.** `run.py:514-534` commits the zone, then `:541-559` runs DQ. Bad rows are visible to every reader, including the MCP server, before the gate returns, and since the only write path is append-only (no `.overwrite` or `.delete` anywhere in `src/`) a re-run cannot remove them. The gate protects the exit code, not the data product.
- **P1 — There is no refresh, correction, or rebuild API.** The public surface is `append_data`, `filter_existing_records`, `read_with_duckdb`, `get_snapshots`. The only rebuild guidance is a spec footnote (`docs/specs/idempotent-promote-pattern.md:227`: `table.delete(AlwaysTrue())` then promote), which is raw PyIceberg, untested, and non-atomic: delete commits one snapshot (0 rows) and promote commits the next. A crash in between leaves an empty Gold table live and readable.
- **P1 — `read_with_duckdb(limit=N)` costs 1.65 GB on the real Gold table.** Measured on 626,406 × 69: `limit=1000` alone 1,656 MB peak RSS; `columns=` alone 486 MB; both 198 MB; no arguments 5,500 MB. The docstring claims limit "stops the scan"; it does not, and `:401-406` round-trips through DuckDB for what `to_pylist()` does, doubling the peak. A 1 GB container OOMs on a preview query.
- **P1 — Concurrent promotes lose whole batches.** Four processes with disjoint grains: one succeeded, three raised `CommitFailedException`, 2,000 of 8,000 rows landed. It fails loudly (credit), but `append_data` has no retry loop and PyIceberg's `commit.retry.*` properties are never set. `BaseIngestor.ingest` commits one snapshot per entity, so a mid-loop failure leaves a partial table with no resume marker.
- **P2 — Grain hashing has real collisions and cross-run instability.** `grain.py:52` uses `str(row[f])`: `1` collides with `"1"`, `None` with `"None"`, `True` with `"True"`; yet `1` and `1.0` do not collide, so a column that reads as int one year and float64 the next (any pandas CSV read that picks up a null) rehashes the whole table and lands a full duplicate set. The docstring claims tuples "can never collide"; nine arity-3 collisions were found because the backslash is not escaped. Bronze's own dedup (`base_ingestor.py:140`) uses `str(row.get(f, ""))`, collapsing distinct rows on a missing field, which is exactly what `grain.py:44` raises to prevent.
- **P2 — Schema drift is asymmetric**: extra keys raise, missing declared columns land as NULL with no error. A source that drops a column on its annual refresh produces a silent null column.
- **P2 — The base class fights the chunking rule.** `ingest()` calls `fetch()` once and holds every payload in memory; there is no batch hook, no retry, no backoff, no cache in the base class.
- **P3** — `append_data` returns whichever snapshot is last after refresh, not the one it created; `relocate --apply` prints "0 rewritten" and exits 0 on a warehouse laid out anywhere it does not expect; snapshot expiry is documented and never invoked.

| Operation a yearly-rebuilt product needs | Supported | How |
|---|---|---|
| Initial load and idempotent re-run | yes | works, real tests, no mocks |
| Annual vintage of a source | partial | only if the vintage key is in the grain; nothing enforces or helps |
| Correct a value for an existing grain | **no** | silently dropped, reported as `skipped` |
| Rebuild a derived table after a formula change | partial | spec footnote, non-atomic |
| Relocate to a new machine | partial | works when the old path is gone; silently wrong when it still exists |
| Concurrent write | partial | loud failure, batch lost, no retry |
| Snapshot expiry, orphan cleanup | no | README snippet only |

Credit where due: `test_promote`, `test_grain`, `test_relocate`, `test_iceberg_roundtrip`, and `test_base_ingestor` contain zero mocks and drive real temp catalogs with real Iceberg writes. The coverage is honest and it is all happy path: the moved-warehouse fixture uses `shutil.move`, the one relocation case the guard handles; there is no copy case, no concurrent-writer case, no crash-between-delete-and-promote case, no correction case.

---

## 3. Data-quality and verification subsystem — grade D

The question was whether this subsystem can protect a rebuilt table without a human remembering to run a script. It cannot. The only automatic path is `python -m brightsmith.run`, and for a consumer whose tables are not in the four medallion namespaces it evaluates zero rules and exits 0. Six independent routes report PASS on wrong data.

- **P0 — The automatic zone gate passes with zero rules when namespaces are not `bronze/silver/gold/mcp`.** `run.py:607-611` returns success when `_rule_matches_zone` finds nothing; matching is a string compare against `_KNOWN_NAMESPACES` (`dq_runner.py:206,256`). A P0 uniqueness rule on `consumable.program_career_paths` with duplicate rows passed every zone; the same rule on `gold.*` failed correctly. `tests/infra/test_pipeline_runner.py:266-290` asserts SUCCESS for a failing `raw.*` P0 as the intended behaviour. 874 of the consumer's 901 rules reference the non-medallion names.
- **P0 — The scorecard prints "P0 Gate: PASS" for a failing P0 whenever rule metadata lookup misses.** `dq_scorecard.py:115-122` derives P0 status from a metadata lookup keyed on the file's top-level `spec` and ignores the `priority` already stored on each result row. A rules file without a top-level `spec` produced `p0_passed=False` from the runner and "P0 Gate: PASS" from the scorecard, with the failure listed as informational.
- **P0 — Chaos (shadow) runs are recorded as real runs and overwrite the spec's DQ score.** `run_rules(shadow=True)` reads shadow tables but the result dict and `write_dq_run` carry no mode flag; `_write_governance_results` then rewrites `spec_registry.dq_score_pct`. After a shadow run, `get_latest_results(spec)` returns it. The consumer's chaos runner does exactly this, which is why 35 result files landed in one hour on April 11 under a real spec name.
- **P0 — Hand-written JSON result files are ingested as authoritative, with P0 defaulting to PASS.** The runner never writes JSON, yet CLAUDE.md and two agent prompts tell agents to check `governance/dq-results/*.json`; `sync.py:138-180` imports whatever is there and a missing `p0_passed` becomes `True` (`:172-175`).
- **P0 — Contract "DQ P0" and "golden dataset" checks are file-existence checks reported as PASS.** `contract.py:617-641`: `dq_p0 PASS "rules file exists"`; the docstring says it runs them; generated contracts default `p0_pass_required: true`.
- **P0 — Golden mismatch still cannot change the headless exit code.** `run.py:138-162` reads zone status and contract violations only; golden 0 of 9 with zone SUCCESS exits 0. README line 181 still claims "blocks completion on mismatch"; the 2026-07-03 audit flagged this and it is unresolved. Worse, a golden value on a non-numeric column raises inside `_verify_golden_datasets`, which is called outside any `try`, so a fully successful run dies with a traceback and no run-history record.
- **P1 — No automatic execution on write; results are not tied to a snapshot.** `validate_after_write` has zero framework call sites; `promote()` drops the snapshot id it returns; results are keyed by spec, and nothing can tell a stale result from a current one.
- **P1 — No drift, baseline, P1, or acknowledgement path.** Nothing compares run N to N−1; `run.py:614-630` counts P0 only, so a P1 red forever is invisible to every gate; `acknowledge_failures` writes `rule_id="*"` and nothing consults it.
- **P2 — Threshold semantics are narrow, chosen by substring, lenient on NULL.** Grammar is `result|result_count <op> <number>`; scalar vs row-count is decided by whether the string contains `result_count`; NULL becomes 0, so `MAX(x)` over an empty table with `result = 0` passes; `between` and `%` are "unparseable" failures with `error=None`; a scalar `COUNT(*)` with `result_count = 0` can never pass, which is the exact rule shape that drove the consumer to write its own runner. The semantics exist only in a code comment.
- **P2 — Three incompatible golden-dataset shapes**, none matching the consumer's; the template documents the harness shape, the engine parses a different one, and the harness is used only by a test with a mocked catalog.
- **P2 — Lifecycle knows three states.** `approved` and `active` are treated identically and nothing ever sets `active`; `deactivated`/`deferred`/`superseded` are skipped but the warning calls them PROPOSED and prints an `approve` command for them.
- **P2 — The chaos monkey is not wired in.** Reconcile needs a `--dq-results` JSON the runner never writes; the reconciler matches `category` while rules carry `dimension`; the safety gate checks `GRIST_ENV=dev` while the agent prompt documents `BRIGHTSMITH_ENV=dev`, so the documented command fails; the step is mandatory in the gate with no output check.
- **Side finding from this audit:** a read-only governance command invoked by a reviewer created a new `governance.agent_activity` table in FutureProof's live, committed catalog (`data/catalog/catalog.db`), with an absolute path baked in. Status and validate commands must not mint tables in the deployment artifact. Reverted by `git checkout`.

| Protection | Exists | Automatic | Used by the consumer |
|---|---|---|---|
| P0 gate on rebuild | only inside `run.py`, per zone | only via `run.py`; zero rules for non-medallion names | no (19 bespoke runners) |
| Snapshot-tied results | no field anywhere | no | no |
| Threshold semantics | two forms, undocumented outside code | n/a | diverged |
| Drift baseline | no | no | no |
| Golden verification | engine + CLI; no exit-code effect | no | files unparseable or crash |
| Contract at write | verify only post-zone; P0/golden are existence checks | only via `run.py` | no |
| Chaos | inject/reconcile CLI; results indistinguishable from real | no | pollutes real results |

---

## 4. Operating model: agents, skills, definition of done — grade D

This is the layer that produced FutureProof's outcome deterministically rather than by accident.

- **P0 — "Done" is a frozen set of per-spec artifacts, and nothing ever re-validates them.** Completion requires artifacts to exist and rules to have executed once ("results file exists"). `pipeline_gate.validate` then hash-locks each step's output and **fails if it was modified after completion** (`pipeline_gate.py:648-660`), actively penalising the regeneration of an artifact after a later spec rebuilds the table. Every skill routes to `dq_runner run --spec`. A search of CLAUDE.md, README, workflows, agents, and skills for refresh, rebuild, or re-run returns nothing.
- **P0 — Drift is designed in at four points.** Per-spec artifacts (a table rebuilt by spec N+1 is still guarded by spec N's file). Snapshot-fitted thresholds (the rule-writer prompt says "EDA shows 0 violations in N rows, so 100% is achievable"; the consumer has 49 rules with headroom rationales and 640 of 901 at P0). Glossary as prose (the validator checks that eleven fields exist, not that the formula matches code). Contracts generated once and regenerated only when a later spec explicitly touches the table.
- **P1 — The rules reward volume and the framework reports volume as success.** Every zone skill ends with a celebration banner of counts ("DQ Rules: [total count across ALL specs]", "Lineage Events: [total count]"); the content-strategist agent sells "governance artifact counts"; the web-designer renders "metric cards with large numbers". The consumer's 116 rules stuck in `proposed`, 283 audit-trail files read by four scripts, and chaos runs filed as real runs are what that incentive produces.
- **P1 — The three-agent DQ split produces files, not better rules.** The analyst profiles once, the rule-writer fits thresholds to that profile, the engineer runs `--spec` once and is told to "compare current run to previous runs" with nothing that triggers a later run. The responsibility for re-execution falls into the gap between three personas.
- **P1 — "No hardcoded entity data" has no home for the constants it forbids.** Source YAML has no vintage, release date, or expected rows; contracts offer `min_row_count` (default 1) and `max_staleness_hours`. So the consumer hardcoded `EXPECTED_ROWS = 3039`, `69_947`, `2040`, `239200`, and Stanford's `unitid` in scripts, exactly as the rule forbids.
- **P2 — There is no refresh, rebuild, or expiry workflow.** The headless runner is the right primitive but is positioned as "Option 2 — no agents" and nothing in the agent workflow routes to it.
- **P3 — Human gates gate ceremony.** Approval documents are written by the doc-generator from the proposing agent's own content; the adversarial-auditor prompt itself notes "the human approved what the AI suggested". The consumer has 51 approval documents and at least 20 COMPLETE specs with review sections still PENDING. The one gate that genuinely needs a human (choosing three to five golden anchor values per table) is machine-checkable once chosen.

**Agent map.** Runtime-read outputs come from 8 or 9 of the 26 agents. Seventeen produce artifacts no runtime or executor reads: governance-reviewer, lineage-tracker (the executor emits lineage; the agent's column JSON is unused), cde-tagger, semantic-modeler, pii-scanner, policy-engineer (write-only by its own description), temporal-modeler, adversarial-auditor, bcbs239-auditor, principal-data-architect, insight-manager, content-strategist, web-designer, and doc-generator's outputs other than contracts. FutureProof's own CLAUDE.md pipeline uses eight `fp-*` agents and none of the DQ trio, chaos-monkey, lineage-tracker, cde-tagger, doc-generator, or the modelers. The implementer agent every skill dispatches to, `primary-agent`, does not exist in the roster and never has.

Compared with how mature platforms achieve protection: tests-on-every-build (the primitive exists in `run.py`; the model routes around it), expectations enforced at write time (partial: grain dedup only), contracts as CI schema checks (primitive exists; the scaffold ships no CI), freshness as a first-class SLA (exists; the consumer's contracts declare it on 2 of 42), lineage emitted by the executor (the one thing done right in intent, and broken in practice per §1 finding 5).

---

## 5. Domain-agnosticism and reuse — agnosticism B, reuse D+

The code is genuinely domain-agnostic: zero higher-education coupling in `src/`, and the SEC-EDGAR ancestry that remains is in examples and one template (`grain.py:10-11` `cik/fy/fp`, `agents/setup.md:22-25` seeding Apple and Microsoft by CIK, `dq_runner.py:246` `r.cik`). What is missing is the abstraction a second consumer would need.

- **P1 — Silver and Gold have no base class; the skeleton is copied 22 times.** `src/brightsmith/silver/` contains only concept normalization (used zero times by the consumer) and `gold/` does not exist. `run.py:387-411` defines the whole contract: import a module, call `func()` with no arguments, tolerate a dict. Measured by AST over FutureProof's 22 transformers (10,530 lines): 3,076 lines (29%) are scaffolding (catalog → load table → read → grain id → get-or-create → promote → log → return) and 892 more are schema declarations. A three-way diff of two Silver and one Gold entry point after masking names shows 31 byte-identical lines between the Silver pair and 24 with the Gold one; the domain content of each is a single row function.
- **P1 — Config has no per-zone warehouse.** `config.py:156` names only the Bronze warehouse, so 22 of 22 transformers build `data/<zone>/iceberg_warehouse` themselves and none imports `brightsmith.config`.
- **P1 — No sanctioned read pattern, so the consumer invented three**, including a DuckDB replacement-scan hack over a `# noqa: F841` local (eight sites). `BaseMCPServer._ensure_query_connection` already builds the all-tables-as-views world a Gold transformer needs, but it is private to MCP.
- **P1 — Runtime lineage for every derived table has no inputs**: no transformer calls `emit_start`, and `promote()` emits with `input_tables=[]` (and, per §1, never actually emits).
- **P2 — Every ingestor re-implements the transport layer.** `BaseIngestor` owns table creation, dedup, provenance columns, append, and lineage; everything before `flatten()` is the consumer's. Across 12 ingestors: all 12 implement a cache fallback and private coercion helpers (40 definitions), 10 call `requests` directly, 6 share an identical download shape, 2 retry, 2 chunk CSVs, 3 check required columns.
- **P2 — The MCP consumer replaced the query layer and the governance block** (about 37% of its 5,715 lines is platform work). The upstreamed fix (M6) landed after the pin; even at HEAD, `query_iceberg(sql)` takes no bind parameters and `attach_governance` re-walks every contract YAML per call (the consumer measured 250 ms).
- **P3 — No second zone consumer exists.** brightforge imports `domain_loader` once for a UI; brightgemma depends on the package but imports nothing; the consumer-journey CI runs `--zone bronze` only. Silver and Gold have never been exercised through a consumer in CI.
- **P3 — `init` scaffolds 37 directories (22 empty) and 10 files**, no manifest, no CLAUDE.md, no ingestor, no Silver or Gold module.

| Zone | Framework provides | Consumer must write | Consumer scaffold, measured |
|---|---|---|---|
| Bronze | `BaseIngestor` (table, dedup, provenance, append, lineage) | HTTP, retry, cache, zip/xlsx, magic bytes, chunking, sentinels, coercion, required columns, schema | 1,669 of 6,283 lines (27%) |
| Silver | `promote`, `compute_grain_id`, `get_catalog`, `read_with_duckdb` | paths, catalog wiring, read loop, id stamping, promote, log, return, schema | 1,781 of 4,935 (36%) |
| Gold | same, no package | same plus hand-rolled DuckDB joins | 2,187 of 5,595 (39%) |
| MCP | `BaseMCPServer` (5 tools, locked connection, pushdown, governance) | schemas, bound-parameter engine, telemetry, cache, fuzzy search, handlers | 2,137 of 5,715 (37%) |

Three abstractions would move about 5,300 consumer lines (23%) into the framework: a `ZoneTransformer` base (removes ~2,800 lines, retires the eight replacement-scan sites, and fixes the per-zone-warehouse, read-pattern, and lineage-input findings together); an `HttpSource`/`FetchHelper` (removes ~1,500 and gives all 12 ingestors the retry and chunking that two have); and a query layer in `BaseMCPServer` the consumer keeps (~1,000).

## 6. Governance state machinery: gate, registry, sync, lineage, CAB, OSI — grade D

This is the part of the framework that decides what "complete", "approved", "verified", and "in lineage" mean. On the only consumer, every state store contradicts the others: spec files say 79 COMPLETE, the registry says 0, the gate says 23 of 24 broken, lineage says only ingests ever happened. That is drift by construction, not neglect.

- **P0 — "COMPLETE" is asserted by nothing that looks at the table.** `validate` (`pipeline_gate.py:600-662`) checks step status, output-file existence, and per-step hashes; the zone-specific checks (`:664-780`) check that files exist; the warehouse check (`:782-851`) checks only that tables have more than zero rows; contract and schema are explicitly skipped (`:728`). Experiment: a Gold spec validated PASS and was registered COMPLETE; its output table was then dropped and recreated with different columns and a third of the rows; `validate` still PASS, registry still COMPLETE. `contract verify` would have said `schema_match FAIL`, and nothing in the gate calls it.
- **P0 — No code path in the framework ever writes a spec status of COMPLETE.** `write_spec_registry` is called from `init` and from every gate event with `status = _last_known_status(...) or "IN_PROGRESS"`; only tests write COMPLETE. The consumer's registry has 846 rows and zero COMPLETE, ever. The carry-forward fixes in the working tree and in `b40d770` correctly stop gate events from clobbering status and `output_tables` back to defaults, but they protect a value with no producer; `output_tables` is, by the gate's own comment, "never written into the state dict".
- **P1 — Shared artifacts are hashed per spec by construction.** `complete_step` hashes whatever `--output` path the step registers; the agent contracts name shared files (`data-steward.md:151` writes the glossary, doc-generator writes the dictionary, domain-context writes its markdown). Reproduced with two specs. On the consumer, `validate --all` gives 23 FAIL / 1 PASS with 59 "modified after completion" issues and zero warehouse issues: the one tamper signal the gate has is 100% noise, and `scripts/rehash_pipeline_state.py` exists to blank it.
- **P1 — The file→Iceberg sync is specified to run on every pipeline run and has no caller.** `governance-database-only.md` Decision 6 says "automatic sync in `brightsmith.run`"; `sync_from_files` is reached only from the CLI and the migration. Runtime writers do write Iceberg directly (gate events, DQ runs), so the tables are half-live, and `governance_db export` would rewrite the consumer's current state files from May-era events.
- **P1 — A namespace rename shipped with no migration.** Product tables moved to `governance_product.*` on 2026-06-30 while lineage stayed under `governance.*`; the consumer's catalog has `governance.spec_registry`. Upgrading starts with empty registry tables, silently. The contract sync understands only `metadata:`/`contract:` shapes, so the consumer's 40 flat v1.4.0 contracts never sync: two rows at v1.0.0 is the correct output of this code.
- **P1 — Lineage emission is swallowed twice and writes to the wrong catalog under test.** `emit_*` catch-and-warn, wrapped again by callers, allowlisted in the no-swallow meta-test as "observability, never a gate", while `lineage verify` is the lineage-tracker's gate step. `_get_lineage_table` uses the cwd-derived `config.CATALOG_PATH` and ignores the `catalog_path` the caller passed to `ingest()`. Reproduced: `ingest(warehouse_path=tmp, catalog_path=tmp)` landed the data in tmp and the lineage in the project catalog. That is exactly how 584 IPEDS pytest-fixture events reached FutureProof's shipped catalog, and it is a framework responsibility. At runtime the grain is job-level only; `inputs` on COMPLETE is always `"[]"`; `_write_event` is a bare append with no dedup; `verify` selects by `job_name LIKE '%spec%'`.
- **P2 — CAB gates nothing automatically.** `cab.review` writes files; `write_cab_decision` is called only by the migration; `detect_schema_modification` has no callers; the step is skippable with any reason, and `validate` fails only on a PENDING entry in an index file. The consumer's CAB files are hand-written in a shape `cab.py` neither produces nor reads.
- **P2 — OSI export is real at HEAD and unreachable for the consumer.** At the pin the module does not exist; at HEAD, `--zones gold` raises "No data contracts" because the consumer's contracts are `consumable.*`, and `--zones consumable` yields two datasets (the v1.0.0 drafts), no relationships, no metrics.
- **P2 — The manifest is never validated against reality.** `load_manifest` checks shape; it never compares sources to the ingestors on disk (12 vs 8), to catalog namespaces (`raw.*` and `bronze.*` side by side), or to zone step targets. Drift is undetectable.
- **P2 — The warehouse-population check is zone-wide, not spec-scoped**, and keys on the literal zone name, so a consumer whose tables live in `consumable.*` fails "Namespace 'gold' not found".
- **P3 — Coverage** at HEAD (fresh run, 889 passed, 54 s): `pipeline_gate.py` 96%, `osi.py` 93%, `parsers.py` 88% (fixed since July), `lineage.py` 77%, `cab.py` 66%, `sync.py` 55%, `model_writers.py` 44%, `exporters.py` 27%, `cli.py` 14%. `test_lineage_verify` and `test_lineage_cli` assert "doesn't crash" and emit into whatever catalog the cwd resolves to, which is the pollution pattern in test form.

| State | Written by | Read by | Validated against reality by | Drifts silently |
|---|---|---|---|---|
| Spec status in the `.md` | humans, agents | humans | nothing | yes |
| pipeline-state hashes | `complete_step` | `validate`, `audit` | file hash only; shared files flip | yes, as noise |
| `spec_registry.status` | gate events, sync | `governance_db status` | nothing; no COMPLETE producer | yes |
| `dq_runs` | `dq_runner`, sync | `status`, exporters | nothing; files diverged in May | yes |
| `contract_metadata` | contract sync (framework-shaped only) | `get_contracts` | nothing | yes |
| `lineage_events` | ingestor, `promote(spec_name)` | `verify`, MCP `get_lineage`, CAB blast radius | nothing; fixtures pollute | yes |
| OSI semantic model | `osi generate` | MCP resource | `osi check`, manual | detectable if run |
| `domain/manifest.yaml` | humans | `run.py`, ingestors, CAB, OSI | shape only | yes |

## 7. Tests, CI gates, and code health — grade B

Fresh run at HEAD plus working tree: 889 passed, 0 failed, 0 skipped, 0 xfail, 210 s, 76.0% coverage; ruff clean; pyright 0 errors. An AST census over all 889 tests finds 9 assertion-free tests (all legitimate must-not-raise cases), 26 weak-only tests, zero skip or xfail markers, zero constant-equals-constant, and no test that mocks its own subject. Eight in-memory sabotage mutations: 6 caught, including every business-logic one (grain delimiter escaping, promote anti-join, both DQ threshold comparisons, zero-row entity handling, relative-path rewrite). This is a genuinely clean suite, and it is the best-engineered part of the framework.

It grades below the repo's own B+ self-assessment because two CI gates announce protection they do not provide, and a doctrine-enforcing meta-test still has the bypass the last audit named.

- **P0 — The warehouse-relocation CI step cannot fail.** `ci.yml:37-38` runs `relocate --check` "to guard committed example warehouses", but `data/` is gitignored and `git ls-files data/` is empty, so on every CI checkout it prints "No Iceberg warehouse found. Nothing to do." and exits 0. Mutation: neutering `Plan.is_foreign_absolute`, which drives the whole relocation detector, leaves all 889 tests green.
- **P0 — Golden-dataset MISMATCH still exits 0.** `run.py:135-162` never reads `self.golden_datasets`; measured "golden 0/10 MATCH → SUCCESS, exit 0". README line 181 says the opposite. Unchanged from the 2026-07-03 audit's first open risk; the one test that reaches this path asserts on printed text only.
- **P0 — The no-swallowed-exceptions guard has four bypass forms, one live.** `_is_broad` matches only a bare `except:` or the literal name `Exception`; `except (FileNotFoundError, Exception)`, `except BaseException`, `except builtins.Exception`, and an alias all pass. Live at `silver/concept_normalization/config.py:24`, which silently degrades the normalizer to discovery mode and sits at 0% coverage.
- **P1 — The in-process pipeline-gate hash tests are tautological**: they compute the recorded hash with the function under test, so a constant `_hash_file` survives; only the subprocess tamper test discriminates.
- **P1 — Governance dark corners moved rather than closed**: `parsers.py` 9% → 88%, but `cli.py` 14% and `exporters.py` 27% are unchanged, and the operator command `governance_db export` is entirely unexercised; `sync.py` 55% and `model_writers.py` 44% are new.
- **P1 — The uncommitted MCP allowlist change is untested and undocumented.** `base_mcp_server.py:655-666` widens DuckDB's `allowed_directories` to every table root; no test references it; its `except OSError` does not catch `NoSuchTableError`, so a catalog row whose table is gone now propagates instead of degrading; the CHANGELOG diff does not mention it.
- **P2 — Nothing enforces version agreement** across pyproject, plugin.json, marketplace.json, and CHANGELOG; the package has no `__version__`; the CHANGELOG links point at tags `v0.4.0` and `v0.5.0` that do not exist.
- **P2 — The consumer-journey smoke test never re-runs Bronze** to assert zero new rows, which is the framework's headline invariant.
- **P3 — Four modules have no production importer** (`integration_test_harness`, `period_disambiguator`, `staging`, `glossary_validator`); `governance/repository.py` has no importer anywhere. The `network` pytest marker is declared and unused. CI has no `permissions`, `timeout-minutes`, or `concurrency`; dependencies have no upper bounds.

| Gate | In CI | Blocking | Passing | Known bypass |
|---|---|---|---|---|
| pytest (889) | yes | yes | yes | at HEAD, `paths-ignore` skips CI for docs, agents, skills (removed in the working tree) |
| coverage floor 75% | only in the uncommitted edit | once committed | 76.0%, 73 statements of headroom | README claims it is gated today |
| ruff | yes | yes | yes | does not flag redundant `(X, Exception)` tuples |
| pyright | yes | yes | yes | none found |
| no-swallow meta-test | yes | yes | yes | four forms, one live |
| no-leak meta-test | yes | yes | yes | single test, three flows |
| consumer journey | yes | yes | assumed | no idempotency re-run; Bronze only |
| relocate `--check` | yes | nominally | always | cannot fail: nothing under `data/` is tracked |
| template drift | yes | yes | yes | none |

---

## 8. What is genuinely good

- The suite: real Iceberg writes in temp catalogs, no mocks of the subject, no skips, mutation-resistant business logic (§7).
- `promote` and `compute_grain_id` for the case they were designed for: initial load and an idempotent re-run (§2).
- The MCP surface: read-only, two-layer injection defence, locked DuckDB connection, and a persistent query connection that stopped re-registering views per call (§5; FutureProof's own audit found no injection path through it).
- `run.py`'s design: typed exit codes, DQ gates between zones, a warehouse-population check. It is the right primitive; the operating model just routes around it (§4).
- The remediation discipline: CHANGELOG entries cite audit finding IDs, fixes carry regression tests, and field bugs from the consumer were upstreamed (relocate, nested manifests, the MCP query connection).
- Domain-agnosticism in code: nothing higher-education-specific leaked in (§5).

---

## 9. Verdict

**Overall: C−. Well built, wrongly shaped.**

| Area | Grade |
|---|---|
| Tests, CI gates, code health (§7) | B |
| Domain-agnosticism (§5) | B |
| Reuse and abstraction (§5) | D+ |
| Storage and ingest core (§2) | D+ |
| DQ and verification (§3) | D |
| Governance state machinery (§6) | D |
| Operating model (§4) | D |

Is Brightsmith a bad framework? No. The engineering craft is unusual: a clean, honest test suite; primitives that do what they say for the case they were designed for; a visible feedback loop from the one consumer. A team that ships `except Exception` bypass meta-tests and a wheel-install smoke job is not a team that writes bad code.

But it does not do what its README says it does, and the gap is not at the edges. Of the nine feature claims on the front page, three hold for a real consumer (domain discovery, the MCP surface, the OSI export once contracts are framework-shaped). "Executes DQ rules, P0 blocks the pipeline" runs after the write, with no rollback, and evaluates zero rules for the consumer's namespaces. "Fails loudly" is contradicted by the three worst findings, all silent: a clone writes into the original's warehouse, a corrected value is reported as a successful skip, and promote lineage has thrown and been swallowed on every call since March. "Tracks everything in a governance database" describes tables that no runtime reads, that nothing syncs, and in which no code path can ever write COMPLETE. "Hardens rules adversarially" describes a chaos monkey whose results are indistinguishable from real runs and whose documented command fails its own safety gate.

The through-line is one habit and one design choice. The habit is catch-everything-and-warn, which turned every observability feed into a dashboard that stays green while empty. The design choice is that governance is a set of per-spec artifacts whose existence is checked once, rather than a set of checks that run on every write. Given that choice, FutureProof's outcome (rules fitted in April, tables rebuilt in August, scorecards still saying 100%) was not an accident. It was the operating model working as designed.

On FutureProof specifically: the framework is responsible for the governance layer being fragile and silent; the project is responsible for stopping feeding it in May and for pinning, on 2026-08-31, a commit that is April-6 code plus a LICENSE. The pin is worse than stale. It is a lineage that cannot execute any of the project's 874 rule references, sitting behind an unmerged branch that contains every fix made since June. FutureProof should not upgrade to 0.5.0 as it stands: the zone-alias migration rewrites text and never renames Iceberg tables, so the upgrade would break every view registration.

---

## 10. Plan

Ordered by damage prevented per hour. Items marked **delete** remove code; that is most of the leverage here.

### Phase 0 — days: stop the silent failures

1. **Relocation guard keys on the baked prefix, not file existence** (`iceberg_setup.py:237-238`, `:89-90`). `_path_under` already exists; about five lines. Closes the clone-writes-into-original P0.
2. **`promote()` gets a conflict bucket.** Hash the non-grain payload alongside the grain; return `{promoted, skipped, conflicts}`; add `on_conflict="skip"|"error"|"replace"` defaulting to `error`. A correction is never again a successful skip.
3. **`dedup_grain` becomes required** at manifest load; `filter_existing_records` rejects a null id field. Silent double-loads end.
4. **Fix promote lineage**: `table.name()` instead of the non-existent `table.identifier`; emit unconditionally; raise when a spec is named; add a round-trip test that reads the event back. Thread `catalog_path` through `emit_*` so tests stop writing into the project catalog.
5. **Golden MISMATCH changes the exit code** in `finalize()`, and `_verify_golden_datasets` runs inside the run's error handling. Then README line 181 is true.
6. **Scorecard and gates read `priority` from the result row**, never from a metadata lookup that can miss. Add a `mode` column (`real` / `shadow` / `imported`) to `dq_runs` and `dq_rule_results`; scorecards, `get_latest_results`, and the registry ignore anything but `real`; imported JSON never sets `p0_passed`.
7. **Read-only commands stop minting tables.** `governance_db status`, `pipeline_gate validate`, and `lineage verify` must not create `governance.agent_activity` or any other table in a consumer's catalog (this audit's read-only pass created one in FutureProof's committed catalog).
8. **Widen the no-swallow meta-test** to tuples, `BaseException`, and dotted names; fix the live instance; convert the catch-and-warn sites that a gate depends on (`promote.py:87`, `run.py:194`, `dq_runner.py:505`) to raise.
9. **Make `relocate --check` provable in CI** with a committed fixture warehouse that has a foreign baked prefix, and a test asserting exit 1.
10. **Merge `audit-remediation` to `main`, tag `v0.5.0` for real, and fix the version-agreement gap** with a `__version__` and a test.

### Phase 1 — weeks: protection runs on the write path

11. **DQ on write, keyed by table and snapshot.** `promote()`, `append_data()`, and `BaseIngestor` call `validate_table(namespace.table, snapshot_id)`: every active rule referencing that table runs; `dq_runs` gains `table`, `snapshot_id`, `mode`; a P0 raises. Write to a shadow table and swap in one catalog commit so a P0 never leaves bad rows visible. Add `dq_runner stale` listing tables whose current snapshot has no real run.
12. **One evaluator contract.** Replace the string grammar with a structured `expect` (`{"of": "scalar"|"rows", "op", "value"|"min"/"max"}`); NULL and unparseable are ERROR, never PASS; `execute_sql_rule` is the single evaluator used by the runner, chaos reconciliation, and contract `dq_p0` (which executes instead of checking that a file exists). Zero matched rules on a populated zone is a failure.
13. **Namespaces come from the catalog.** Drop `_KNOWN_NAMESPACES` as a gate; match rules to tables via the catalog and the contract's declared table. Make `migrate_zone_aliases.py` rename Iceberg tables and rewrite view names, or keep the aliases. Bind `get_catalog` to `config.CATALOG_PATH` and warn on any other path.
14. **`validate` checks the table, not the paperwork.** For each output table: `verify_contract` schema match, grain, row count; a non-empty `diff_contract` fails; regenerate the contract on every promote and fail a schema change that lacks a version bump. Hash only spec-owned outputs; shared artifacts record "contributed", never "owns". **Delete** the output-hash freeze.
15. **One golden-dataset schema**, validated at load with a loud failure on shape or on a non-numeric column, and string equality supported.
16. **A drift baseline.** Store row count, null rate per column, and per-rule `raw_value` on every real run; `run_rules(compare_previous=True)` flags moves beyond a band. Distribution-style rules become P2 monitors; observed-value-plus-headroom is forbidden at P0.

### Phase 2 — a month: the abstractions a second consumer needs

17. **`ZoneTransformer` base**: declares source tables, target, grain, prefix, schema; resolves per-zone warehouses from config (add `SILVER_WAREHOUSE`, `GOLD_WAREHOUSE`); exposes inputs as rows or a DuckDB connection with views (reuse the MCP server's view builder); stamps ids and `promoted_at`; promotes; emits lineage with real inputs; returns the runner's dict. Removes ~2,800 consumer lines and retires the replacement-scan hack.
18. **`HttpSource` / `FetchHelper`**: retry with backoff, cache with offline fallback, magic-byte validation, zip/xlsx, 50k-row chunked CSV, sentinel-coercion family, required columns, vintage stamp, `single_file` mode. Removes ~1,500 consumer lines.
19. **Refresh and rebuild as supported operations.** `source_version` in the grain contract and a replace-by-vintage promote mode; an atomic `rebuild(table, records)`; `brightsmith.infra.maintenance expire --keep N` plus orphan removal, invoked by the runner after each zone; `release`, `expected_rows {min,max}` in source YAML honoured by ingest and contracts.
20. **A query layer the consumer keeps** in `BaseMCPServer`: `query_iceberg(sql, params)` with bind parameters, a per-table cached governance block, `ToolDef.from_contract()`. Then FutureProof can leave its 0.2.0 pin and delete its `_query_engine.py`.
21. **`read_with_duckdb(limit=)` honours the limit** without materializing the table, and the DuckDB round-trip in `:401-406` goes.
22. **Manifest check**: sources vs ingestors on disk vs catalog namespaces vs zone targets, run by `run.py` preflight and in the consumer's CI.

### Phase 3 — the operating model

23. **Protection is defined as "every write runs the table's rules"**, and done is defined as "`brightsmith.run --validate-only` is green on the current warehouse plus golden verify". **Delete** the pipeline-state JSON, the per-spec checklists, the celebration banners, and every count in a skill's output; the only reported number is "last full validation: timestamp, P0 failures: N".
24. **Rules keyed by table, not spec.** A rule in `dq-rules/<namespace.table>.json` runs; a rule not in it is git history. **Delete** the PROPOSED/APPROVED/ACTIVE lifecycle, `--spec`, and chaos output ever touching real results.
25. **Eight agents**: setup, analyst (EDA and domain context), dq-author (rule-writer and chaos merged, each rule proven against an injected corruption), doc-generator (contracts and dictionary from schema, glossary), tagger (CDE and PII), mcp-engineer, document-extractor, reviewer (governance and staff merged; runs the CLIs; picks three to five golden anchors). **Delete** the other eighteen, and the dangling `primary-agent` reference.
26. **One state store.** The warehouse plus governance rows written by the executor. **Delete** the product governance tables, `sync.py`, `exporters.py`, and `migration.py` (about 1,600 lines) until a reader exists; keep `dq_runs` and `dq_rule_results` only with `dq_runner` as their sole writer. **Delete** approval documents, audit-trail markdown, the session-log export, and the triple-write rule. Two human gates remain: golden anchors per table, and a MAJOR contract diff.
27. **An executable glossary**: a derived term carries `formula_sql` or a function reference verified by a test against the table; drop `approval_status`.
28. **Explicit `/bs:refresh <source>` and `/bs:rebuild`** skills that bump the vintage, ingest, run everything, diff contracts, verify golden values, and regenerate the scorecard from the run.

### For FutureProof, specifically

Do not upgrade to 0.5.0 until item 13 lands; the namespace migration would break every view. In the meantime the project has two honest options: stay on the pin and vendor the four primitives it actually uses (`get_catalog`, `promote`, `compute_grain_id`, `read_with_duckdb`), accepting that the governance layer is documentation; or move to the branch once Phase 0 is done and let items 11 and 14 replace its twenty bespoke scripts. Either way, its 17 empty catalog files, the fixture rows in `lineage_events`, and the `rehash_pipeline_state.py` workaround should go.

---

## Appendix — method

Seven reviewers ran in parallel, each owning one area and briefed with the FutureProof findings so they attributed rather than re-derived: attribution at pin and HEAD; storage and ingest core; DQ and verification; governance state machinery; the operating model; tests and gates; reuse and domain-agnosticism. Reviewers were instructed to run scratch experiments outside both repositories (`scratchpad/bs_core`, `bs_dq`, `bs_gov`, `bs_onboard`) and never to modify tracked files. Two side effects occurred and are recorded: one reviewer ran `uv sync --frozen --group dev` into Brightsmith's `.venv` because its pytest shim pointed at a dead interpreter; and a read-only governance CLI invoked against FutureProof created a `governance.agent_activity` table in that project's committed catalog, which was reverted with `git checkout` and is itself reported as finding 7 of Phase 0. The clone-writes-into-original and correction-discarded experiments, the `Table.identifier` claim, and the branch topology were re-verified by hand before appearing here. The previous five audits (`docs/technical-audit-2026-06-10.md` through `2026-07-03.md`) were read for their open-risk lists; the three risks left open on 2026-07-03 were re-checked and two remain open.
