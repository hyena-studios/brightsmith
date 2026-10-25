# Brightsmith — Technical Audit & Improvement Plan

**Date:** 2026-06-10
**Auditor:** Claude (principal-level repo audit, analysis only — no code modified)

**Scope note:** ~80% of source read by volume (all of `dq_runner`, `pipeline_gate`, `promote`, `grain`, `iceberg_setup`, `run`, `config`, `staging`, `base_ingestor`, exporters, contract I/O, MCP query paths; structural passes over `product.py`, `cab.py`, `lineage.py`). Lighter review: `period_disambiguator`, `concept_normalization`, glossary modules, chaos-monkey corruptors, `setup.py` scaffolding, and the 25 agent prompt files. Two critical findings were **empirically reproduced**, not inferred. Test suite was run (487 passed, 7.4s).

---

## Executive Summary

**Overall health: C-.** The architecture vocabulary is genuinely good — deterministic grain hashing, idempotent promotes, a real chaos-testing safety gate, 487 fast tests — but the two features the README sells hardest are currently broken or fake.

**Top 3 risks:**

1. **(Critical, reproduced)** The pipeline gate state machine no longer persists state — `init` writes no file, `complete` prints success and loses the record, and the next `check` reports BLOCKED. The entire CLAUDE.md-mandated governance enforcement loop has been broken since commit `c1f41f5` (2026-04-28).
2. **(Critical)** The headless runner's "DQ P0 gate" never executes a single rule — it counts every rule as passed (`run.py:347-348`, comment: *"Simplified: count rules as passed"*), directly violating the project's own "no placeholder DQ" rule.
3. **(High)** The "Iceberg-authoritative" refactor was left half-finished — writes go to Iceberg, reads come from files, and the exporter that should bridge them is manual-only and produces an incompatible shape; contracts have the same split-brain (`generate` → Iceberg, `verify` → files → "No contracts found").

**Top 3 opportunities:**

1. All three risks share one root cause (an unfinished migration), so one focused effort fixes the product's core promise.
2. A CI pipeline (currently absent — no `.github/`, 55 ruff errors sitting in a "passing" repo) would have caught every regression here.
3. The governance layer's pervasive `except Exception → pretend success` pattern (~80 sites) is mechanical to fix and would convert silent governance failures into loud ones — the single highest-leverage cultural fix for a governance product.

---

## Repo Map

**Purpose:** Domain-agnostic AI-agent data pipeline framework. Ingests raw data (Bronze) → normalizes (Silver) → builds data products (Gold) → serves them to LLMs via MCP, producing governance artifacts (DQ rules, contracts, lineage, approvals) at every step. Distributed as both a pip package and a Claude Code plugin; orchestration is done by 25 agent personas defined in markdown, with a Python "pipeline gate" enforcing execution order.

**Maturity:** Solo-developer OSS framework, 47 commits, v0.2.0, extracted from a production SEC EDGAR pipeline. Pre-production: no CI, no releases beyond a local wheel, one consumer project.

**Stack:** Python 3.11+ (runs on 3.14 locally), DuckDB + Iceberg extension, PyIceberg with SQLite catalog, PyArrow, MCP SDK, uv/hatchling, pytest, ruff (configured, not enforced).

**Architecture:** Two parallel execution models share one storage layer — (a) AI-agent-driven via Claude Code skills/hooks calling CLI modules (`pipeline_gate`, `dq_runner`, `contract`), and (b) headless via `brightsmith.run`. Domain projects supply a "domain pack" (`domain/manifest.yaml`, `BaseIngestor` subclass); the framework supplies everything else. Governance state was recently migrated from JSON/YAML files to Iceberg tables ("Iceberg-authoritative"), with file exporters for human review — this migration is the epicenter of the critical findings.

| Path | What it is |
|---|---|
| `src/brightsmith/infra/` | Cross-cutting engines: gate, DQ, lineage, contracts, promote/grain, chaos monkey |
| `src/brightsmith/infra/governance/` | Iceberg governance DB: `product.py` (2,637 lines — schemas, R/W API, sync, migration, Mermaid parser, CLI), `exporters.py`, `enterprise.py` |
| `src/brightsmith/{bronze,silver,mcp}/` | Zone base classes (`BaseIngestor`, concept normalization, `BaseMCPServer`) |
| `src/brightsmith/run.py` / `serve.py` / `setup.py` | Headless runner, MCP server entry, project scaffolder |
| `skills/`, `agents/`, `.claude/agents/`, `hooks/` | Claude Code plugin surface (9 skills, 25 agents, subagent-type-enforcing hook) |
| `docs/specs/` (24), `docs/workflows/` (8) | Spec-driven development corpus |
| `governance/` | Artifact directories (several mandated ones don't exist yet, e.g. `data-contracts/`) |
| `tests/` | 487 tests organized by zone; biggest suite is `test_governance_db.py` (1,289 lines) |

**Surprises:** (1) `governance_db.py` is now a shim re-exporting a `governance/` package the docs never mention. (2) `docs/sessions/` — mandated by both README and CLAUDE.md — was deleted in the last commit. (3) The largest file in the repo is undocumented in CLAUDE.md's otherwise meticulous Key Paths section.

---

## Audit Report

*Findings are facts with file:line evidence unless marked **[judgment]**.*

### Architecture & Design

**A1 — CRITICAL: Pipeline gate state machine doesn't persist; the governance enforcement loop is broken.**
`PipelineGate._save()` is a no-op — *"Retain state in memory; file generation is handled by exporters"* (`pipeline_gate.py:281-283`) — but `_load()` reads the JSON state file (`pipeline_gate.py:275-279`). Every CLAUDE.md-mandated CLI call (`check` → `complete` → `check`) is a separate process. **Reproduced in a sandbox:** `init` creates no state file; `complete governance-reviewer-pre` prints "Recorded completion: → COMPLETED"; the immediately following `check primary-agent` exits `BLOCKED: prerequisites not met: governance-reviewer-pre`. The intended bridge fails twice over: nothing invokes the exporters automatically (only the manual `governance_db export` CLI reaches them — verified by grep), and `export_pipeline_state_to_files()` writes `{**spec_registry_row, "events": [...]}` (`exporters.py:121-123`) — a payload with **no `steps` dict** and `spec_name` instead of `spec`, which `check_prerequisites`, `validate`, `check_zone_transition` (`data["spec"]`, `pipeline_gate.py:776`), and `audit_report` (`:873`) cannot consume. The one committed state file (`governance/pipeline-state/brightgemma-deepagents-runtime-pipeline.json`) is in the legacy shape, written before `_save()` was neutered in `c1f41f5`. Consequence: every gate rule in CLAUDE.md ("Before any agent runs… if BLOCKED, stop") is unenforceable, and it fails *silently* — `complete` claims success.

**A2 — CRITICAL (same root cause): Contract lifecycle is split-brained.**
`save_contract` writes to Iceberg only — the YAML write happens only when an explicit `contracts_dir` *outside* the project is passed (`contract.py:130-153`) — while `load_contract`, `list_contracts`, and `verify_contract` read YAML files (`contract.py:113-128, 156-182`). `governance/data-contracts/` doesn't exist in the repo. So the documented flow `contract generate` → `contract verify` yields "No contracts found." This also poisons consumers: `run.py`'s contract gates, `check_headless_ready` ("No data contracts found"), and `BaseMCPServer.attach_governance` (`base_mcp_server.py:449-457`) all read the file side.

**A3 — HIGH: `config.py` mutable-global pattern is unsound.**
`configure()` rebinds module globals (`config.py:82-121`), but six modules bind values at import time — `dq_runner.py:28`, `lineage.py:38`, `dq_scorecard.py:13`, `iceberg_setup.py:21`, `domain_loader.py:28`, `glossary_loader.py:23` — so `configure()` silently has no effect on the DQ engine, lineage, or the catalog name. The rest of the codebase works around this with ~40 scattered function-local `from brightsmith.config import …` statements, an unwritten convention that's one refactor away from breaking again.

**A4 — MEDIUM: `product.py` is a 2,637-line god module** with five responsibilities (20 table schemas, write API, query API, file-sync/migration including a ~387-line `sync_from_files` at `:1700-2086` and ~258-line `migrate_files_to_iceberg` at `:2209-2466`, a Mermaid ER parser, and a CLI). **[judgment]** Any schema change forces edits in the most dangerous file in the repo; the shim `governance_db.py` wildcard-re-exporting it (including private names, `governance_db.py:9-18`) makes the real dependency graph invisible.

**A5 — MEDIUM: Dual zone-naming systems permeate the code.** `bronze/silver/gold/mcp` vs `raw/base/consumable/ai_ready` coexist via `ZONE_ALIASES` (`dq_runner.py:136`, `run.py:45` vs `run.py:173`), and `run.py`'s own docstring examples (`--zone raw`, `run.py:10-11`) are rejected by its own argparse (`run.py:520`). The medallion-rename spec exists but the migration is half-applied.

### Code Quality & Error Handling

**Q1 — CRITICAL: The headless runner's DQ gate is a placeholder that always passes.**
`_run_dq_for_zone` (`run.py:328-352`) loads rules, then: `# Simplified: count rules as passed (actual execution needs Iceberg)` / `passed += 1`. `failed` is always 0, `p0_failures` always empty, and the `except Exception` path returns `(True, 0, 0, [])`. The "post-write DQ gate" at `run.py:293-304` can therefore never fail. This is precisely the "placeholder DQ" and "test theater" the project's own rules reject — and a working real implementation (`dq_runner.run_rules`) sits one import away. `_check_contracts_for_zone` has the same shape: `except Exception: return True` (`run.py:375-376`).

**Q2 — HIGH: Errored P0 rules pass the post-write gate.** `validate_after_write` (`dq_runner.py:296-309`) filters out errored rules ("Only real failures, not errors from missing tables") before deciding whether to raise — so a P0 rule that *errors* (table missing, SQL broken) does not block, the exact scenario the warehouse-population rules in CLAUDE.md exist to catch. The run summary says `p0_passed: false` while the gate waves it through — two sources of truth disagree.

**Q3 — HIGH: ~80 `except Exception` handlers, concentrated where failure must be loud.** Representative: `_query_table` returns `[]` on any failure (`product.py:1210-1212`) — every governance read failure is indistinguishable from "no data," so `get_latest_results` → governance-reviewer's "rules have been executed" check can pass vacuously; `BaseIngestor._build_existing_grains` returns `set()` on read failure (`base_ingestor.py:124-125`), **silently disabling dedup** and ingesting duplicates; `run.py:351-352, 375-376, 394-395, 420-421` convert failures into successes. **[judgment]** For a governance product, "swallow and report success" is the worst available default.

**Q4 — MEDIUM: Idempotency hole — in-batch duplicates are not deduplicated.** `filter_existing_records` anti-joins incoming records against the *table* only (`iceberg_setup.py:106-118`); two records with the same `record_id` in one batch both survive the left-join and both get appended. `BaseIngestor` has the same gap (`base_ingestor.py:186-190` checks `existing_grains` only). "Re-running with the same data produces 0 new rows" holds, but "same grain twice in one run" violates CONS-GRAIN-UNIQUE at write time.

**Q5 — MEDIUM: `compute_grain_id` silently degrades.** Missing grain fields become `""` (`grain.py:35`) — a typo'd grain field never errors, it just collapses distinct rows into one hash (data loss via dedup); and unescaped `|` joining means `("a|b","c")` and `("a","b|c")` collide. `append_data` compounds it: `r.get(field.name)` inserts `None` for misspelled columns (`iceberg_setup.py:63`).

**Q6 — MEDIUM: Three `dq_runner` CLI subcommands are dead without `--spec`.** `get_latest_results()` returns `None` when `spec` is falsy (`dq_runner.py:516-518`), so `results` (`:670-671`), `scorecard` (`:673-677` — exits before reaching its own all-specs loop), and `badge` (`:711-714`) all print "No results found" in their documented no-arg form.

**Q7 — LOW:** `_rewrite_sql` uses naive substring replace on SQL (`dq_runner.py:161-167`) — a table reference inside a string literal or a name-prefix overlap rewrites wrongly. Dead compat alias `_COMPAT_DQ_RESULTS_DIR` (`dq_runner.py:32`). `staging.py:103` uses `p["status"]` (KeyError-able) where `:147` uses `.get`. `print()` in library code (`base_ingestor.py:213`).

### Security

Healthy baseline: no secrets, no `eval`/`pickle`/`shell=True`, threshold parsing is a constrained regex not `eval` (`dq_runner.py:83-85`), governance reads use parameterized DuckDB queries (`product.py:1203-1206`).

**S1 — HIGH: `query_iceberg` executes arbitrary, write-capable SQL from the LLM.** `base_mcp_server.py:405-437` runs whatever SQL the MCP client sends through a DuckDB connection with no read-only mode, no statement allowlist. DuckDB SQL can read and write local files (`COPY … TO`, `read_csv('…')`) — so a prompt-injected agent can exfiltrate or corrupt files on the host. The docstring acknowledges access control is a TODO ("Future RLS filters… inject here"). For the framework's flagship AI-facing deliverable, this needs read-only connection config or statement validation before any real deployment.

**S2 — LOW:** f-string SQL identifier interpolation (`iceberg_setup.py:113-118`, `dq_runner.py:252-255`) — inputs are internally-derived today; fragile, not exploitable. DQ rules are by-design arbitrary SQL from governance JSON; acceptable for the local trust model, worth documenting as a trust boundary.

### Testing

**T1 — CRITICAL: The enforcement core has no test file.** 487 tests pass, but `pipeline_gate.py` (1,149 lines) is exercised by exactly one incidental test (`test_governance_db.py:1109-1118`, event emission only). That's how A1 shipped: the `init→complete→check` round trip is tested nowhere. `staging.py`, `serve.py`, and the exporter↔gate round trip are similarly untested.

**T2 — HIGH: Test theater around the fake DQ gate.** `test_pipeline_runner.py` tests `ZoneResult`/`finalize()` dataclass logic (e.g. `:32, :53-58`) — it asserts how results are *summarized*, never that rules *execute*. The project's own rejection criterion ("tests that don't validate real behavior") applies to its own runner suite. **[judgment]** The irony is instructive: the staff-engineer agent enforces minimum test counts on domain specs, but the framework holds no equivalent bar for itself.

**T3 — MEDIUM:** No CLI-level (subprocess) tests at all, though the CLI is the contract with the agents; that's the layer where A1 and Q6 live.

### Performance

(Local-lakehouse scale: these are Medium at worst, but three are O(table) on hot paths.)

**P1 — MEDIUM:** `BaseIngestor._build_existing_grains` materializes the entire raw table into a Python set of tuples on *every ingest* (`base_ingestor.py:112-125`) — grows linearly with table history; `filter_existing_records` already demonstrates the better pattern (scan only the id column, anti-join in DuckDB).

**P2 — MEDIUM:** `BaseMCPServer.query_table` loads the full table then filters/limits in Python (`base_mcp_server.py:386-403`) — the `limit=100` is applied after full materialization, on the interactive AI path.

**P3 — MEDIUM:** Governance tables are append-only "latest row wins" with full-scan reads (`product.py:1198-1199`) and `_emit_governance_event` appends a spec_registry row on *every step event* (`pipeline_gate.py:366-376`) — unbounded growth with no compaction story.

**P4 — LOW:** `_validate_warehouse_population` scans every table fully just to count rows (`pipeline_gate.py:723-725`); snapshot summaries carry row counts.

### Dependencies

Healthy: 7 runtime deps, all maintained, floor-pinned; `uv.lock` present (2026-03-19); MIT license; no license conflicts. Not verified: CVE status (no network audit run). One drift: README's Stack section lists "Anthropic SDK" (`README.md:339`) which is not a dependency — and `check_headless_ready` actively *forbids* anthropic imports (`run.py:455-465`).

### DevEx & Operations

**D1 — HIGH: No CI whatsoever.** No `.github/`, no workflow files. Consequences are visible in the repo right now: 55 ruff errors (35 F541, 16 F401, 4 F841) in a repo with ruff configured as a dev dependency, and a critical regression (A1) merged un-caught in the most recent substantive commit.

**D2 — LOW:** The `SessionStart` hook pip-installs the plugin into whatever environment Claude Code runs in (`hooks/hooks.json:7`) — convenient, surprising side effect. Legacy `GRIST_*` env vars still active (`config.py:21,31,43,50`; `chaos_monkey/safety.py:33`).

### Documentation

Extensive and mostly excellent (README, 24 specs, 8 workflow docs). Drift, in priority order:

1. README's headline flows are the broken ones — the pipeline-gate workflow and `python -m brightsmith.run` DQ gating (`README.md:141,145,155, 303-318`) describe behavior that doesn't exist (High, because this is the sales pitch).
2. Session logging is mandated (CLAUDE.md Workflow References; `README.md:344`) but `docs/sessions/` was deleted in commit `3dce94b`.
3. CLAUDE.md says `REQUIRE_HUMAN_APPROVAL` lives in `src/config.py` (actual: `src/brightsmith/config.py`) and omits `infra/governance/` — the largest module.
4. README says 24 agents; `.claude/agents/` has 25 files.
5. `run.py:10-11` documents flags its own parser rejects.

### Strengths (preserve these)

- **The grain/promote design is right**: deterministic SHA-256 grain IDs + anti-join dedup + append-only Iceberg is a sound idempotency foundation (`grain.py`, `promote.py`); the modules are small and single-purpose.
- **Chaos monkey safety gate** is genuinely well-engineered: three independent conditions, fail-closed, no override path (`chaos_monkey/safety.py`).
- **Threshold evaluation avoids `eval`** where most projects wouldn't have (`dq_runner.py:83-128`).
- **Test breadth at module level is real** — `test_governance_db.py` (1,289 lines), chaos corruptors, contract deprecation, glossary loaders all have substantive suites that assert behavior.
- **Spec/workflow corpus** is an unusually disciplined paper trail; the pipeline-state file-hash integrity check (`pipeline_gate.py:584-592`) is a clever tamper detector.
- Clean dependency hygiene, clean repo (no committed artifacts, sensible `.gitignore`).

---

## Improvement Strategy

### Theme 1 — Finish the Iceberg-authoritative migration (or roll it back)

**Explains:** A1, A2, Q6, half of the doc drift.
**Root cause:** Commit `c1f41f5` moved *writes* to Iceberg but left *reads* on files, with a manual, shape-incompatible exporter as the only bridge.
**Target state & principle:** *One source of truth per artifact type, with reads and writes on the same side.* Either (a) reads go to Iceberg too (gate reads `pipeline_events`/`spec_registry`; contract verify reads `contract_metadata`), with file export demoted to a human-review convenience — or (b) restore file persistence as authoritative and treat Iceberg as the analytical mirror. Option (a) matches the project's stated direction; (b) is the fast rollback. Decide once, apply to gate + contracts + DQ results uniformly.

### Theme 2 — Make the gates real (no governance theater)

**Explains:** Q1, Q2, Q3, T2.
**Target state & principle:** *A gate that cannot fail is worse than no gate — it manufactures false assurance.* `run.py` calls the real `run_rules()`; errored P0 rules block (or require explicit acknowledgment via the existing `acknowledge` flow); every `except Exception` in a gate/enforcement path either re-raises, returns an explicit `UNKNOWN`/error status, or is narrowed to the specific expected exception with a logged justification.

### Theme 3 — CI as the framework's own staff-engineer

**Explains:** D1, the 55 ruff errors, how A1 shipped, T1/T3.
**Target state & principle:** *The framework should be held to the bar it imposes on domain specs.* GitHub Actions running `ruff check` + `pytest` on every push; new round-trip tests for `pipeline_gate` and `contract` CLIs (subprocess-level, the layer agents actually touch) so Theme 1's fix can never silently regress.

### Theme 4 — Tame the config and the god module

**Explains:** A3, A4, A5.
**Target state:** A single `get_config()` accessor (or frozen dataclass) so `configure()` actually works and the 40 function-local imports become unnecessary; `product.py` split along its existing section comments into `schemas.py`, `writers.py`, `queries.py`, `sync.py`, `cli.py` — mechanical, no behavior change. Zone-name duality reduced to one canonical set + one alias table at the boundary.

### Explicitly NOT recommending

- **No enterprise infra** (servers, real catalogs, RBAC, observability stacks) — wrong maturity; SQLite+local Iceberg is the right call for this stage.
- **No performance work beyond P1/P2** — P3/P4 don't hurt at current scale; fix opportunistically.
- **No rewrite of the agent/skill markdown layer** — it's content, it's working, and it's cheap to adjust after the Python contract underneath stabilizes.
- **Defer S1's full fix** (RLS/entitlements) to when an MCP server is exposed beyond localhost — but do the cheap read-only hardening now (it's one config flag plus a test).
- **Not chasing 80% global coverage** — targeted round-trip tests on the enforcement core beat a coverage number.

### Definition of done — measurable signals

1. Sandbox repro from this audit (`init → complete → check`) passes; a subprocess test encodes it.
2. `python -m brightsmith.run --zone bronze` against a seeded warehouse with one failing P0 rule **exits 1** with the rule named.
3. `contract generate` → `contract verify` round-trips green out of the box.
4. CI badge: ruff 0 errors, pytest green, on every push.
5. Zero `except Exception` in `run.py`, `pipeline_gate.py`, `dq_runner.py` gate paths without a narrowed type or explicit `UNKNOWN` propagation.
6. README quick-start commands all work as printed.

---

## Task Plan

### Milestone 0 — Safety net (do first; nothing else is safe to refactor without it)

| # | Task | Files | Acceptance criteria | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 0.1 | **CI pipeline**: GitHub Actions — `uv sync`, `ruff check`, `pytest` on push/PR | `.github/workflows/ci.yml` | CI red on lint error or test failure | **S** | None | — |
| 0.2 | **Fix the 55 ruff errors** (51 auto-fixable F541/F401; review 4 F841 by hand) | ~15 files | `ruff check` clean; tests still green | **S** | Low | 0.1 |
| 0.3 | **Characterization tests for the gate CLI**: subprocess tests for `init→check→complete→check→skip→validate` in a tmp project root (encode the *intended* behavior — they will fail red against today's code, which is the point) | new `tests/infra/test_pipeline_gate.py` | The audit's sandbox repro exists as a failing test | **M** | None | — |
| 0.4 | **Characterization tests for contract round trip**: `generate → list → verify` in tmp root | new `tests/infra/test_contract_roundtrip.py` | Failing test capturing A2 | **S** | None | — |

### Milestone 1 — Critical fixes (correctness of the core promise)

| # | Task | Files | Acceptance criteria | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 1.1 | **Repair pipeline-gate persistence** (decide Theme 1 direction; see sketch below) | `pipeline_gate.py`, `exporters.py` | 0.3 tests green; `check_zone_transition`/`audit` work on regenerated state | **L** | Medium — touches every agent workflow | 0.3 |
| 1.2 | **Make the headless DQ gate real**: replace `_run_dq_for_zone` body with `dq_runner.run_rules()`; map zone→spec/table via `ZONE_ALIASES`; propagate P0 failures & errors | `run.py:328-352` | Done-signal 2; failing P0 → exit 1; errored P0 → exit 1 or explicit ack | **M** | Medium — may surface real DQ failures downstream consumers were ignoring (that's the goal) | 0.1 |
| 1.3 | **Unify contract read/write side** per Theme 1 decision | `contract.py:108-182`, callers in `run.py`, `base_mcp_server.py` | 0.4 tests green | **M** | Medium | 1.1 (same decision) |
| 1.4 | **Close the P0-error loophole**: `validate_after_write` treats errored P0 rules as blocking | `dq_runner.py:296-309` | Test: P0 rule referencing a missing table raises `DQValidationError` | **S** | Low | — |
| 1.5 | **Read-only MCP SQL**: open the `query_iceberg` connection read-only / validate statements; document the trust boundary | `base_mcp_server.py:405-437` | `COPY TO` / DDL through the MCP tool fails; test included | **S** | Low | — |

### Milestone 2 — High-leverage improvements

| # | Task | Files | Acceptance criteria | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 2.1 | **Error-handling sweep of enforcement paths**: narrow/remove `except Exception` in `run.py`, `dq_runner.py`, `pipeline_gate.py`, `product.py:_query_table` (return explicit error status, never fake success) | 4 files, ~25 sites | Done-signal 5; tests for the formerly-swallowed paths | **L** | Medium — will surface latent failures | M1 |
| 2.2 | **Config refactor**: `get_config()` accessor; migrate the 6 import-time binders; deprecate module globals | `config.py` + 6 modules | `configure()` test proving paths actually change post-import | **M** | Medium — wide but mechanical | 0.1 |
| 2.3 | **Split `product.py`** along existing section comments; keep `governance_db.py` shim but stop re-exporting private names | `infra/governance/*` | No file > ~700 lines; imports unchanged for public API; tests green | **M** | Low (mechanical) | 2.2 |
| 2.4 | **Grain hardening**: raise (or warn loudly) on missing grain fields; escape the `\|` delimiter; dedupe within batch in `filter_existing_records` (and `BaseIngestor`) | `grain.py`, `iceberg_setup.py:91-123`, `base_ingestor.py` | Tests: missing-field raises; in-batch dup appends once | **M** | Medium — could change existing record_ids; gate behind explicit choice (see Open Questions) | 0.1 |
| 2.5 | **Fix `dq_runner` no-`--spec` CLI paths** (`results`/`scorecard`/`badge`) — add an all-specs implementation of `get_latest_results` | `dq_runner.py:515-535, 670-714` | All three subcommands produce output with no flags | **S** | Low | — |

### Milestone 3 — Quality & polish

| # | Task | Effort |
|---|---|---|
| 3.1 | Doc-truth pass: README quick-start verified by hand, agent count, `src/brightsmith/config.py` path in CLAUDE.md, document `infra/governance/`, fix `run.py` docstring flags, resolve session-logging mandate vs deleted `docs/sessions/` | **S** |
| 3.2 | Zone-name canonicalization: one set internally, aliases at boundaries only | **M** |
| 3.3 | Perf: id-column-only dedup scan in `BaseIngestor` (mirror `filter_existing_records`); push filters/limit into the scan in `query_table` | **M** |
| 3.4 | Remove `GRIST_*` legacy env vars (one deprecation release), dead `_COMPAT_DQ_RESULTS_DIR`, `staging.py` `.get` consistency, `print` → logger | **S** |
| 3.5 | Governance-table compaction story (latest-row-wins materialization or periodic rewrite) | **L** — defer until volume hurts |

### Quick wins (do immediately, ~1 day total)

**0.1** CI skeleton · **0.2** ruff `--fix` · **1.4** P0-error loophole · **1.5** read-only MCP connection · **2.5** dq_runner CLI fixes · **3.1** README truth pass.

### Implementation sketches — top 3

**1.1 Gate persistence.** Recommended direction: make Iceberg the read side too, with a JSON-file fallback for pre-migration state. Steps: (a) add `PipelineGate._load()` path that reconstructs `{steps, skipped_steps, approvals, zone, mode}` by replaying `get_pipeline_events(spec)` + latest `spec_registry` row — the event log already records STARTED/COMPLETED/SKIPPED/APPROVED with agents and outputs, so state is derivable; (b) keep reading a legacy JSON file when present and newer (the committed brightgemma file must keep working); (c) fix `export_pipeline_state_to_files` to emit the full legacy shape (`spec`, `steps`, …) so the human-readable file and `check_zone_transition` agree; (d) `init` must materialize *something* durable — simplest is to also write the JSON file (`_save()` becomes "write file", losing nothing of Iceberg-authoritativeness since events remain canonical). Gotchas: `_emit_governance_event` writes a spec_registry row per event (P3) — replay must take latest-per-step, and `output_hash` isn't in the event schema yet (add a column or keep hashes file-side); per-process `PipelineGate` instances must re-read, never cache across CLI calls. The alternative — just restore file `_save()` (a ~5-line revert) — is an acceptable hotfix to ship *first*, with Iceberg reads as the follow-up; do the revert inside this task as step zero so the bleeding stops day one.

**1.2 Real headless DQ.** Replace `_run_dq_for_zone`'s loop with: `result = run_rules()` filtered to rules whose `tables` match the zone (reuse `_extract_table_refs`/`ZONE_ALIASES` so `raw.` and `bronze.` both match); derive `(p0_ok, passed, failed, p0_failures)` from `result["results"]` + rule priorities; **treat rule errors as failures** (consistent with 1.4). Key decision: `run_rules` writes governance results as a side effect — that's desirable here (run history becomes real), but tests must use a tmp `PROJECT_ROOT`. Gotcha: `validate-only` mode currently skips transforms but should still run real DQ — it becomes genuinely useful once this lands. Delete the now-false "Simplified" comment and the `except Exception: return (True, …)`; let config errors surface as `TRANSFORM_ERROR`/`CONFIG_ERROR` exits.

**0.3 Gate characterization tests.** Use `subprocess.run([sys.executable, "-m", "brightsmith.infra.pipeline_gate", …], env={**os.environ, "BRIGHTSMITH_PROJECT_ROOT": tmp})` — *not* in-process calls — because the bug class is precisely cross-process state. Cover: init creates durable state; complete→check unblocks; non-skippable skip refused; validate fails on NOT_STARTED; `check-transition` works on exporter-regenerated files; tamper test (modify an output file, validate flags hash mismatch — preserving the existing integrity feature). Gotcha: the governance Iceberg side effects need the tmp root too, or writes will land in the real `data/`; assert on exit codes and stderr, since that's the agents' actual interface.

---

## Addendum (2026-06-11) — finding reported from the field

**A6 — HIGH: Iceberg warehouses are not relocatable — absolute paths are baked into all metadata layers, and a moved warehouse fails *silently* with empty reads.**
`get_catalog()` resolves the warehouse to an absolute path (`iceberg_setup.py:29-30`), and PyIceberg then embeds that prefix in four layers: the SQLite catalog rows (`metadata_location` / `previous_metadata_location`), every `*.metadata.json` (table `location` + per-snapshot `manifest-list` paths), every manifest-list `.avro`, and every manifest `.avro` (→ parquet data-file paths). Clone the project to another path, run it on another machine, or containerize it, and every read returns **empty results rather than an error** — the worst failure mode for a governance product. **Field evidence:** the `futureproof-data` consumer project hit this the night before a deadline and had to ship a 5,740-file mechanical rewrite (`futureproof-data` commits `b81c7b7a` "rebase metadata paths from absolute to repo-root-relative" and `9f789d0d` "pin CWD to project root + drop hardcoded WORKDIR", 2026-05-15 22:27); its `scripts/rebase_iceberg_paths.py` docstring states the root cause lives here: *"The Brightsmith ingestors will keep writing absolute paths; this script is the last-mile normalizer."* Consequence for open-sourcing: any user who commits or moves a warehouse (the README's "clone and run" story) gets a silently empty pipeline. Remediation tracked as **WP-1.6** in `docs/specs/audit-remediation-open-source-readiness.md`: adopt the field-proven rebase script as a framework CLI (`python -m brightsmith.infra.relocate`), add stale-prefix detection that fails loudly with the fix command, and document relocation in the README.

## Open Questions (need a human decision)

1. **Theme 1 direction:** Iceberg-authoritative reads (finish the migration) or revert to file-authoritative state (rollback)? Recommendation: hotfix-revert now, finish migration next — but the `governance-database-only` spec suggests a strategic preference worth honoring.
2. **Grain ID compatibility (task 2.4):** Fixing the `|`-delimiter/missing-field behavior changes `record_id` values for any data that hit those edge cases. Is the existing consumer project (`sec_edgar_grist`) warehouse re-derivable, or is a migration/compat mode needed?
3. **Errored-P0 policy (1.4):** Should an errored P0 rule hard-block (recommended), or block-unless-acknowledged via the existing `acknowledge` flow?
4. **Session logging:** `docs/sessions/` was deleted but both README and CLAUDE.md still mandate it. Drop the mandate, or restore the practice (possibly into the governance `documents` table instead of git)?
5. **MCP threat model:** Is `BaseMCPServer` ever intended to run non-localhost or against data the operating user shouldn't fully control? That decides whether S1's full fix (entitlements/RLS) gets scheduled or stays deferred.
6. **`GRIST_*` env vars:** Safe to remove, or does the consumer project still set them?
