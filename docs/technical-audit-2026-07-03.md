# Brightsmith — Technical Audit & Improvement Plan (2026-07-03)

*Branch `audit-remediation` @ `fea0e06`. All 825 tests pass locally (118s, Python 3.14; CI matrix 3.11/3.12), total coverage 74%. This is the fifth audit of this repo; unlike the prior ones, its first job was verifying whether the 2026-07-02 remediation actually landed — every claim was checked against source, and the coverage run was re-executed rather than trusted.*

---

## Executive Summary

**Overall health: B+** (up from the prior audit's B−). The 2026-07-02 remediation is real, not cosmetic: all five Critical/High fixes were verified in source (C1's `allowed_directories` → `lock_configuration` ordering at `base_mcp_server.py:565-596`, H2's `ZoneNotRegisteredError` at `run.py:48`, H5's `tables:` and nested-manifest support, H4's packaged templates with a byte-drift guard), and the claimed coverage jump is genuine — `pipeline_gate.py` is now at 97% and `run.py` at 99%, confirmed by a fresh coverage run. The consumer-journey CI job closes the structural gap that produced the last audit's worst findings.

**Top 3 remaining risks:**

1. Golden-dataset verification runs in the headless pipeline but its result never affects exit code — a MISMATCH exits 0, directly contradicting the README.
2. The no-swallowed-exceptions CI guard has a bypass (tuple-form handlers), and one live instance already exploits it.
3. The governance-DB package is the new coverage dark corner (`parsers.py` 9%, `cli.py` 14%, `exporters.py` 27%) — the same inverse-criticality pattern the last audit fixed elsewhere.

**Top 3 opportunities:**

1. Finishing the "loud failure" last mile is cheap (all three fixes above are small).
2. A governance-package characterization pass repeats a play that already worked (the 0.4.0 characterization pass surfaced two production bugs).
3. One deliberate decision about the shared SQLite catalog would clean up the MCP trust boundary.

No Critical findings. No secrets, no injection holes on the untrusted surface, dependencies pinned and current.

---

## Phase 1 — Repo Map

**Purpose.** Domain-agnostic AI-agent data pipeline framework: raw data → Bronze → Silver → Gold → MCP zones on Apache Iceberg (SQLite catalog, DuckDB reads, PyIceberg writes), producing governance artifacts (DQ rules, contracts, lineage, approval gates) at every step. Ships two ways: a pip-installable package (`src/brightsmith/`, ~16.5k lines) and a Claude Code plugin (`agents/` — 25 personas, `skills/` — 9 commands, `hooks/` — 2 hooks).

**Stack.** Python 3.11+, DuckDB 1.5.0, PyIceberg, PyArrow, MCP SDK, uv + hatchling. `uv.lock` present; ruff (E/F/B/I/UP/SIM) and pyright both blocking in CI.

**Architecture.** Domain packs supply `domain/manifest.yaml` + a `BaseIngestor` subclass (`bronze/base_ingestor.py` — fetch/flatten/dedup/append). Cross-cutting `infra/` carries the weight: `pipeline_gate.py` (1,204 lines, per-spec state machine), `dq_runner.py` (SQL rules → thresholds → P0 gates, with a PROPOSED→APPROVED→ACTIVE lifecycle gate), `promote.py`+`grain.py` (idempotent appends via SHA-256 grain IDs with delimiter escaping), `contract.py`, `lineage.py`, `relocate.py` (repairs baked absolute Iceberg paths), and `infra/governance/` (Iceberg-backed governance DB, factored into schemas/writers/queries/sync/migration). `run.py` is the headless orchestrator with typed exit codes (0/1/2/3/4); `serve.py` + `mcp/base_mcp_server.py` are the MCP deliverable with a two-layer read-only SQL defense.

**Key directories.**

| Path | What it is |
|---|---|
| `src/brightsmith/infra/` | ~70% of the code; governance + Iceberg infrastructure |
| `src/brightsmith/infra/governance/` | Iceberg governance DB package (façade at `governance_db.py`) |
| `src/brightsmith/{bronze,silver,mcp}/` | Zone base classes |
| `src/brightsmith/_templates/` | Wheel-packaged DQ templates (drift-guarded against `governance/` copy) |
| `agents/`, `skills/`, `hooks/` | Claude Code plugin surface |
| `tests/` | 825 tests incl. meta-tests (no-swallowed-exceptions, no-leaked-connections), consumer fixtures |
| `scripts/consumer_journey_smoke.sh` | Wheel-install → scaffold → ingest → MCP-query smoke test, blocking in CI |
| `docs/specs/` (25), `docs/workflows/` | Spec-driven development record; 4 prior audit documents |

**Surprises.** (1) The remediation discipline is exceptional — CHANGELOG entries cite audit finding IDs, fixes carry regression tests, and the fixes spot-checked all matched their descriptions. (2) The repo audits itself in a genuine feedback loop: field bugs from the real consumer (futureproof-data) were upstreamed, including its query-engine workaround becoming the framework's M6 fix. (3) The remaining defects cluster where the last audit's definition-of-done *didn't* reach: golden-dataset gating, the meta-test's own blind spot, and the governance package's CLI/parser layers.

---

## Phase 2 — Audit Report

### Verification of prior remediation (context for what follows)

Checked in source, all confirmed present and correct: C1 fix ordering (`base_mcp_server.py:565-596`), H1 `mcp:` manifest parsing (`domain_loader.py` + `serve.py:143-185` with logged fallbacks), H2 `ZoneNotRegisteredError` (`run.py:48-59,529-537`), H4a-c packaged templates with loud `FileNotFoundError` (`setup.py:119-156`) + drift test, H5.1 `tables:` support (`domain_loader.py:_resolve_table_names`), H5.2 nested manifest shape with `PipelineManifestShapeError` (`run.py:268-364`), M2 `rule_filter` pre-execution (`dq_runner.py:397-448`), M3 lifecycle gate (`dq_runner.py:88-131`), M6 persistent MCP connection with cheap staleness check. Coverage claims re-verified: `pipeline_gate.py` 97%, `run.py` 99%, 825 tests green.

### Critical

None found.

### High

**H-1. Golden-dataset failures do not gate the headless pipeline — a MISMATCH exits 0.** *(fact)*

`run_pipeline` computes golden results (`run.py:581`) but `finalize()` (`run.py:135-162`) sets status/exit-code from zone failures and contract violations only — `result.golden_datasets` is never consulted. A run whose gold numbers have drifted from every known-correct reference value reports `SUCCESS`, exit 0, to the scheduler. This directly contradicts README.md:170 ("verification runs in the headless pipeline and **blocks completion on mismatch**") and undercuts the feature's whole point: golden datasets exist to catch exactly the silent numeric drift nothing else catches. The standalone `verification.py` CLI does gate — but `python -m brightsmith.run`, the documented cron/Airflow entry point, does not. The 99%-coverage characterization pass over `run.py` covered these lines without asserting the gating semantics — a small residue of the test-theater pattern the project rejects.

### Medium

**M-1. The no-swallowed-exceptions CI guard has a bypass, and one live instance already exploits it.** *(fact)*

`_is_broad` (`tests/infra/test_no_swallowed_exceptions.py:152-156`) matches only `except:` and `except Exception` as a bare `ast.Name`. A tuple handler — `except (SomeType, Exception)` — or `except BaseException` evades the guard entirely. Live instance: `silver/concept_normalization/config.py:24` reads `except (FileNotFoundError, Exception)` (the `FileNotFoundError` element is dead — `Exception` subsumes it), swallowing *any* error — including a malformed manifest — into "discovery mode" at INFO level. That is precisely the silent-degrade the doctrine forbids, in a file with 0% test coverage. The guard's value is that a new swallow "fails CI automatically" (README:245); this hole means it doesn't, and the one existing instance suggests the pattern will recur.

**M-2. `verify_golden_dataset` is the full-table-materialization hot path the M1 remediation missed.** *(fact)*

`golden_dataset.py:113` calls `read_with_duckdb(iceberg_table)` with no columns/limit — the whole table into a Python list — then filters per-value in Python (`:124-126`) and takes `matching[0]` (first arbitrary row) as the actual. Every other hot path (MCP queries, dedup, contract verification) was converted to pushdown/pruned reads in round 2; this one still scales with table size and its "first matching row" semantics are fragile when filters under-specify the grain. Module coverage is 58%.

**M-3. The shared SQLite catalog leaks governance tables into the MCP surface as listed-but-unqueryable phantoms, and governance writes invalidate the MCP view cache.** *(fact for mechanism; judgment on impact)*

Both warehouses share one catalog file and catalog name (`config.py:157-158`, `get_catalog`). `BaseMCPServer._ensure_query_connection` registers a view for **every** catalog row (`base_mcp_server.py:542,572-587`) but scopes `allowed_directories` to the data warehouse only (`:565-566`). Since `iceberg_scan` views are lazy, governance-table views register fine and then fail at query time with a DuckDB permission error — so `list_tables` (`:401-414`) advertises governance tables an MCP client can never query, with a confusing error rather than a clear "not exposed". Additionally, the staleness check compares **all** table locations (`:542-543`), so any governance-DB append (which happens on every pipeline event) tears down and rebuilds the entire MCP query connection — the exact per-call rebuild cost M6 was designed to remove, reintroduced whenever a pipeline runs alongside a live server. Security holds (deny by default); coherence and performance don't.

**M-4. `run_rules` silently swallows view-registration failures — including `WarehouseRelocationError` — with an unlogged `pass`.** *(fact)*

`dq_runner.py:466-469`: `except RuntimeError: pass` (no log line). `_register_iceberg_views` wraps every load failure — including the relocation guard's error, since `WarehouseRelocationError(RuntimeError)` — into `RuntimeError` (`:334-335`). Net effect: DQ rules against a moved warehouse fail with generic "table does not exist" SQL errors instead of the loud, repair-command-bearing relocation message the framework built an entire module to produce. The P0 gate still fails (errored = failed, decision D3), so correctness holds — but the diagnosis is destroyed, and this is a literally silent `pass` in the framework's flagship loud-failure module. It evades the meta-test because it narrows to `RuntimeError`.

**M-5. Test coverage inside `infra/governance/` is inversely distributed to risk — the last audit's H3 pattern, relocated.** *(fact + judgment)*

Fresh coverage run: `governance/parsers.py` 9%, `governance/cli.py` 14%, `governance/exporters.py` 27%, `governance/model_writers.py` 44%, `governance/sync.py` 51%, plus `chaos_monkey/__main__.py` 0% and `contract.py` still at 59% (the untested lines are mostly the diff/deprecate/CLI lifecycle, `contract.py:647-933`). The parsers feed the sync path that populates the governance DB the whole product reports from; a parse bug silently mis-records governance state. Judgment: this is exactly the shape of the pipeline_gate finding that round 2 fixed at 97% — the play is proven, it just wasn't run here.

### Low

**L-1.** `_rewrite_sql` (`dq_runner.py:238-244`) rewrites `namespace.table` via naive `str.replace`, so a match inside a string literal (`WHERE note = 'see bronze.foo'`) is also rewritten. Trusted-SQL surface, so a wart not a hole.

**L-2.** `load_manifest` resolves `project_root` differently for explicit vs default paths (`domain_loader.py:260`: `path.parent.parent if manifest_path is None else path.parent`) — the same manifest file resolves its `source_config:` relative paths against two different roots depending on how it was loaded. Latent trap for any tool that passes the path explicitly.

**L-3.** `infra/governance/repository.py` is dead code — a 3-import re-export shim with zero importers anywhere in `src/` or `tests/` (verified by grep) and 0% coverage.

**L-4.** CLAUDE.md:42 is stale: "Agent definitions: `.claude/agents/`" — they were consolidated to `agents/` in commit `7de76bc`; `.claude/` contains only `settings.local.json`. The README was fixed (L2, round 1); the instruction file agents actually read was not.

**L-5.** An exception during golden-dataset verification propagates out of `run_pipeline` as a raw traceback (`run.py:581`, uncaught in `main()`), bypassing the typed exit-code contract (would exit 1 via interpreter default, indistinguishable from `EXIT_DQ_FAILURE`).

**L-6.** `filter_existing_records` (`iceberg_setup.py:437`) materializes every existing grain ID into memory per promote, and `_build_existing_grains` (`base_ingestor.py:121`) does the same for ingest. Column-pruned, so acceptable at the framework's stated scale — but it's the remaining unbounded-growth read pattern after M1.

### Strengths (what to preserve)

- **The consumer seam is now genuinely tested**: the consumer-journey CI job (`ci.yml:51-73`, `scripts/consumer_journey_smoke.sh`) installs the wheel outside the checkout, scaffolds, ingests, and queries through both MCP query paths — blocking, no `continue-on-error`.
- **Two meta-tests enforce policy as code** (no-swallowed-exceptions across the whole tree with a justified allowlist; zero leaked DB connections), plus a byte-level drift guard for packaged templates.
- **Idempotency is designed-in**: grain hashing with delimiter escaping and a loud missing-key error (`grain.py:41-52`), in-batch dedup before anti-join (`iceberg_setup.py:424-435`).
- **The untrusted MCP SQL surface is properly layered**: keyword allowlist → `allowed_directories` scoping → external-access off → `lock_configuration` (`base_mcp_server.py:63-126, 541-601`), values always bind-parameterized, identifiers regex-validated.
- **Failure-mode engineering is a habit**: relocation guard with the exact repair command, typed exit codes, `PipelineManifestShapeError`, `ZoneNotRegisteredError`.
- **Hygiene**: no secrets (grep-verified), current pinned deps via `uv.lock`, `dist/`/caches/`.coverage` all gitignored, CHANGELOG that a release engineer could actually use, plugin version in sync with `pyproject.toml` (0.4.0).

---

## Phase 3 — Improvement Strategy

**Theme 1 — Finish the "loud failure" last mile.** The doctrine is right and mostly enforced; the residue is three spots where a failure signal is computed and then dropped: golden datasets (H-1), the meta-test's tuple blind spot (M-1), and the unlogged `RuntimeError` pass (M-4). *Target state:* every gate the docs advertise is wired to an exit code, and the meta-test flags any handler capable of catching `Exception` however it's spelled. *Principle:* a gate that can't fail is worse than no gate (the repo's own decision D3).

**Theme 2 — Repeat the coverage play on the governance package.** Round 2 proved that characterization tests over a dark module are cheap and occasionally surface real bugs (two production bugs fell out of the 0.4.0 characterization pass). `governance/parsers|sync|exporters|cli` and `contract.py`'s lifecycle half are the current dark corners. *Target:* ≥80% on parsers/sync/exporters, with at least the parse→sync→query round-trip asserted on real Iceberg.

**Theme 3 — Make the shared-catalog boundary a decision, not an accident.** M-3's phantom tables and cache invalidation both stem from never deciding whether governance tables are part of the MCP surface. *Target:* an explicit namespace scope for the MCP server (data zones only, or governance included with `allowed_directories` extended) applied consistently to view registration, `list_tables`, and the staleness check.

**Theme 4 — Close out the read-path conversion.** One hot path (`golden_dataset`) and two bounded-but-growing paths (`filter_existing_records`, `_build_existing_grains`) remain full-materialization. Convert the hot path now; leave the bounded ones documented.

**Explicitly not fixing (trade-offs):** the config-module shim removal (correctly deferred to a major release — it's now consistent and tested); MCP row-level security (deferred until non-localhost, right call for maturity); automated snapshot compaction (documented manual procedure is proportionate); `chaos_monkey/__main__.py` coverage (thin CLI wrapper over 95-100%-covered logic); L-1's string-literal rewrite (trusted surface, rare, a real SQL parser is not worth the dependency).

**Definition of done:** headless run exits non-zero on golden MISMATCH with a regression test; meta-test rejects tuple/`BaseException` forms and the tree is clean under the stricter scan; governance parsers/sync/exporters ≥80%; `list_tables` returns only queryable tables (or governance exposure is documented as intentional); zero High findings on the next audit.

---

## Phase 4 — Task Plan

### Milestone 0 — Safety net

The safety net largely exists (CI gates, consumer-journey job, meta-tests). One addition:

**T0 — Pin the current golden-dataset behavior with a failing-first regression test.** Test that a `run_pipeline()` whose golden dataset MISMATCHes currently exits 0, then flip the assertion with T1. Files: `tests/infra/test_run_coverage.py`. *Accept:* test exists and fails against current `finalize()`. **Effort S · Risk none · Deps none.**

### Milestone 1 — Correctness fixes

**T1 — Gate golden-dataset results in the headless pipeline.** Make `finalize()` consult `golden_datasets`; map failure to a distinct status and exit code; catch verification-infrastructure errors in `run_pipeline` and map to `EXIT_TRANSFORM_ERROR` (fixes L-5). Files: `run.py:135-162,580-583`, README (or, if gating is deliberately deferred, fix README:170 instead — but the code fix is the right call given the feature's purpose). *Accept:* MISMATCH → non-zero exit, test from T0 green. **Effort M · Risk low-medium** (could newly fail runs that previously "passed" — that's the point, but flag it as BREAKING in the CHANGELOG like M3 was) · **Deps T0.**

*Sketch:* add `GOLDEN_FAILURE` status; in `finalize()`, after the zone-failure branch, check `golden_datasets.failed > 0` → `exit_code = EXIT_DQ_FAILURE` (reuse code 1; a new code 5 would break the documented scheduler contract). Decide MISSING-status semantics: MISSING should fail (consistent with "a dataset that yields no checkable values is a FAIL", CHANGELOG 0.4.0). Wrap `_verify_golden_datasets()` in try/except → `TRANSFORM_ERROR`. Update `_print_summary` to show the gate result.

**T2 — Close the meta-test bypass and fix the live instance.** Extend `_is_broad` to match `ast.Tuple` elements and `BaseException`; change `concept_normalization/config.py:24` to `except FileNotFoundError` (manifest absent → discovery mode) and let parse errors propagate; run the stricter scan and triage any other newly-flagged handlers. Files: `tests/infra/test_no_swallowed_exceptions.py:152-156`, `silver/concept_normalization/config.py`. *Accept:* a tuple-swallow fixture fails the guard; tree is clean. **Effort S · Risk low · Deps none.**

*Sketch:* `_is_broad` returns True if any of {handler.type is None, Name in ("Exception","BaseException"), Tuple containing either}. Gotcha: the allowlist keys on `(file, function)` so existing entries survive; also fix the subtle `break` in `_handler_reraises` (`:140-149`) that stops the walk at the first nested def and can miss a later `raise` — a false-positive source under the stricter scan.

**T3 — Preserve the relocation diagnosis in `run_rules`.** Re-raise `WarehouseRelocationError` from the registration loop; log the skip for other errors. Files: `dq_runner.py:334-335,466-469` (have `_register_iceberg_views` chain the original as `__cause__` — it already does — and check `isinstance(e.__cause__, WarehouseRelocationError)`, or better, let that error propagate un-wrapped). *Accept:* DQ run against a relocated warehouse surfaces the repair command; other load failures produce a `logger.warning`. **Effort S · Risk low · Deps none.**

### Milestone 2 — High-leverage

**T4 — Scope the MCP server's catalog view to data namespaces (decision + implementation).** Filter `list_catalog_table_locations` results to zone namespaces (bronze/silver/gold/mcp + aliases) in `_ensure_query_connection` and the staleness snapshot; `list_tables` then matches reality, and governance appends stop invalidating the data-view cache. If governance exposure is wanted instead, add `GOVERNANCE_WAREHOUSE` to `allowed_directories` deliberately. Files: `base_mcp_server.py:541-601,401-414`, tests in `tests/mcp/`. *Accept:* `list_tables` lists only queryable tables; a governance write does not trigger a connection rebuild (assert via the registry object identity). **Effort M · Risk medium** (touches the security-ordering code — keep the C1 regression tests as the tripwire) · **Deps none.**

*Sketch:* add a `namespaces: set[str] | None` filter param to `list_catalog_table_locations` or filter at the call site with `ZONE_ALIASES`-aware canonicalization; apply the same filter to both the snapshot comparison and registration loop so they can't drift apart.

**T5 — Characterization tests for the governance package's dark half.** parsers → sync → queries round-trip on real Iceberg fixtures; exporters against a populated governance DB; CLI handlers in-process. Files: new tests under `tests/infra/`; no production changes expected (but budget for the 1-2 bugs this pass historically finds). *Accept:* parsers/sync/exporters ≥80%. **Effort L · Risk none · Deps none.**

**T6 — Pushdown reads for golden-dataset verification.** Replace the full-table read + Python filter with per-value filtered, column-pruned queries (the `query_iceberg_simple` pattern already exists to copy); make multi-row filter matches an explicit FAIL or aggregate rather than `matching[0]`. Files: `golden_dataset.py:110-165`, tests. *Accept:* verification reads only filtered columns/rows; ambiguous-match behavior asserted. **Effort M · Risk low-medium** (semantics change for under-specified filters — surface it in CHANGELOG) · **Deps none.**

### Milestone 3 — Quality & polish

**T7 — Unify `load_manifest` path resolution** (`domain_loader.py:260`): always resolve `project_root` as `path.parent.parent` (manifest lives at `domain/manifest.yaml` by convention), or take an explicit `project_root` param. **S · low risk.**

**T8 — Repo hygiene batch:** delete `governance/repository.py`; fix CLAUDE.md:42 (`.claude/agents/` → `agents/`); note both in CHANGELOG. **S · none.**

**T9 — (Optional, scale-gated)** convert `filter_existing_records`/`_build_existing_grains` to a DuckDB `iceberg_scan` anti-join instead of materialized ID sets. Do this only when a consumer's table sizes demand it. **M · medium risk.**

### Task table

| ID | Title | Milestone | Effort | Risk | Deps |
|---|---|---|---|---|---|
| T0 | Pin golden-dataset exit-0 behavior (failing-first test) | M0 | S | none | — |
| T1 | Gate golden datasets in `finalize()` + typed error mapping | M1 | M | low-med | T0 |
| T2 | Close meta-test tuple/`BaseException` bypass + fix live instance | M1 | S | low | — |
| T3 | Preserve relocation diagnosis in `run_rules` view registration | M1 | S | low | — |
| T4 | Scope MCP catalog view to data namespaces | M2 | M | medium | — |
| T5 | Governance-package characterization tests (parsers/sync/exporters/cli) | M2 | L | none | — |
| T6 | Pushdown reads for golden-dataset verification | M2 | M | low-med | — |
| T7 | Unify `load_manifest` project-root resolution | M3 | S | low | — |
| T8 | Delete dead `repository.py`; fix stale CLAUDE.md path | M3 | S | none | — |
| T9 | (Optional) anti-join dedup reads at scale | M3 | M | medium | — |

### Quick wins (do immediately)

**T2** (meta-test bypass, S), **T3** (relocation logging, S), **T8** (dead code + stale doc, S), **T0** (pin golden behavior, S). Combined: under half a day, and T2/T3 close two doctrine violations.

---

## Open Questions

1. **Golden-dataset gating semantics (T1):** should any MISMATCH block, or a pass-rate threshold like the CLI's 80% (`golden_dataset.py:244`)? And should MISSING (table/row not found) block? Yes to both is the recommended answer, but it changes scheduler behavior — decide before T1 ships.
2. **Is the governance DB meant to be MCP-queryable** (affects T4's direction)? Today it's accidentally half-exposed: listed, never queryable.
3. **Rule lifecycle terminal state:** `approve_rules` advances PROPOSED→APPROVED but no code path to ACTIVE was found — is APPROVED→ACTIVE intentionally a manual file edit, or a missing CLI verb?
4. **Release plan:** the Unreleased section carries a BREAKING change (M3 rule-status default). Is the next tag 0.5.0, and should the README's "unreleased 0.5" note become a migration section at release time?
