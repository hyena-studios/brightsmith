# Brightsmith — Technical Audit & Improvement Plan

*Independent audit, 2026-07-02, on branch `audit-remediation` @ `cf8d492`. Prior audit documents in `docs/` were deliberately not read; all conclusions were derived from source, tests, and empirical verification. All 632 tests pass (83s, Python 3.14 locally; CI matrix is 3.11/3.12). Ruff and pyright are clean.*

---

## Executive Summary

**Overall health: B−.** This is a disciplined, well-engineered codebase for its maturity (v0.4.0, single maintainer, pre-open-source): 632 tests pass in 84s, ruff and pyright gates are green and blocking in CI, exception hygiene is enforced by meta-tests, and idempotency is designed-in rather than bolted on. What pulls the grade down is concentrated in one seam: **the consumer boundary is field-tested but the field findings never came home.** A real domain project (`~/code/bright/futureproof-data`) exercised the framework end-to-end — and hit this exact seam: its own docstrings document two framework blockers that forced a bypass launcher for `/bs:serve`, a from-scratch query engine replacing the base MCP helpers, and overrides of both base query methods. Those workarounds and bug reports accumulated in the consumer repo; nothing flowed back into the framework's code or test suite, which still contains no test that installs the package and drives the documented consumer journey.

**Top 3 risks:**

1. `BaseMCPServer.query_iceberg`, the documented untrusted-SQL choke point of the flagship MCP zone, **cannot read any real Iceberg table** — verified empirically, masked by tests that only run `SELECT 1`. The one field consumer never exposed this because it overrides both base query methods.
2. The governance enforcement core (`pipeline_gate.py`, 1,204 lines) has **25% test coverage** while being the thing the whole product promises.
3. The serve/scaffold consumer path is broken in at least five places — two confirmed in the field by futureproof-data's "KNOWN BLOCKER" bypass script (`tables:` multi-table sources crash the loader; the nested pipeline manifest shape isn't parsed), plus the never-parsed `mcp:` manifest key, DQ templates that can't ship in the wheel (the consumer's templates directory is empty), and a stale scaffold warehouse path.

**Top 3 opportunities:**

1. An end-to-end consumer-journey test in CI would have caught every Critical/High finding here.
2. Standardizing reads on the `iceberg_scan` pushdown path (which `dq_runner` already uses) removes the memory ceiling.
3. A half-day of packaging/README hygiene makes the open-source launch story credible.

---

## Phase 1 — Repo Map

**Purpose.** A domain-agnostic AI-agent data pipeline framework: raw data → Bronze → Silver → Gold → MCP zones on Apache Iceberg (local SQLite catalog, DuckDB reads), with governance artifacts (DQ rules, contracts, lineage, glossary, approval gates) produced at every step. Distributed two ways: as a **pip-installable Python package** (`src/brightsmith/`, ~15.7k lines) and as a **Claude Code plugin** (`agents/` — 25 agent personas, `skills/` — 9 slash commands, `hooks/`) that orchestrates those agents through a spec-driven workflow.

**Stack.** Python 3.11+ (CI: 3.11/3.12; local venv is 3.14 and passes), DuckDB ≥1.0, PyIceberg ≥0.7, PyArrow, MCP SDK, uv + hatchling. Dependencies are few and current; `uv.lock` present.

**Architecture sketch.** Domain packs supply `domain/manifest.yaml` + a `BaseIngestor` subclass. Writes go through PyIceberg (`infra/iceberg_setup.py`); reads go through DuckDB via Arrow or `iceberg_scan()`. Cross-cutting `infra/` carries the real weight: `pipeline_gate.py` (per-spec state machine gating agent steps), `dq_runner.py` (SQL rules → thresholds → P0 gates), `promote.py` + `grain.py` (idempotent appends via SHA-256 grain IDs), `contract.py`, `lineage.py`, `cab.py`, `relocate.py` (repairs baked absolute Iceberg paths), and `infra/governance/` (an Iceberg-backed governance database, cleanly split into schemas/writers/queries/sync/migration). `run.py` is the headless (agent-free) orchestrator; `serve.py`/`mcp/base_mcp_server.py` are the MCP zone deliverable.

**Key directories.**

| Path | What it is |
|---|---|
| `src/brightsmith/infra/` | ~70% of the code; governance + Iceberg infrastructure |
| `src/brightsmith/infra/governance/` | Iceberg governance DB (well-factored package, façade at `governance_db.py`) |
| `src/brightsmith/{bronze,silver,mcp}/` | Zone base classes (`BaseIngestor`, concept normalization, `BaseMCPServer`) |
| `agents/`, `skills/`, `hooks/` | Claude Code plugin surface (25 agents, 9 skills, 2 hooks) |
| `tests/` (~10.9k lines, 632 tests) | Includes meta-tests enforcing no-swallowed-exceptions and no-leaked-connections |
| `docs/specs/` (25 specs), `docs/workflows/` | Spec-driven development record |
| `governance/dq-rule-templates/` | The only shipped governance content; rest is runtime output (gitignored) |

**Surprises.** (1) The engineering culture is unusually explicit — comments cite decision IDs ("decision D3", "audit finding A3"), the CHANGELOG is genuinely excellent, and CI runs a warehouse-relocation guard. (2) `config.py` swaps the module's class at import time (`config.py:281`) to make `UPPER_CASE` names live views — clever, documented, and a maintenance hazard in equal measure. (3) The repo contains three prior self-audits; the remediation work they drove is visible and real (connection-leak meta-test, loud-failure gates). (4) Despite all this rigor, **no test anywhere installs the wheel or drives the pipeline as a consumer would** — and the one real consumer project (`futureproof-data`) documents framework blockers and workarounds in its own repo that were never fed back upstream. That seam is exactly where the serious findings live.

---

## Phase 2 — Audit Report

### Critical

**C1. The untrusted MCP SQL surface cannot query any real Iceberg table.** *(fact, empirically verified)*

`BaseMCPServer.query_iceberg` sets `SET enable_external_access=false` (`src/brightsmith/mcp/base_mcp_server.py:544`) **before** registering table views with `CREATE VIEW … AS SELECT * FROM iceberg_scan('<metadata_path>')` (`:556-559`). DuckDB's external-access lock blocks `iceberg_scan`'s file reads, so **every view registration fails** (swallowed into a per-table warning at `:560-564`), and any query against a real table returns `Table … does not exist`. Verified empirically: creating an Iceberg table through the project's own `append_data` and calling `query_iceberg("SELECT * FROM gold_verify_tbl")` errors, while `query_iceberg_simple` on the same table returns rows. The security tests (`tests/mcp/test_base_mcp_server.py:247-258`) only ever run `SELECT 1`, so the suite passes.

**Why it matters:** this method is documented as "the single choke point for MCP-client data access" (`base_mcp_server.py:509-527`) and is the API domain packs are told to build SQL tools on. It is dead on arrival, and the gap is masked by exactly the "test theater" the project's own CLAUDE.md names as a rejection offense.

**Field evidence:** the one real consumer (futureproof-data) never exposed this in production because it **overrides both** `query_iceberg` and `query_iceberg_simple` in its subclass (`futureproof-data/src/mcp_server/futureproof_server.py:821,844`) with a custom persistent-connection engine — the broken base implementation was routed around, not fixed.

### High

**H1. Domain MCP server loading is unreachable code.** *(fact)*

`serve.py:39` reads `getattr(manifest, "mcp", None)`, but `DomainManifest` (`src/brightsmith/domain_loader.py:100-110`) has no `mcp` field and `load_manifest` never parses an `mcp` key (only `pipeline`, `:215`). `manifest.yaml.example` has no `mcp` section either. Result: a consumer who configures a domain MCP server per the docstring gets the generic base server, **silently** — the fallback warning at `serve.py:52-56` doesn't fire because no exception is raised. The flagship deliverable ("tool-use chat agent" per README) can never use domain-specific tools via the documented entry point.

**H2. Domain transform failures can be misclassified as SKIPPED.** *(fact)*

In `run.py:344-349`, `except ValueError` after `_execute_zone_module(zone)` is meant to catch "no module registered" (`run.py:220`), but it equally catches any `ValueError` raised by the domain's own transform code — including `append_data`'s strict-mode misspelled-column error (`iceberg_setup.py:292`) and `compute_grain_id`'s missing-grain-field error (`grain.py:44`), both of which the framework deliberately raises loudly. The zone is marked `SKIPPED`, the pipeline continues, and the run can exit 0. This directly undermines the loud-failure doctrine the rest of the file works hard to uphold.

**H3. The governance enforcement core is the least-tested code in the repo.** *(fact + judgment)*

`pipeline_gate.py` — 1,204 lines, the state machine that CLAUDE.md makes mandatory before/after every agent step — has **25% coverage (379 of 507 statements missed)**; the validation paths (`validate`, `_validate_zone_specific`, `_validate_warehouse_population`, `check_zone_transition`, `audit_report`, lines 573–1014) are largely unexercised. Similarly `run.py` is at 44%, `contract.py` 57%, `governance/cli.py` 14%, `setup.py` and `serve.py` 0%. Overall coverage is 64%, but it is distributed inversely to criticality: the pure-logic utilities are at 95–100% while the orchestration and gates — the product's actual promise — are dark. *Judgment:* given that agents are instructed to trust `pipeline_gate check/validate` output, bugs here silently disable governance.

**H4. The pip-install consumer path is broken in three places.** *(facts)*

- `setup.py:106` locates DQ templates via `_TEMPLATES_DIR.parent.parent.parent / "governance" / "dq-rule-templates"` — that resolves to the repo root only in a source checkout. The wheel packages only `src/brightsmith` (`pyproject.toml:25-26`), so for the documented `pip install git+…` flow (`README.md:308`) the path doesn't exist and the copy **silently no-ops** (`if src_templates.exists():`, `setup.py:108`). The "mandatory patterns for gold zone" never reach pip-installed consumers.
- `setup.py:44` scaffolds `data/raw/iceberg_warehouse`, but the framework default is `data/bronze/iceberg_warehouse` (`config.py:156`) — a leftover from the medallion rename. Scaffolded projects get an empty decoy directory and the real warehouse appears elsewhere.
- `setup.py`'s docstring promises "CLAUDE.md, pyproject.toml, ingestor skeleton, governance directories, and first spec" (`setup.py:1-7`); it actually writes only directories, a pyproject, and a gitignore — `src/brightsmith/_templates/` contains nothing but `__init__.py`. The module has 0% test coverage.

**Field evidence:** `futureproof-data/governance/dq-rule-templates/` exists and is **empty** — the mandatory gold-zone templates never reached the real consumer project.

**H5. `python -m brightsmith.serve` cannot start against the real field project — two documented, unfixed framework blockers.** *(fact, documented by the consumer)*

`futureproof-data/scripts/serve_mcp.py:1-20` opens: *"Hackathon MCP server launcher — bypasses /bs:serve manifest loader… because of two Brightsmith bugs tracked in the mcp-futureproof-core spec (see the KNOWN BLOCKER section)"*:

1. **Multi-table sources crash the loader.** `domain_loader._load_source_config` hard-requires singular `table:` (`domain_loader.py:144`, `data["table"]` → `KeyError`), but multi-table sources (e.g., the consumer's `onet.yaml`) naturally use `tables:`.
2. **The nested pipeline manifest shape isn't parsed.** `_load_zone_registry` (`run.py:204-209`) expects flat `pipeline: {silver: {module: dotted.path}}`, but the field manifest — "the shape that multi-source domains produce naturally" — is nested `pipeline: zones: {silver: [step, step, …]}` with per-step file paths. `pipeline.items()` yields `("zones", {...})`, `zone_config.get("module")` returns nothing, and the registry stays silently empty. This also means the **headless runner (`python -m brightsmith.run`) cannot drive the field project at all** — same root cause.

The consumer worked around both by instantiating its server class directly. Neither bug has a corresponding fix, test, or spec in the framework repo. **Why it matters:** the documented serve and headless-run entry points fail against the only project that has ever really used them, and the knowledge lives only in the consumer's repo.

### Medium

**M1. Full-table materialization is the default read pattern.** *(fact)*

`read_with_duckdb` (`iceberg_setup.py:313-326`) scans the entire table to Arrow and converts to a Python list-of-dicts. Consumers: `query_iceberg_simple` loads the whole table then filters/limits in Python (`base_mcp_server.py:493-507`) — an MCP `query_table` call with `limit: 100` materializes every row; `verify_contract` does the same per contract (`contract.py`, `rows = read_with_duckdb(iceberg_table)` in the data-dependent checks); `BaseIngestor._build_existing_grains` reads **all columns of all rows** when it needs only the grain fields (`base_ingestor.py:119`). Meanwhile `dq_runner` already demonstrates the right pattern (`iceberg_scan` views with pushdown, `dq_runner.py:244-260`). For a framework advertising "any data source," this is the scalability ceiling.

**Field evidence:** futureproof-data hit this in production — `futureproof-data/src/mcp_server/_query_engine.py:1-10`: *"The brightsmith base helpers (query_iceberg_simple and query_iceberg) rebuild the DuckDB world on every call… That setup cost dominates the MCP request budget."* The consumer built a process-lifetime connection with cached views and predicate pushdown — the exact fix 2.2 proposes, already designed and battle-tested downstream, ready to be upstreamed.

**M2. Headless runs execute the full DQ rule set once per zone.** *(fact)*

`_run_dq_for_zone` calls `run_rules()` with no spec/zone filter (`run.py:432`) and then discards non-matching results. A 4-zone run executes every rule 4× and writes 4 governance run records. Correct but wasteful, and it skews `dq_runs` history.

**M3. DQ rule lifecycle is opt-out, not enforced.** *(fact)*

`load_rules` applies `rule.setdefault("status", "active")` (`dq_runner.py:74`). A rules file that simply omits `status` bypasses PROPOSED→APPROVED→ACTIVE entirely and executes immediately. The lifecycle CLAUDE.md mandates is only enforced for rules that opt into it.

**M4. SQL table-reference rewriting is fragile.** *(fact)*

`_TABLE_REF_RE = r"\b([a-z_]+)\.([a-z_]+)\b"` (`dq_runner.py:156`) won't match table names containing digits or uppercase (e.g., `gold.revenue_2024`) — the view is never registered and the rule errors. `_rewrite_sql` uses blind string `replace` (`:178-184`), which can rewrite matches inside string literals. Errors surface as rule failures (safe direction), but the failure mode is confusing.

**M5. Governance DB: one snapshot per event, full scan per query, no maintenance story.** *(fact + judgment)*

Every governance write is a single-record `promote()` → one Iceberg snapshot + tiny parquet file per event (`governance/queries.py:36-61`, `writers.py` throughout); every read scans the whole table to Arrow before filtering (`queries.py:97-108`). Append-only tables (`spec_registry`, `dq_rule_results`, `agent_activity`) grow monotonically with no compaction or snapshot expiration anywhere in the repo. Fine today; a chatty multi-month project will accumulate thousands of files and progressively slower governance reads.

**M6. `query_iceberg` re-registers every table on every call.** *(fact)*

Lines `base_mcp_server.py:546-566` enumerate all namespaces and `load_table` each table per query on a long-lived stdio server. Moot until C1 is fixed, but it should be fixed together with it.

**M7. Repository identity is inconsistent — RESOLVED as one repo, two names (2026-07-02).** *(fact, downgraded from the original finding)*

Verified via `gh api` and redirect check: there is exactly **one** GitHub repo (id `1182781960`, created 2026-03-16). It currently lives at `hyena-studios/brightsmith`; `jcernauske/brightsmith` is a **permanent 301 transfer redirect** to it (consistent with the owner's recollection of moving the repo to his personal account for hackathon judging, then back). Both metadata responses are identical to the second — same repo.

- **No divergence risk:** every reference (`README.md:308` pip URL, `setup.py`'s scaffolded dependency, futureproof-data's `pyproject.toml:7`, the git remote, the CI badge) resolves to the same repo via the redirect. Nobody installs stale code. The original worry ("canonical is behind") is void.
- **Residual risk:** the redirect is only safe until a *new* repo named `jcernauske/brightsmith` is created — at that moment every `jcernauske` reference (including the dependency line the scaffolder writes into every consumer project) silently points at the new repo. Relying on a transfer redirect in a distribution URL is a time bomb, not a bug.
- **Fix — DONE (2026-07-02):** owner confirmed `hyena-studios/brightsmith` as the permanent name. All references aligned: README badge + install line, `setup.py` scaffolded dependency, `agents/setup.md` scaffolding instructions, and the URL assertion in `tests/infra/test_setup.py`. The git remote and futureproof-data's dependency already pointed at the org. Nothing depends on the `jcernauske` redirect anymore.

**M8. SessionStart hook pip-installs into whatever Python is active.** *(fact + judgment)*

`hooks/hooks.json:8`: `pip show brightsmith > /dev/null 2>&1 || pip install -e ${CLAUDE_PLUGIN_ROOT}` — silent, unversioned, environment-blind (no uv/venv detection), and failure is swallowed. On a system Python this either errors invisibly (PEP 668) or pollutes the global environment.

### Low

- **L1.** f-string SQL with interpolated `job_name` in `lineage.py:419-425` (`WHERE job_name = '{job_name}'`) — local CLI so injection impact is negligible, but it breaks on quotes and is inconsistent with the parameterized style used in `governance/queries.py:103`.
- **L2.** README project structure still documents `.claude/agents/ — 25 agent definitions` (`README.md:414-415`); that directory was removed in commit `7de76bc` (agents consolidated to `agents/`).
- **L3.** `domain_loader.py:136-139` — comment says "Ensure entity keys are the right type"; the loop is a verbatim dict copy (dead code, misleading comment).
- **L4.** `.claude/skills/webapp-testing/SKILL.md` is committed (a local dev skill, presumably unintentional), and `.pytest_cache/`/`.ruff_cache/` are neither committed nor gitignored, leaving permanent `git status` noise.
- **L5.** `governance/run-history/` and DQ result files grow without rotation (gitignored, so low stakes).

### Strengths (preserve these)

- **Loud-failure doctrine, mechanically enforced.** `tests/infra/test_no_swallowed_exceptions.py` scans the whole `src` tree and fails CI on any unjustified `except Exception`; `test_no_unclosed_connections.py` asserts zero `ResourceWarning`s. Only 3 `except: pass` sites exist in ~15.7k lines, each with a written justification. This is rarer than it should be, even in professional codebases.
- **Idempotency as a first-class design.** Grain hashing with delimiter escaping (`grain.py:50-52`), missing-key hard errors (`grain.py:44`), in-batch + cross-batch dedup (`base_ingestor.py:194-209`, `iceberg_setup.py:345-355`), strict `append_data` catching misspelled columns (`iceberg_setup.py:288-297`).
- **`WarehouseRelocationError` / `relocate.py`** — turning Iceberg's silently-empty-reads-after-move failure mode into a loud error naming the exact repair command, guarded per-table, wired into CI. Genuinely good systems thinking.
- **CI is real:** ruff (expanded ruleset with reasoned ignores), blocking pyright, coverage, 2-version matrix, relocation guard.
- **`config.py` live-snapshot design** solves the import-time-config-freeze problem correctly, with the trade-offs documented in-file.
- **Documentation of intent** — CHANGELOG, 25 specs, decision IDs cited in code comments — is exemplary for a solo project.

*Lighter-review areas:* the 25 agent persona files and 9 skill markdown files (the plugin's "prompt code") were skimmed for structure only, not audited for prompt quality; `chaos_monkey/` internals, `glossary_*`, `staging.py`, `period_disambiguator.py`, and `cab.py` logic got a structural pass (their coverage is 66–100% and nothing alarming surfaced in outline).

---

## Phase 3 — Improvement Strategy

**Theme 1 — Field findings must flow back into the framework.** Every Critical/High finding (C1, H1, H2, H4, H5) lives at the seam where a consumer touches the product: an installed wheel, a scaffolded project, a manifest key, an MCP query against real data. The framework *was* field-tested — futureproof-data drove it end-to-end — but the resulting bug reports (`serve_mcp.py`'s KNOWN BLOCKER section) and fixes (`_query_engine.py`) stayed in the consumer repo. The framework's own tests still never play the user, so the same defects survived into this audit. *Target state:* (a) one CI job that builds the wheel, installs it in a clean venv, scaffolds a project, ingests fixture data through a stub `BaseIngestor`, runs `python -m brightsmith.run`, and queries the result through `BaseMCPServer` tools — the README Quick Start, executed; (b) a fixture manifest **modeled on futureproof-data's real shape** (nested pipeline, multi-table source), not the idealized example. *Principle:* the documented journey is the contract; the field project is the best available spec of that contract — test against it, and treat downstream workarounds as upstream bug reports.

**Theme 2 — The gates must be as trustworthy as they are loud.** The project's differentiator is enforcement (pipeline gate, DQ P0 gates, contracts), yet `pipeline_gate.py` is 25% covered and `run.py` misclassifies transform errors (H2). *Target state:* enforcement modules ≥ 70% coverage with tests asserting *blocking behavior* (BLOCKED stays blocked, failures never read as SKIPPED/PASS), not just happy paths. *Principle:* a gate that can silently fail open is worse than no gate — the repo's own D3 decision, applied to its own gatekeepers.

**Theme 3 — One read pattern, the scalable one.** Two read idioms coexist: `iceberg_scan` views with pushdown (dq_runner) and full-materialization `read_with_duckdb` (everything else, M1/M5/M6). *Target state:* `read_with_duckdb` grows `columns=`/`where=`/`limit=` parameters (or consumers move to a shared view-registration helper), and the hot paths (`_build_existing_grains`, `query_iceberg_simple`, `verify_contract`) stop loading whole tables. *Principle:* the framework shouldn't have a memory ceiling its own DQ engine already avoided.

**Theme 4 — Ship-readiness hygiene.** Repo identity (M7), wheel contents (H4a), scaffolder truthfulness (H4c), README staleness (L2), hook behavior (M8). All small, all embarrassing at open-source launch, all cheap.

**Deliberately not fixing:** the `config.py` module-class shim (removal already consciously deferred to a major release — agreed; it's tested and documented); governance-table compaction/snapshot expiration (M5 — document the limitation, defer implementation until a real project feels it); agent/skill prompt quality (different discipline, different review); `_rewrite_sql` robustness beyond the digit fix (a real SQL parser is not worth it at this maturity); Python 3.13/3.14 CI lanes (nice-to-have; suite already passes on 3.14 locally).

**Definition of done:**

1. Consumer-journey CI job green, including a `query_iceberg` assertion returning rows from a real Iceberg table.
2. `pipeline_gate.py` + `run.py` ≥ 70% coverage.
3. Zero Critical/High findings open.
4. Scaffolded project's `uv sync && python -m brightsmith.run --dry-run` succeeds from a wheel install.
5. README contains no references to removed paths or wrong repos.

---

## Phase 4 — Task Plan

### Milestone 0 — Safety net

| # | Task | Files | Acceptance | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 0.1 | **End-to-end consumer-journey test** (fixture ingestor → run.py → MCP query, in-repo first; wheel-install variant in 2.1) | `tests/integration/` | Test fails today on C1; green after M1 fixes | **L** | Low (test-only) | — |
| 0.2 | **Characterization tests for `pipeline_gate` validation paths** (validate, zone transition, warehouse population, skip justification) | `tests/infra/test_pipeline_gate.py` | Gate coverage ≥ 60% before any refactor | **L** | Low | — |

### Milestone 1 — Critical & correctness fixes

| # | Task | Files | Acceptance | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 1.1 | **Fix `query_iceberg` so real tables are queryable while staying read-only** (see sketch A) | `mcp/base_mcp_server.py`, tests | 0.1's MCP assertion passes; `read_csv('/etc/hosts')` and `COPY TO` still blocked | **M** | Medium — security-sensitive ordering | 0.1 |
| 1.2 | **Fix SKIPPED misclassification** — raise a dedicated `ZoneNotRegisteredError` from `_execute_zone_module` and catch only that (see sketch B) | `run.py:212-230, 344-349` | A transform raising `ValueError` yields `FAILED`/exit 2; unregistered zone still `SKIPPED` | **S** | Low | — |
| 1.3 | **Parse `mcp:` from the manifest and wire `serve.py`** — add field to `DomainManifest`, parse in `load_manifest`, document in `manifest.yaml.example` | `domain_loader.py`, `serve.py`, example | Test: manifest with `mcp:` block loads the named class; missing block → base server | **S** | Low | — |
| 1.4 | **Ship DQ templates inside the package** — move `governance/dq-rule-templates/*.json` into `src/brightsmith/_templates/` (or `importlib.resources`), make `_copy_dq_templates` fail loudly if missing | `setup.py`, `_templates/`, `pyproject.toml` | Wheel-installed `python -m brightsmith.setup init` produces templates | **S** | Low | — |
| 1.5 | **Fix scaffold warehouse path** `data/raw/…` → `data/bronze/…`; make `setup.py` docstring match reality (or generate the promised manifest/CLAUDE.md skeletons); add tests | `setup.py` | Scaffolded layout matches `config.py:156-158`; setup.py coverage > 0 | **S–M** | Low | — |
| 1.6 | **Support `tables:` (multi-table) sources** (H5.1) — accept both singular `table:` and plural `tables:` in source YAML; fixture copied from futureproof-data's `onet.yaml` | `domain_loader.py:141-152`, tests | `onet.yaml`-shaped source loads; singular form unchanged | **S–M** | Low | — |
| 1.7 | **Parse the nested pipeline manifest shape** (H5.2) — `_load_zone_registry` handles `pipeline.zones.{zone}: [steps]` with file-path modules (as the field manifest uses) in addition to the flat dotted-path form; fail loudly on unrecognized shapes instead of leaving the registry silently empty | `run.py:183-230`, `domain_loader.py`, tests | futureproof-data's `manifest.yaml` (copied as fixture) registers all zones; `--headless-ready` reports the truth | **M** | Medium — manifest contract change | 1.6 |

### Milestone 2 — High-leverage improvements

| # | Task | Files | Acceptance | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 2.1 | **Wheel-install CI job** — build wheel, install in clean venv, scaffold, run pipeline + MCP smoke (see sketch C) | `.github/workflows/ci.yml`, `tests/` | CI job green; fails if package data or entry points regress | **M** | Low | 1.4, 1.5 |
| 2.2 | **Push filters/columns/limit into reads** — extend `read_with_duckdb`; fix `_build_existing_grains` (grain columns only), `query_iceberg_simple` (SQL-side filter+limit), `verify_contract`. **Upstream futureproof-data's `_query_engine.py`** (persistent connection, cached views, identifier validation, thread-safety lock) as the base implementation rather than designing from scratch | `iceberg_setup.py`, `base_ingestor.py`, `base_mcp_server.py`, `contract.py` | Grain build selects only grain fields; MCP `query_table` never materializes > limit× small factor; consumer can delete its override | **M–L** | Medium — touches core read path; suite + 0.1 protect | 0.1 |
| 2.3 | **Run DQ once per pipeline run** — execute `run_rules()` once, partition results by zone | `run.py:404-448` | One `dq_runs` record per headless run; per-zone gating unchanged | **S** | Low | 0.2 |
| 2.4 | **Enforce DQ rule lifecycle default** — default missing `status` to `proposed` (honoring `REQUIRE_HUMAN_APPROVAL=False` auto-advance), or fail loudly on missing status | `dq_runner.py:74`, tests | Status-less rule does not execute when approval required | **S** | **Medium — behavior change**; migration note needed | — |
| 2.5 | **Fix table-ref regex** to allow digits (`[a-z][a-z0-9_]*`), add tests for numeric table names | `dq_runner.py:156` | `gold.revenue_2024` resolves | **S** | Low | — |

### Milestone 3 — Quality & polish

| # | Task | Files | Acceptance | Effort |
|---|---|---|---|---|
| 3.1 | **Repo identity — DONE:** one repo, two names; `jcernauske` is a transfer redirect. Owner chose `hyena-studios/brightsmith` as permanent; all references (badge, README install line, `setup.py` dependency, `agents/setup.md`, test assertion) aligned to the org URL — nothing relies on the 301 | `README.md`, `setup.py`, `agents/setup.md`, tests | ✅ Complete 2026-07-02 | **S** |
| 3.2 | Parameterize `lineage.py` CLI SQL (`:419`) | `lineage.py` | Matches queries.py style | **S** |
| 3.3 | Harden SessionStart hook: detect uv/venv, surface failure, or replace with an instructive message | `hooks/hooks.json` | No silent global-env installs | **S** |
| 3.4 | Repo hygiene: gitignore `.pytest_cache/`/`.ruff_cache/`, remove committed `.claude/skills/webapp-testing`, delete dead loop `domain_loader.py:136-139`, remove `.claude/agents` reference from README | misc | Clean `git status` on fresh clone | **S** |
| 3.5 | Document governance-DB growth characteristics + recommended maintenance (snapshot expiration cadence) in README/workflow doc; defer implementation | docs | Limitation stated | **S** |
| 3.6 | Raise gate/runner coverage to ≥ 70%, then consider splitting `pipeline_gate.py` (step definitions / state machine / validation / CLI) | `pipeline_gate.py`, tests | Coverage target met; no behavior change | **L** |

**Quick wins (do immediately, ~half a day total):** 1.2, 1.3, 1.4, 1.5, 2.5, 3.1, 3.2, 3.4 — all S-effort, and 1.2–1.5 close two High findings outright.

### Implementation sketches — top 3

**A. Fix `query_iceberg` (1.1).** The root conflict: `iceberg_scan` needs filesystem access at *query* time (views are lazy), but the lock is connection-wide. Two viable designs:

- **(preferred) Scoped allow-list:** keep `enable_external_access=false` but set DuckDB's `allowed_directories`/`allowed_paths` to the resolved warehouse root(s) before locking, then `SET lock_configuration=true` so the untrusted statement cannot flip settings back (defense-in-depth on top of the keyword allowlist, which already rejects `SET`). Verify the exact setting names against the pinned DuckDB 1.5 — this is the gotcha; the capability landed in the 1.2–1.4 window and names shifted.
- **(fallback) Materialize-then-lock:** with external access still enabled, `CREATE TEMP TABLE {view} AS SELECT * FROM iceberg_scan(…)` for referenced tables only (parse refs from the SQL rather than registering the whole catalog — also resolves M6), then `SET enable_external_access=false; SET lock_configuration=true;` and execute. Costs memory (bounded by referenced tables), guarantees isolation.

Either way: extend the security tests to assert **rows come back from a real Iceberg table** and that `read_csv` is still blocked *after* a successful table query on the same connection.

**B. Fix SKIPPED misclassification (1.2).** Define `class ZoneNotRegisteredError(Exception)` in `run.py`; raise it at `run.py:220` instead of `ValueError`; change the handler at `:344` to catch only it. Gotcha: check `tests/infra/test_pipeline_runner.py` for tests that rely on `ValueError → SKIPPED`; update them to use the new type. Add one regression test: registered zone whose function raises `ValueError` → `FAILED`, `exit_code == EXIT_TRANSFORM_ERROR`.

**C. Wheel-install CI job (2.1).** New CI job: `uv build`, create scratch venv, `pip install dist/*.whl`, `python -m brightsmith.setup init --name smoke --output $TMP/smoke`, `cd` there, drop a 20-line fixture ingestor + manifest (committed under `tests/fixtures/consumer/`), run `python -m brightsmith.run --zone bronze` and assert exit 0 + rows in the warehouse, then a 5-line script that instantiates `BaseMCPServer` and asserts `query_iceberg` returns rows. Gotchas: the job must run *outside* the repo checkout directory (that's the point — it catches `_TEMPLATES_DIR.parent.parent.parent`-style escapes); keep it off the 3.11/3.12 matrix (one lane is enough); expect it to be red until Milestone 1 lands — wire it as `continue-on-error: true` initially, flip to blocking when green.

---

## Open Questions

1. ~~Which repo is canonical for consumers?~~ **Fully resolved 2026-07-02.** First answer ("jcernauske is canonical") rested on a two-repo premise; verification showed there is only ONE repo — it lives at `hyena-studios/brightsmith`, and `jcernauske/brightsmith` is a permanent transfer redirect from a temporary hackathon-judging move. Owner confirmed `hyena-studios/brightsmith` as the permanent canonical name; all references aligned (task 3.1 complete).
2. **Is `python -m brightsmith.setup` meant to be a real scaffolder or a stub behind the @setup agent?** Its docstring promises far more than it does (H4c). If the agent is the only supported path, say so and slim the module; if headless scaffolding is a product feature, it needs the templates and tests.
3. **What's the intended trust model for DQ rule files without `status`?** (M3/2.4). If legacy rules files in existing consumer projects omit `status`, defaulting to `proposed` is a breaking change — is a one-release deprecation warning preferred?
4. **Expected data scale?** If tables stay < ~1M rows, M1/M5 can be deprioritized to documentation. Note futureproof-data already found the base query helpers' cost "dominates the MCP request budget" at its scale — evidence 2.2 belongs earlier rather than later.
4a. **Should futureproof-data's `mcp-futureproof-core` spec (KNOWN BLOCKER section) be imported into `docs/specs/` here?** It is currently the only written record of H5, and it lives in the consumer repo.
5. **Should the `GRIST_*` env-var shim and `governance_db.py` façade be removed in 0.5.0 or held for 1.0?** Both are cheap to keep; a stated timeline would let dependents plan.

---

*Process note: this codebase's remediation loop (audit → spec → fix → meta-test) demonstrably works — the connection-leak and swallowed-exception classes of bugs are extinct here. The pattern that keeps recurring is different in kind: defects at the consumer seam that the field project (futureproof-data) discovered, documented, and worked around locally — without the fix ever landing upstream. Milestone 2.1 (consumer-journey CI, with fixtures modeled on the real field manifest) is the structural fix for that class, the same way `test_no_swallowed_exceptions.py` was for silent failures. A lighter-weight habit helps too: any `# workaround for brightsmith bug` in a consumer repo should spawn a framework spec the same day.*

---

*Revision note (2026-07-02, same day): the initial draft claimed the framework "has never been exercised the way a consumer would use it." The owner pointed to `~/code/bright/futureproof-data`, which was then examined. The claim was corrected: the framework **was** field-exercised; the corrected finding is that field-discovered defects and workarounds never flowed back upstream. That examination confirmed M1/M6 and H4a in the field, explained why C1 never surfaced (base methods overridden downstream), and added H5 (two consumer-documented blockers in `domain_loader`/`run.py`) plus tasks 1.6 and 1.7.*
