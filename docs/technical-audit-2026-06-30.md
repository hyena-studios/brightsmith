# Brightsmith — Technical Audit & Improvement Plan

**Date:** 2026-06-30 · **Auditor:** Claude (principal-level, analysis only — no code modified)
**Branch:** `audit-remediation` · **Version:** 0.3.0

**Scope & method:** Read all manifests, CI, config, `run.py`, `config.py`, the MCP query path, `dq_runner`, and structural passes over the large infra modules (`pipeline_gate`, `cab`, `lineage`, `contract`, `governance/*`). **Empirically verified**, not just inferred: the full test suite (590 passed, 67s), `ruff check` (clean), and a **live 3-process reproduction** of the pipeline-gate state machine. Lighter review: chaos-monkey corruptors, `period_disambiguator`, `concept_normalization`, the 25 agent prompt files, and the 24 specs.

**Important context:** A prior audit (`docs/technical-audit-2026-06-10.md`, graded **C-**) found three Criticals. This audit **confirms all three are genuinely fixed** in 0.3.0 — not papered over. So this report grades the *current* state and focuses on what remains.

---

## Executive Summary

**Overall health: B (≈B, up from C- one release ago).** This is a well-engineered solo-developer OSS framework that just completed a serious remediation pass. The three Criticals from the last audit are fixed and I verified the hardest one by reproduction: `init → complete → check` now persists state across separate processes and unblocks correctly (`pipeline_gate`). The DQ gate now actually executes SQL against Iceberg (`run.py:404-448`), the contract generate/verify split-brain is closed (dual-write), CI exists, and the untrusted MCP SQL surface is genuinely locked down (allowlist + `enable_external_access=false`). Test discipline is strong: 590 tests, ~9.9k test LOC against ~15.3k source LOC, and several *guard* tests that encode invariants (no-swallowed-exceptions, contract round-trip, cross-process gate).

**No open Critical findings.** What remains is medium-grade consolidation debt and one High.

**Top 3 risks:**

1. **(High) Agent definitions are forked into two directories that have drifted 1,087 lines apart.** `agents/` (what the plugin ships) is *behind* `.claude/agents/` (dev copy) — the shipped agents are missing the "Governance Database Logging" instructions the Brightforge UI depends on. Edit-one-forget-the-other is now a certainty.
2. **(Medium) Residual "swallow exception → return empty/None" pattern in 76 sites**, only 6 of which are guarded by the no-swallow test. For a *governance* product, a silent read failure that looks like "no data" is the highest-consequence smell.
3. **(Medium) No type checking and a near-default lint config** despite pervasive type hints — `pyproject.toml` sets only `line-length`; ruff runs bare `F`/`E` rules; no mypy/pyright. Type holes and unsorted/foot-gun imports pass CI.

**Top 3 opportunities:**

1. Collapse the agent duplication to one source of truth (symlink or build step) — kills a whole class of future drift bugs for ~S effort.
2. Turn on `ruff lint.select` (add `B`, `I`, `UP`, `SIM`) and add `pyright` to CI — mechanical, high-leverage, catches regressions the last audit had to find by hand.
3. Two 250–384-line god-functions (`sync_from_files`, `migrate_files_to_iceberg`) concentrate most of the remaining complexity; decomposing them makes the governance layer far easier to evolve.

---

## Repo Map

**Purpose:** Domain-agnostic AI-agent data pipeline framework. Ingests raw data (Bronze) → normalizes (Silver) → builds data products (Gold) → serves them to LLMs via MCP, emitting governance artifacts (DQ rules, contracts, lineage, approvals, CAB decisions) at every step. Distributed **both** as a pip/hatchling package and as a Claude Code plugin; orchestration is 25 markdown agent personas gated by a Python state machine.

**Maturity:** Pre-1.0 solo OSS, extracted from a production SEC-EDGAR pipeline, one downstream consumer. Now has CI, a CHANGELOG, LICENSE, and a security section — the trappings of a project preparing for open-source release.

**Stack:** Python 3.11+ · DuckDB + Iceberg extension · PyIceberg (SQLite catalog) · PyArrow · MCP SDK · uv/hatchling · pytest · ruff.

**Architecture:** Two execution models over one Iceberg storage layer — (a) AI-agent-driven via Claude Code skills/hooks calling CLI modules, and (b) headless via `brightsmith.run`. Governance state is Iceberg-authoritative with file dual-writes for human review. Config resolves at *call time* via `get_config()` (`config.py`), a clean fix for the old import-time-freeze problem.

| Path | What it is |
|---|---|
| `src/brightsmith/infra/` (7.2k LOC) | Cross-cutting engines: `pipeline_gate` (1174), `cab` (1005), `lineage` (846), `contract` (838), `dq_runner` (815), `relocate` (436) |
| `src/brightsmith/infra/governance/` (3.4k LOC) | Iceberg governance DB, split (WP-2.3) into `writers`/`queries`/`schemas`/`sync`/`cli`/`exporters`; `product.py` is a re-export façade |
| `src/brightsmith/{bronze,silver,mcp}/` | Zone base classes: `BaseIngestor`, concept normalization, `BaseMCPServer` |
| `run.py` / `serve.py` / `setup.py` | Headless runner, MCP entry, scaffolder |
| `agents/` **and** `.claude/agents/` | **Two** 25-file agent sets (drifted — see A1) |
| `skills/` (9), `hooks/` | Plugin surface; `require-subagent-type.sh` enforces named agents |
| `docs/specs/` (24), `docs/workflows/` (5) | Spec-driven corpus |

**Surprises:** (1) `agents/` and `.claude/agents/` both exist and differ by 1,087 lines. (2) `.claude-plugin/plugin.json` still says `0.2.0` while `pyproject.toml` says `0.3.0`. (3) `governance/reviews/` and `governance/pipeline-state/brightgemma-deepagents-runtime-pipeline.json` are committed dev artifacts from the framework's *own* development, shipping inside the framework repo.

---

## Audit Report

*Findings are facts with `file:line` evidence unless marked **[judgment]**.*

### ✅ Verified remediations (was Critical, now fixed)

- **Pipeline gate persists across processes.** Reproduced live: `init` writes `test-spec-pipeline.json`, `complete governance-reviewer-pre` records it, and a fresh-process `check primary-agent` returns `CLEAR`. (`pipeline_gate.py`)
- **Headless DQ gate executes real SQL.** `_run_dq_for_zone` calls `run_rules()` against Iceberg, honors `ZONE_ALIASES`, and propagates `EXIT_DQ_FAILURE` (`run.py:404-448`). The old `# count rules as passed` is gone.
- **Contract generate→verify round-trips** via YAML dual-write; **errored P0 rules now block**; **MCP `query_iceberg` is read-only** (`base_mcp_server.py:65-114, 521-532`).

### Architecture & Design

- **A1 — HIGH: Agent definitions forked and drifted.** `agents/*.md` (plugin-shipped) vs `.claude/agents/*.md` (dev) differ by **1,087 lines across 25 files**; `.claude/agents/` carries "Governance Database Logging" blocks (e.g. `data-analyst.md:124-142`) that the shipped `agents/` lacks. *Consequence:* plugin installs run agents that never log to the governance DB the Brightforge UI reads, while repo-local dev sessions do — behavior depends on invocation path, and any future edit must be made twice.
- **A2 — MEDIUM [judgment]: Governance layer has two god-functions.** `sync.py:35 sync_from_files()` is **384 lines**; `migration.py:25 migrate_files_to_iceberg()` is **258**. These concentrate branching and are the hardest code to modify safely. Everything else is reasonably sized (next largest real function ~176 lines).
- **A3 — LOW: Zone/table routing leans on SQL-regex parsing.** `_rule_matches_zone`/`_extract_table_refs` (`run.py:451-470`, `dq_runner`) infer zones by regex-scanning rule SQL for table refs. Fragile against unusual SQL, but rules are trusted committed input, so blast radius is small.

### Code Quality

- **Q1 — MEDIUM: 76 `except Exception` handlers; only 6 guarded.** `test_no_swallowed_exceptions.py` rigorously guards `run.py`, `dq_runner`, `pipeline_gate`, `base_ingestor`, and `queries._query_table` — excellent. But the pattern persists unguarded in `lineage.py:271,303,339` (`return []`), `cab.py:336`, `contract.py:315`, `base_mcp_server.py` (7 sites). Most are observability reads that log with `exc_info=True` (defensible), but nothing stops a *new* enforcement-adjacent swallow in the ~11 unguarded modules.
- **Q2 — LOW: Zero `TODO/FIXME/placeholder/simplified` markers in `src`.** Genuinely clean — the placeholder-DQ smell that sank the last audit is gone.
- **Q3 — LOW: `config.py` module-class swap is clever but subtle.** The `_ConfigModule.__getattr__/__setattr__` shim (`config.py:207-223`) is correct and well-commented, but it's the kind of metaprogramming a new contributor will misread. Slated for removal in 0.4.0 per CHANGELOG — fine.

### Security

- **Healthy.** No hardcoded secrets, no `subprocess`/`eval`/`exec`/`pickle`/`yaml.load` in `src`. The one untrusted surface (MCP `query_iceberg`) has a defense-in-depth allowlist + external-access lock with a documented trust boundary (`base_mcp_server.py:154-183`). f-string SQL exists only on trusted inputs (`metadata_location` from the catalog, committed rule SQL). **S1 — LOW:** the multi-statement check rejects semicolons inside string literals conservatively — correct choice, noted only so it isn't "fixed" into a vulnerability later.

### Testing

- **Strong.** 590 tests, behavior-asserting (subprocess-level gate tests, round-trip contract tests, AST-based invariant guards). **T1 — MEDIUM:** no coverage measurement in CI, so gaps are invisible. **T2 — LOW [judgment]:** the largest suite `test_governance_db.py` (1,329 lines) tests the façade heavily while the 384-line `sync_from_files` is exercised mostly end-to-end — branch coverage there is unverified.

### Performance

- **Adequate for scale.** DQ reads use `iceberg_scan()` for predicate pushdown rather than full materialization (`dq_runner.py:262-276`) — good. No N+1 or unbounded-growth patterns found in the core paths. Not a concern at this maturity.

### Dependencies

- **Clean.** Tight, current deps (`duckdb>=1.0`, `pyiceberg>=0.7`, `mcp>=1.0`); `uv.lock` present; dev group isolated. No heavy/unmaintained packages. `pytz` pinned to a future-dated post-release (`2026.1.post1`) — cosmetic.

### DevEx & Operations

- **O1 — MEDIUM: Lint config is near-default; no type checker.** `pyproject.toml:31-33` sets only `target-version`/`line-length`; ruff runs bare `F`/`E`. No `lint.select` (missing `B` bugbear, `I` import-sort, `UP`, `SIM`), no mypy/pyright despite `from __future__ import annotations` everywhere. CI (`ci.yml`) runs ruff+pytest+`relocate --check` on 3.11/3.12 — a solid baseline, but type regressions pass.
- **O2 — LOW: `dist/` wheels present locally but correctly gitignored** (verified not tracked). Good hygiene.

### Documentation

- **D1 — MEDIUM: Version drift.** `.claude-plugin/plugin.json:3` = `0.2.0` vs `pyproject.toml:3` = `0.3.0`. A plugin user sees the wrong version.
- **D2 — LOW: Stale `docs/sessions/` mandate.** The dir was deleted, README no longer requires it, but 5 specs still reference it (`infra-framework-hardening.md:689`, `iceberg-authoritative-…:59,131`, etc.). The prior audit's Open Question #4 was never resolved.
- **D3 — LOW: Committed dev artifacts ship in the framework.** `governance/reviews/*` and `governance/pipeline-state/brightgemma-…json` are the framework's *own* development records, committed into the distributable repo.

### Strengths (preserve these)

- Deterministic grain hashing + idempotent promote; real chaos-testing gate; **guard tests that encode invariants** (the no-swallow AST test is exemplary).
- Honest, high-quality remediation: the CHANGELOG documents breaking changes precisely, and the fixes are real (reproduced), not theatrical.
- Clear trust-boundary documentation on the one security-sensitive surface.
- Fast, behavior-asserting test suite with a strong LOC ratio.

---

## Improvement Strategy

**Theme 1 — One source of truth for duplicated assets.** Agent defs (two dirs), version strings (two files), and governance dev-artifacts all violate DRY across the repo boundary. *Target:* each fact lives once; distribution copies are generated, not hand-maintained. *Principle:* single source of truth.

**Theme 2 — Make the machine enforce what humans now catch.** The last audit found regressions by hand that a linter/type-checker/coverage gate would have caught. *Target:* `ruff lint.select` expanded, `pyright` in CI, coverage reported (not necessarily gated). *Principle:* shift-left; CI is the auditor that never sleeps.

**Theme 3 — Finish the "loud failure" doctrine.** The no-swallow guard is right but covers 6 of 76 sites. *Target:* every governance *read* either narrows its exception or is explicitly on a justified allowlist. *Principle:* in a governance product, silent ≠ safe.

**Theme 4 — Decompose the two god-functions.** *Target:* `sync_from_files`/`migrate_files_to_iceberg` broken into named, independently testable steps. *Principle:* functions should fit on a screen and a test.

**Explicitly NOT recommending (and why):**

- No RLS/entitlements on MCP — correctly deferred to non-localhost deployment (decision D5); premature at this maturity.
- No architectural rewrite — the architecture is sound; this is consolidation, not redesign.
- No coverage *gate* (only reporting) — a hard threshold on a solo project invites gaming; visibility is enough for now.

**Definition of done:** zero Critical/High findings; agent defs single-sourced; `pyright` + expanded `ruff` green in CI; version strings consistent; no-swallow guard extended to all governance-read modules; both god-functions <120 lines with unit tests.

---

## Task Plan

### Quick wins (do immediately — high impact, S effort)

- **QW1:** Sync `.claude-plugin/plugin.json` version to `0.3.0` (D1). *~5 min.*
- **QW2:** Add `[tool.ruff.lint] select = ["E","F","B","I","UP","SIM"]` and fix fallout (O1). *S.*
- **QW3:** Delete or `.gitignore` `governance/reviews/*` and the `brightgemma` state file; decide the `docs/sessions/` mandate (drop it from the 5 specs) (D2, D3). *S.*

### Milestone 0 — Safety net

| ID | Title | Files | Acceptance | Effort | Risk | Deps |
|----|-------|-------|-----------|--------|------|------|
| M0.1 | Coverage reporting in CI | `ci.yml`, `pyproject.toml` | `pytest --cov` runs, report visible in CI logs | S | Low | — |
| M0.2 | Add pyright to CI (non-blocking first) | `ci.yml`, new `pyrightconfig` | pyright runs on 3.11; baseline recorded | M | Low | — |

### Milestone 1 — Critical fixes

*None — no open Critical/High-severity correctness bugs. (A1 is High but is consolidation, handled in M2.)*

### Milestone 2 — High-leverage

| ID | Title | Files | Acceptance | Effort | Risk | Deps |
|----|-------|-------|-----------|--------|------|------|
| M2.1 | Single-source agent defs | `agents/`, `.claude/agents/`, build/pkg step | One canonical dir; the other generated (symlink or hatch build hook); `diff` is empty; plugin ships governance-logging blocks | M | Med (plugin packaging) | QW3 |
| M2.2 | Expand ruff + fix, gate pyright | `pyproject.toml`, `ci.yml`, broad | `ruff`+`pyright` block CI; both green | M | Med | QW2, M0.2 |
| M2.3 | Extend no-swallow guard to governance-read modules | `test_no_swallowed_exceptions.py`, `lineage.py`, `queries.py`, `cab.py` | Guard covers `lineage`/`queries`/`cab`; each broad handler narrowed or allowlisted with justification | M | Low | — |

### Milestone 3 — Quality & polish

| ID | Title | Files | Acceptance | Effort | Risk | Deps |
|----|-------|-------|-----------|--------|------|------|
| M3.1 | Decompose `sync_from_files` | `governance/sync.py` | ≤120-line orchestrator + named helpers; new unit tests per helper | L | Med | M0.1 |
| M3.2 | Decompose `migrate_files_to_iceberg` | `governance/migration.py` | Same | M | Med | M0.1 |
| M3.3 | Document `governance/` package in CLAUDE.md Key Paths | `CLAUDE.md` | Package + `sync/migration` documented | S | Low | — |

### Top-3 implementation sketches

**M2.1 — Single-source agent defs.** *Approach:* make `agents/` canonical (it's the plugin-standard location); replace `.claude/agents/` with either symlinks or a `hatch`/Make step that copies on build. *Steps:* (1) 3-way reconcile the 1,087-line drift — `.claude/agents/` is ahead, so port its governance-logging blocks into `agents/`; (2) delete `.claude/agents/`, symlink it to `../agents` (works for local Claude Code); (3) add a CI check asserting `diff -r` is empty if you keep both. *Gotcha:* confirm Claude Code follows symlinks for project agents; if not, generate via a pre-commit/build hook instead.

**M2.3 — Extend no-swallow guard.** *Approach:* add `lineage.py`, `cab.py`, and all of `queries.py` to `FULL_SCOPE_FILES`. *Steps:* (1) run the test, get the violation list; (2) for each: narrow to `(OSError, duckdb.Error, …)` where the failure mode is known, else allowlist with a written justification; (3) keep the existing `test_allowlist_entries_are_real` so the allowlist can't rot. *Gotcha:* lineage reads are legitimately fault-tolerant (observability) — those belong on the allowlist, not narrowed to raise; don't turn observability into a correctness gate.

**M2.2 — Expand ruff + gate pyright.** *Approach:* enable `B,I,UP,SIM` incrementally. *Steps:* (1) `ruff check --fix` for auto-fixable (`I`, `UP`); (2) triage `B`/`SIM` by hand; (3) add pyright, fix the real type holes the metaprogramming in `config.py` will surface, `# type: ignore` the intentional shim with a comment. *Gotcha:* `config.py`'s module-class swap and `**dataclasses.replace` will produce pyright noise — annotate deliberately rather than loosening the whole config.

---

## Open Questions (need a human)

1. **Agent-dir authority:** is `.claude/agents/` intentionally the "enhanced dev" copy, or did the plugin `agents/` simply fall behind? This decides whether M2.1 is "port forward then symlink" or "the dev copy is experimental, revert it."
2. **`docs/sessions/` doctrine:** drop the session-log mandate entirely, or redirect it into the governance `documents` table? Five specs still assume it exists.
3. **Coverage target:** do you want any numeric floor for the core `infra/` modules, or is reporting-only sufficient pre-1.0?
4. **0.4.0 breaking removals:** the `GRIST_*` vars and module-level config globals are deprecation-warned for 0.4.0 removal — is 0.4.0 the next release, i.e. should M2.2's type work assume the shim disappears soon?

**Lighter-review areas (flagged for honesty):** chaos-monkey corruptors, `period_disambiguator`, `concept_normalization`, the CAB blast-radius logic, and the 24 specs got structural rather than line-by-line review. The two god-functions (M3.1/M3.2) were read for size/shape but not fully traced — decomposition should be paired with characterization tests before refactoring.
