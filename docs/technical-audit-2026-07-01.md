# Brightsmith — Technical Audit & Improvement Plan

**Date:** 2026-07-01 · **Method:** analysis only, no code modified · **Branch:** `audit-remediation` · **Version:** 0.3.0

**Empirically verified this session:** `ruff check src tests` → clean; `pyright` → 0 errors/0 warnings; full suite → **597 passed** in 74s at **59% line coverage**; SQL/security surfaces read line-by-line; connection lifecycle traced across all 16 `duckdb.connect()`/`get_catalog` call sites.

**Lighter review (structural, not line-by-line):** the 24 specs, the 25 agent prompt files, chaos-monkey corruptors, `cab.py`, `period_disambiguator`, `concept_normalization`.

---

## Executive Summary

**Overall health: B+ (strong for a pre-1.0 solo OSS framework).** This is a genuinely well-engineered codebase with real discipline: deterministic grain hashing, idempotent promotes, a loud-failure doctrine that refuses to confuse "read failed" with "no data," a locked-down MCP SQL surface, and a fast behavior-asserting test suite. CI enforces ruff + pyright + tests + a warehouse-relocation guard, and all four are green. I found **no Critical or High correctness/security bugs** in the core paths.

The one theme worth taking seriously is **resource/connection hygiene**: DuckDB connections and PyIceberg `SqlCatalog` engines are opened but never closed or disposed across ~12 sites, producing 542 `ResourceWarning: unclosed database` warnings per test run. It's cosmetic in single-shot CLI usage but real in the MCP server — the flagship deliverable, which **runs as a persistent stdio process for the whole client session** (`serve.py` → `serve()` → `stdio_server()`, `base_mcp_server.py:680`). Every `query_iceberg` / `query_iceberg_simple` tool call opens a fresh DuckDB connection that is never closed, so the leak is *monotonic over the life of the session* in exactly the query hot path. (The `SqlCatalog` engine, by contrast, is instance-cached once per server — `base_mcp_server.py:212-217` — so it is not the problem for the server; catalog-rebuild leakage lives in the governance `queries.py` read path instead.)

**Grade justification:** architecture is sound, security is clean, tests assert behavior — no Critical findings. It's held back from A- by two **High** resource-hygiene findings that the confirmed persistent-server lifetime elevates: (1) the per-query DuckDB connection leak (Q1) and (2) the unhandled-error/leak path on the untrusted MCP surface (S1). Both are S-effort fixes — the grade reflects that the *defects are real in the flagship path* but *cheap to close*, not that the design is weak. Also held back by coverage that is genuinely thin (not just subprocess-masked) on `lineage.py` and the governance sync/migration paths.

**Top 3 risks**

1. **Per-query DuckDB connection leak in the persistent MCP server.** `read_with_duckdb` (`iceberg_setup.py:290`) opens a DuckDB connection and never closes it; it's called on every `query_iceberg_simple` (`base_mcp_server.py:486`), and `query_iceberg` (`:527`) does the same inline. Because the server is a long-lived stdio process, these accumulate for the entire session. Governance reads leak the same way (`queries.py:101`).
2. **`query_iceberg` execution errors are unhandled** (`base_mcp_server.py:553`): malformed/invalid untrusted SQL raises out of the tool handler *and* leaks the connection (the `con.close()` two lines down is skipped) — inconsistent with the structured `[{"error": …}]` it returns for validation rejections.
3. **Coverage blind spots on real logic**, not just CLIs: `lineage.py` 25%, `governance/sync.py` 48%, `migration.py` 54%, `golden_dataset.py` 28%; `verification.py` is a live governance entry point (used by `/assay` and `@staff-engineer`) at **0%**.

**Top 3 opportunities**

1. One `_connect()` context-managed helper + catalog caching kills the whole leak class and speeds up governance reads (they rebuild a SQLAlchemy engine per query today). ~S–M, high leverage.
2. Wrap `query_iceberg` execution in try/finally → structured errors + no leak. ~S, closes the last rough edge on the security-sensitive surface.
3. Targeted tests for `lineage` emit paths and `sync/migration` — the two least-covered pieces of real logic.

---

## Remediation Status (applied 2026-07-01, same session)

All connection-lifecycle findings, the swallow audit, the M2 tests, **and the M0 safety net (M0.1 characterization tests + M0.2 leak-regression guard)** were implemented and verified. **Suite: 628 passed + 1 documented xfail** (was 597). `ruff` and `pyright` clean. **`ResourceWarning: unclosed database` count: 542 → 0.** Coverage of the refactored/least-covered modules rose materially: **`lineage.py` 25% → 74%**, `golden_dataset.py` 28% → 58%, `migration.py` 54% → 60%, `sync.py` 48% → 51%, `verification.py` 0% → 79%; total 59% → 64%.

**Two real bugs were surfaced by the M0.1 characterization tests:**
1. **FIXED — `lineage.py` `cmd_verify` / `cmd_generate_docs` read `con.description` after `con.sql()`**, which is `None` in DuckDB 1.5 (only `con.execute()` populates it). These CLI paths (`lineage verify`, `lineage generate-docs`) were at 25% coverage and **broken in production** — they raised `TypeError: 'NoneType' object is not iterable`. Fixed to read the relation's own `.columns`.
2. **FIXED — glossary sync was not idempotent.** `sync_from_files`' docstring promises "Idempotent via promote()", but the `glossary_terms` grain was `["term_id", "updated_at"]` while `sync_glossary_term` stamps `updated_at=now()` on every call — so each re-sync minted a new grain and re-appended every term (unbounded governance-DB growth). Fixed by making the grain **content-based** — `["term_id", "term", "definition", "category", "source", "approval_status"]` — so re-syncing an unchanged glossary is a no-op while a genuine edit to any content field still creates a new row (`updated_at` remains a non-grain "last synced" marker). Verified: unchanged re-sync → 0 rows; a definition edit → 1 new row. Safe because `glossary_terms` is write-only with no reader depending on its grain. `dq_rules` re-migration bumps `version` by design (documented, not a bug).

   *Related observation (not changed):* `spec_registry` (`["spec_name", "status", "updated_at"]`) and `agent_activity` (`[…, "event_time"]`) share the same now()-in-grain pattern, so they also grow on repeated sync — but unlike glossary they have a reader (`get_current_specs` takes the latest via `MAX(updated_at)`), so their history rows are consumed meaningfully. Left as-is; flagged for awareness.

| Item | Finding | Status |
|---|---|---|
| QW1 | S1 — `query_iceberg` error handling + `finally` | ✅ done; new tests assert bad SQL → structured error, no crash, no leak |
| QW2 / M1.1 | Q1 — context-manage every `duckdb.connect()` (`iceberg_setup`, `base_mcp_server`, `lineage`×8, `queries`, `enterprise`, `cli`, `integration_test_harness`) | ✅ done |
| QW3 | Q3 — `dq_runner` connection in `try/finally` | ✅ done |
| M1.2 | Q2 — `SqlCatalog` cached per (warehouse, catalog, project) + `reset_catalog_cache()`; `configure()` resets it; autouse test fixture | ✅ done — root cause of the 542 warnings |
| M2.1 | T2 — `verification.py` gate-semantics tests | ✅ done (10 tests: pass-rate, bucketing, threshold exit codes, tolerance, conditional skip) |
| M2.2 | T1 — `verify_golden_dataset` branch tests vs real tmp warehouse | ✅ done (MATCH/CLOSE/MISMATCH/MISSING, tolerance override, missing-table) |
| M3.1 / swallow audit | Q4 — silent-swallow doctrine | ✅ done — guard now scans the **entire `src` tree**; every broad `except` re-raises, is narrowed, or is allowlisted with a written justification |
| M0.1 | T1 — characterization tests for `lineage` / `sync` / `migration` | ✅ done — `test_lineage_roundtrip.py`, `test_governance_sync_migration.py`; found 2 real bugs (above) |
| M0.2 | Leak-regression guard | ✅ done — `test_no_unclosed_connections.py` asserts no `unclosed database` ResourceWarning escapes a read/query flow |
| M3.2 | `pytz` | ✅ done earlier — documented as a required DuckDB runtime dep |

**Silent-swallow audit outcome:** the guard test (`test_no_swallowed_exceptions.py`) was changed from a hand-maintained 8-file list to auto-discovery of all `src/**/*.py`. It flagged 44 broad handlers; 6 were **narrowed** to specific types (per-item hot loops, file-parse reads), the rest were made **loud** (added logging where truly silent) and **allowlisted with per-handler justifications**. A newly added module with an unjustified `except Exception` now fails CI automatically.

---

## Repo Map

**Purpose:** A domain-*agnostic* AI-agent data pipeline framework. It ingests raw data (Bronze), discovers the domain from the data itself, normalizes (Silver), builds data products (Gold), and serves them to LLMs over MCP — emitting governance artifacts (DQ rules, contracts, lineage, approvals, CAB decisions) at every step. Shipped **both** as a pip/hatchling package and a Claude Code plugin; orchestration is 25 markdown agent personas gated by a Python state machine.

**Maturity:** Pre-1.0 solo OSS, extracted from a production SEC-EDGAR pipeline, one downstream consumer. Has CI, CHANGELOG, LICENSE, security section — clearly being prepped for public release.

**Stack:** Python 3.11+ · DuckDB + Iceberg extension · PyIceberg (SQLite catalog) · PyArrow · MCP SDK · uv/hatchling · pytest · ruff · pyright.

| Path | What it is | LOC |
|---|---|---|
| `src/brightsmith/infra/` | Cross-cutting engines: `pipeline_gate` (1204), `cab` (1006), `lineage` (846), `contract` (838), `dq_runner` (816), `relocate` (440) | ~7k |
| `src/brightsmith/infra/governance/` | Iceberg governance DB, split into `writers`/`queries`/`schemas`/`sync`/`migration`/`cli`/`exporters`; `product.py` is a re-export façade | ~3.4k |
| `src/brightsmith/{bronze,silver,mcp}/` | Zone base classes: `BaseIngestor`, concept normalization, `BaseMCPServer` (686) | |
| `run.py` / `serve.py` / `setup.py` | Headless runner (740), MCP entry, scaffolder | |
| `agents/` (25), `skills/` (9), `hooks/` | Plugin surface; `require-subagent-type.sh` enforces named agents | |
| `docs/specs/` (24), `docs/workflows/` (5) | Spec-driven corpus | |

**What surprised me (positively):** the prior audits' findings are genuinely closed — agent defs are single-sourced (`.claude/agents/` no longer exists), `plugin.json` and `pyproject.toml` both read `0.3.0`, CI runs pyright, and no entity-specific data is hardcoded in `src` (the stated invariant holds — grep for CIK/ticker/fiscal literals came back empty).

---

## Audit Report

*Facts carry `file:line`. Judgments are labeled **[judgment]**.*

### Architecture & Design — healthy

Clean layering: `grain` → `promote` → `iceberg_setup`, zone base classes, a config snapshot read at call-time via `get_config()`. No circular-dependency or god-object problems in the data path. The two largest files (`pipeline_gate.py` 1204, `cab.py` 1006) are CLI-heavy state machines, not tangled logic.

- **A1 — LOW [judgment]: Config back-compat shim has asymmetric semantics.** `config.py:243` `__setattr__` replaces a single field *without recomputing derived paths*, whereas `configure()` (`config.py:217`) recomputes them via `_derive`. So `config.PROJECT_ROOT = x` leaves `WAREHOUSE_PATH` etc. stale, but `configure(project_root=x)` doesn't. Documented and slated for 0.4.0 removal — flagging only so it isn't mistaken for a bug later.

### Code Quality — good, with a leak pattern

- **Q1 — HIGH: DuckDB connections leaked at 5+ sites, monotonically in the persistent server.** `read_with_duckdb` (`iceberg_setup.py:290`) never calls `con.close()`; `_query_table` (`queries.py:101`), `enterprise.py:152`, `cli.py:109`, and all 8 `lineage.py` connect sites likewise never close. `read_with_duckdb` is invoked from the **MCP server** (`base_mcp_server.py:486`) and the **bronze ingestor** (`base_ingestor.py:119`). The MCP server runs as a **persistent stdio process for the whole session** (`serve.py`, `base_mcp_server.py:680`), so every tool call leaks one connection that is never reclaimed until the process exits — the leak is unbounded in session length. (Raised from Medium after confirming server lifetime.)
- **Q2 — MEDIUM (LOW for the MCP server): `SqlCatalog` engines are never disposed, and `get_catalog` has no caching.** `queries.py:32` builds a fresh SQLAlchemy engine + SQLite pool on *every* governance read via `_get_governance_table` — this is the root of the 542 `ResourceWarning: unclosed database` warnings and unnecessary per-read overhead on the most-read store in the system. Note this does **not** affect the MCP server, which instance-caches its catalog once (`base_mcp_server.py:212-217`); the impact is confined to the governance `queries.py` read path and other per-call `get_catalog` sites.
- **Q3 — LOW: `dq_runner` connection isn't exception-safe.** `con` (`dq_runner.py:380`) is closed at line 399 but not via try/finally; a raise in the rule loop leaks it. Low blast radius (single-shot run), but trivial to harden.
- **Q4 — LOW [judgment]: 74 `except Exception` handlers.** Most in `lineage.py` legitimately swallow-and-continue for observability (defensible), and the governance-read path deliberately does the opposite (see Strengths). No bare `except:`. The concentration in `lineage.py` (returning `[]`/`None`) is the main place a *new* silent failure could hide.

### Security — clean

No secrets, no `eval`/`exec`/`pickle`/`yaml.load` anywhere in `src`. The untrusted surface (`query_iceberg`) has real defense in depth: a first-keyword allowlist that strips comments and rejects embedded semicolons (`base_mcp_server.py:65-114`) plus `SET enable_external_access=false` on every connection (`:532`). Governance queries are parameterized (`queries.py:103`). f-string SQL exists only over trusted inputs (catalog `metadata_location`, committed rule SQL).

- **S1 — HIGH: `query_iceberg` execution path is not error-wrapped, on a persistent surface.** `con.execute(sql).fetchall()` (`base_mcp_server.py:553`) has no try/except. Invalid untrusted SQL (bad column, unknown table, type error) raises out of the tool handler instead of returning the structured `[{"error": …}]` used for validation failures, and skips `con.close()` (`:555`) → a leaked connection per failed query. Because the server is long-lived and the input is LLM/prompt-injection-facing, a single malformed query recurs across the session, each occurrence both failing loudly *and* leaking. Not a container-escape (external access is off), but a robustness + resource hole on the one surface that receives untrusted input. (Raised from Medium after confirming server lifetime.)

### Testing — strong count, uneven depth

597 behavior-asserting tests (subprocess-level gate tests, contract round-trips, an AST-based no-swallow guard) against ~10k test LOC. CI reports `--cov`. Coverage of CLI modules (`run.py` 44%, `pipeline_gate.py` 25%, `setup.py`/`serve.py` 0%) is *understated* because those are exercised through `subprocess` (confirmed in `test_pipeline_gate.py`, `test_pipeline_runner.py`).

- **T1 — MEDIUM: Genuinely thin coverage on non-CLI logic.** `lineage.py` 25% (most emit/read paths untested), `governance/sync.py` 48%, `migration.py` 54%, `golden_dataset.py` 28%. These aren't subprocess artifacts — they're importable modules the suite doesn't drive.
- **T2 — MEDIUM: `verification.py` at 0% is a load-bearing gate, not dead code (traced).** It's a thin wrapper over `golden_dataset.verify_golden_dataset` but owns the gate semantics nothing else has: pass-rate aggregation, the `--threshold` (default 80%) decision with `sys.exit(1)` on FAIL, `MATCH`/`CLOSE`-as-pass bucketing, and "no golden datasets ⇒ FAIL" (`verification.py:80-82`). It is the **MCP-zone completion gate** — `/assay` step 5 consumes its exit code (`skills/assay/SKILL.md:30`) and `@staff-engineer` requires "`verification run` pass rate ≥ 80%" before marking a spec COMPLETE (`agents/staff-engineer.md:122`). Not superseded by `run.py:_verify_golden_datasets` (`:536`), which calls the same engine directly but *bypasses* the threshold/tolerance gate. So the 0% coverage sits on the exact logic that decides whether an MCP-zone spec ships.

### Performance — adequate

DQ reads use `iceberg_scan()` for predicate pushdown (`dq_runner.py`), and grain dedup uses a DuckDB anti-join rather than N² comparison (`iceberg_setup.py:331`). The only real inefficiency is Q2 (per-query catalog/engine construction). No unbounded queues/files.

### Dependencies — clean

Tight, current (`duckdb>=1.0`, `pyiceberg>=0.7`, `mcp>=1.0`), `uv.lock` present, dev group isolated, no heavy/unmaintained packages. `pytz>=2026.1.post1` looks unused at first glance (nothing in `src`/`tests` imports it) but is in fact a **required runtime dependency of DuckDB's Arrow/timestamp bridge** — DuckDB imports it lazily and does not declare it in its own metadata, so it must be pinned explicitly here or `read_with_duckdb` fails with `ModuleNotFoundError: pytz`. Verified empirically: removing it breaks the bronze ingest read path. Now documented with an inline comment in `pyproject.toml` so it isn't mistaken for cruft and deleted.

### DevEx & Ops — healthy

CI matrix on 3.11/3.12 runs ruff (`E,F,B,I,UP,SIM`), pyright, pytest+coverage, and `relocate --check` to guard committed warehouses. `paths-ignore` skips CI on doc/agent/skill-only changes — reasonable. Repo hygiene is clean: `.DS_Store`, `.coverage`, `__pycache__` are all gitignored and none are tracked.

### Documentation — accurate

README matches the code I read (module table, MCP trust-boundary description, relocation story all check out). CLAUDE.md is an unusually precise operating contract. No stale-doc-vs-code contradictions surfaced in the areas I verified.

### Strengths (preserve these)

- **`GovernanceReadError` (`queries.py:69`)** — read failure can never masquerade as "no data." For a governance product this is exactly right, and it's enforced by an AST guard test.
- **Deterministic grain hashing (`grain.py`)** — delimiter escaping, missing-key `ValueError`, explicit `None` handling. Idempotent `promote` with in-batch dedup *before* the anti-join (`iceberg_setup.py:311`) is a subtle correctness win.
- **Loud relocation failure (`iceberg_setup.py:29`)** — refuses to return silently-empty results from a moved warehouse, names the repair command.
- **Locked MCP SQL surface** with a documented trust boundary.
- No entity-specific data hardcoded in source (invariant verified).

---

## Improvement Strategy

**Theme 1 — Own the connection lifecycle.** Explains Q1, Q2, Q3, S1 and all 542 warnings. The MCP server is confirmed to be a **persistent stdio process for the whole client session**, which promotes the per-query DuckDB leak (Q1) and the unhandled-error path (S1) to High — they compound over session length. *Target state:* every DuckDB connection is context-managed (`with duckdb.connect() as con:`); `SqlCatalog` is cached per (warehouse, catalog) key (helps the governance read path, not the server, which already instance-caches). *Principle:* resources are acquired and released in the same scope; the flagship long-running server must not leak.

**Theme 2 — Uniform error contract on the untrusted surface.** *Target:* `query_iceberg` returns `[{"error": …}]` for *execution* failures just as it does for validation failures, inside a `finally` that closes the connection. *Principle:* a prompt-injection-facing tool should never crash its host or leak.

**Theme 3 — Test the logic the suite skips.** *Target:* `lineage` emit/read paths and `sync/migration` covered; `verification.py` gets at least a smoke test. *Principle:* coverage should track real logic, and a live governance gate at 0% is a blind spot regardless of subprocess nuance.

**Explicitly NOT recommending:**

- No RLS/entitlements on MCP — correctly deferred to non-localhost deployment; premature now.
- No decomposition of `pipeline_gate.py`/`cab.py` for its own sake — they're large but CLI-shaped and well-tested end-to-end; churn risk outweighs payoff pre-1.0.
- No coverage *gate* — reporting is enough for a solo project; a hard floor invites gaming.

**Definition of done:** zero `ResourceWarning: unclosed database` in a test run; `query_iceberg` returns structured errors and leaks nothing on bad SQL; `lineage.py` and `sync/migration` coverage materially up; `verification.py` has a test.

---

## Task Plan

### Quick wins (high impact, S effort — do immediately)

> Note: QW1 and QW2 address the two **High** findings (S1, Q1). They remain S-effort but are now top-priority, not merely "nice quick wins" — the persistent server makes both compound over a session.

- **QW1 — Fix `query_iceberg` error handling (S1, High).** Wrap `con.execute(sql)` in try/except returning `[{"error": str(e)}]`, close `con` in `finally`. `base_mcp_server.py:553`. *~30 min.*
- **QW2 — Close `read_with_duckdb`'s connection (Q1, High).** Convert to `with duckdb.connect() as con:`. One function, fixes the MCP + ingestor hot paths. `iceberg_setup.py:290`. *~15 min.*
- **QW3 — `dq_runner` try/finally around the rule loop (Q3).** `dq_runner.py:380-399`. *~15 min.*

### Milestone 0 — Safety net

| ID | Title | Files | Acceptance | Effort | Risk | Deps |
|----|-------|-------|-----------|--------|------|------|
| M0.1 | Characterization tests for `lineage` emit/read + `sync`/`migration` before touching them | `tests/infra/` | New tests pin current behavior; coverage of these modules up materially | M | Low | — |
| M0.2 | Add a test asserting **no `ResourceWarning`** escapes a representative governance-read + MCP-query flow | `tests/…` | Test fails today, passes after M1 | S | Low | — |

### Milestone 1 — Correctness & hygiene (the leak class)

| ID | Title | Files | Acceptance | Effort | Risk | Deps |
|----|-------|-------|-----------|--------|------|------|
| M1.1 | **[High]** Context-manage every `duckdb.connect()` — starting with the MCP server hot path (`read_with_duckdb`, `query_iceberg`) | `iceberg_setup.py:290`, `base_mcp_server.py:527`, `lineage.py` (8), `queries.py:101`, `enterprise.py:152`, `cli.py:109` | All read/write paths close connections; a persistent-server soak test shows flat connection count; M0.2 green | M | Low | M0.1 |
| M1.2 | **[Low for MCP]** Cache/dispose `SqlCatalog` per (warehouse, catalog) — value is the governance read path, *not* the server (already instance-cached) | `iceberg_setup.py:191`, `queries.py:32` | Governance reads reuse one engine; 542-warning count → 0 | M | Med (shared-catalog semantics; verify the `load_table` monkeypatch still applies per instance) | M0.2 |
| M1.3 | QW1–QW3 folded in if not already done | as above | — | S | Low | — |

### Milestone 2 — Coverage of real logic

| ID | Title | Files | Acceptance | Effort | Risk | Deps |
|----|-------|-------|-----------|--------|------|------|
| M2.1 | **Gate-semantics tests for `verification.py`** (load-bearing MCP-zone completion gate) | `tests/infra/test_verification.py` (new) | Cover: pass rate ≥/< threshold → exit 0/1; `MATCH`/`CLOSE` count as pass, `MISMATCH`/`MISSING` don't; `--tolerance` override changes CLOSE↔MISMATCH; **pin the "no golden datasets ⇒ exit 1" behavior** (and confirm that's intended for specs that legitimately have none) | S | Low | — |
| M2.2 | Drive `golden_dataset.verify_golden_dataset` directly | `tests/infra/test_golden_dataset.py` | pass/fail/tolerance branches covered | S | Low | — |

### Milestone 3 — Polish

| ID | Title | Files | Acceptance | Effort | Risk |
|----|-------|-------|-----------|--------|------|
| M3.1 | Narrow the broadest `lineage.py` `except Exception` to `(duckdb.Error, OSError)` where the failure mode is known; allowlist the rest with a one-line justification | `lineage.py` | no-swallow guard extended to `lineage` | M | Low |
| M3.2 | ~~Re-pin `pytz`~~ **DONE** — pytz confirmed as a required DuckDB runtime dep (not cruft) and documented with an inline comment in `pyproject.toml` | `pyproject.toml` | comment explains the hidden dependency; do **not** remove pytz | — | — |

### Top-3 implementation sketches

**QW1 / M1.3 — `query_iceberg` error contract.**

```python
try:
    result = con.execute(sql).fetchall()
    columns = [d[0] for d in con.description]
    return [dict(zip(columns, row, strict=False)) for row in result]
except duckdb.Error as e:
    logger.warning("query_iceberg execution failed: %s", e)
    return [{"error": f"query failed: {e}"}]
finally:
    con.close()
```

*Gotcha:* keep the catch to `duckdb.Error`, not bare `Exception`, so genuine bugs still surface; the point is that *user SQL* errors become data, not crashes. Add a test with a deliberately invalid column.

**M1.1 — Context-manage DuckDB.** Mechanical: `with duckdb.connect() as con:` at each site, dedent the body. *Gotcha:* `lineage.py` has functions that `return` mid-body — the `with` still closes correctly, but re-read each to ensure nothing holds the connection past the block (e.g. returning a live relation instead of `fetchall()`).

**M1.2 — Catalog cache.** Add a module-level `dict` in `iceberg_setup` keyed by `(str(warehouse_path), str(catalog_path))`; return the cached `SqlCatalog` if present. *Gotcha:* the per-instance `load_table` monkeypatch (`iceberg_setup.py:223`) must be applied *before* first cache store, and the cache must key on resolved absolute paths (the function already `.resolve()`s them). Provide a `reset_catalog_cache()` for tests that reconfigure `config` between cases, or the cache will hand back a catalog pointed at the previous tmp path.

---

## Open Questions (need a human)

1. ~~**MCP server lifetime**~~ **RESOLVED:** confirmed **persistent** — `serve.py` runs `asyncio.run(server.serve())` and `serve()` opens `stdio_server()` (`base_mcp_server.py:680`), a long-lived stdio loop for the whole client session. This promotes Q1/S1 to **High** and confirms M1.1 (DuckDB context-management) as the priority; M1.2 (catalog caching) is downgraded to Low for the server (it already instance-caches) and retained only for the governance `queries.py` read path.
2. ~~**`verification.py` status**~~ **RESOLVED:** load-bearing, not legacy — tested (M2.1), not deprecated. Sub-decision resolved to a **conditional policy** (owner's call): golden dataset **present → enforce it** (mismatch / pass-rate < threshold = hard FAIL); golden dataset **absent → SKIP** (exit 0, printed loudly as `SKIPPED: …`). A dataset that exists but yields no checkable values (e.g. malformed table ref) still FAILs — skip is reserved for genuine absence. Note this changes only `verification run`'s exit code; `@staff-engineer`'s *separate* "golden dataset exists" check (`golden_dataset verify --spec`) and the CLAUDE.md "every consumable spec requires a golden dataset" doctrine are unchanged, so consumable specs can still be required to have one at that gate.
3. ~~**Coverage intent pre-1.0**~~ **RESOLVED:** reporting-only, no numeric floor pre-1.0 (owner's call). Coverage stays visible in CI; effort goes to the targeted M2 tests, not a global threshold. Revisit a low regression-ratchet floor (≈ today's level, scoped to `infra/` + zone modules) once there are external contributors near 1.0.
4. ~~**`pytz` pin**~~ **RESOLVED:** not an artifact — it is a required-but-undeclared runtime dependency of DuckDB (Arrow/timestamp bridge). Confirmed by removing it and watching the bronze read path fail with `ModuleNotFoundError: pytz`. Kept and documented with an inline comment in `pyproject.toml`. Lesson: a `grep import` "unused dependency" check is insufficient for native extensions that import pure-Python modules lazily — verify by removal + test, not by static search.

**Areas I reviewed lightly (stated for honesty):** the 24 specs and 25 agent prompts (skimmed for consistency, not line-audited), `cab.py` blast-radius logic, chaos-monkey corruptors, and `concept_normalization` — all structurally sound on inspection but not traced branch-by-branch.
