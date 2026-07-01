# Spec: audit-remediation-open-source-readiness

**Status:** COMPLETE (2026-06-30 — all 16 WPs implemented; @governance-reviewer + @staff-engineer both APPROVED; 585 tests green, ruff clean, 0.3.0 builds. Process note: the pre-implementation review (Agent Workflow step 1) was not run — work began from this already-drafted spec; post-implementation + staff reviews were both completed.)
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent (model-routed per work package — see Model Routing)
**Created:** 2026-06-11
**Source:** `docs/technical-audit-2026-06-10.md` (full evidence, file:line citations, severity ratings)
**Related Specs:** `iceberg-authoritative-governance-and-zone-names.md` (introduced the regression this spec repairs), `governance-database-only.md`, `infra-framework-hardening.md`

---

## Claude Code Prompt

```
Read docs/specs/audit-remediation-open-source-readiness.md in its entirety, then
read docs/technical-audit-2026-06-10.md for the evidence behind every work package.

You are the ORCHESTRATOR. You do not write code yourself. You spawn subagents with
the model specified in the Model Routing table (Agent tool, `model: "opus"` or
`model: "sonnet"`), one work package per agent, in the phase order below. Paste the
full text of the work package into each agent's prompt along with the relevant
audit finding IDs.

Execution order (phases are barriers — do not start a phase until the previous
phase's gate passes):

  PHASE 0 (safety net):     WP-0.1 → WP-0.2 in sequence, then WP-0.3 ∥ WP-0.4 in parallel
  GATE 0: CI workflow file exists and `ruff check src tests` + `pytest tests/` pass
          locally; the new characterization tests exist and FAIL for the documented
          reasons (they encode intended behavior — red is correct at this point).
  PHASE 1 (critical fixes): WP-1.1 → WP-1.3 in sequence (same design decision),
                            WP-1.2 ∥ WP-1.4 ∥ WP-1.5 ∥ WP-1.6 in parallel with them
  GATE 1: All Phase-0 characterization tests now GREEN. Full suite green.
  PHASE 2 (leverage):       WP-2.1 → WP-2.2 → WP-2.3 in sequence, WP-2.4 ∥ WP-2.5 anytime
  GATE 2: Full suite green, ruff clean, zero `except Exception` in gate paths
          (verified by the WP-2.1 guard test).
  PHASE 3 (release polish): WP-3.1 ∥ WP-3.2 ∥ WP-3.3 in parallel
  GATE 3: Every command printed in README.md executes successfully verbatim.

After every work package: run `ruff check src tests` and `python -m pytest tests/ -q`.
A red suite blocks the next work package. Build accountability: the agent that broke
it fixes it, max 3 attempts, then STOP and escalate to the human.

Escalation rules:
  - Minor (fixable in place): fix, log in Implementation Log, continue.
  - Significant (design question, multi-file surprise, any deviation from this
    spec's stated decisions): STOP that work package, write the question to the
    Discussion section of this spec, alert the human.
  - Any test outside the work package's "Authorized Test Modifications" list
    fails: STOP and escalate. Never weaken, skip, or delete a test to get green.

Completion:
  1. Invoke @governance-reviewer (model: opus) for the post-implementation review
     → governance/reviews/audit-remediation-open-source-readiness-post-review.md
  2. Invoke @staff-engineer (model: opus) for the final quality gate, including
     re-running the Verification section below from scratch
     → governance/reviews/audit-remediation-open-source-readiness-staff-review.md
  3. Only after BOTH approve: check off Success Criteria, set Status: COMPLETE.
     @staff-engineer can send any work package back to its implementing agent.
```

---

## Problem Statement

An independent technical audit (2026-06-10) found that the two capabilities this framework
advertises most prominently are currently **broken or fake**:

1. **The pipeline gate doesn't persist state** (audit A1, Critical, empirically reproduced).
   `PipelineGate._save()` was turned into a no-op in commit `c1f41f5` while `_load()` still
   reads the JSON state file. The mandated CLI loop fails: `init` writes no file, `complete`
   prints success and loses the record, the next `check` reports BLOCKED. Every gate rule in
   CLAUDE.md is currently unenforceable, and it fails silently.
2. **The headless runner's DQ gate never executes a rule** (audit Q1, Critical).
   `run.py:_run_dq_for_zone` counts every rule as passed (`# Simplified: count rules as
   passed`). The "P0 gate" cannot fail. This violates the project's own first principle:
   DQ rules validate real data, never placeholders.
3. **The Iceberg-authoritative migration is half-finished** (audit A2/Q6, Critical/High).
   Writes go to Iceberg; reads come from files; the exporter bridge is manual-only and
   emits a shape the gate cannot consume. Contracts: `generate` writes Iceberg,
   `verify` reads files → "No contracts found" out of the box.

4. **Iceberg warehouses are not relocatable** (audit A6, High, field-reported 2026-06-11).
   `get_catalog()` bakes the absolute warehouse path (`/home/jcernauske/...` or
   `/Users/jcernauske/...`) into four metadata layers — SQLite catalog rows, every
   `*.metadata.json`, every manifest-list `.avro`, every manifest `.avro`. A cloned, moved,
   or containerized project reads **silently empty** instead of erroring. The
   `futureproof-data` consumer hit this the night before a deadline and shipped a
   5,740-file emergency rewrite (`scripts/rebase_iceberg_paths.py`, commits `b81c7b7a` +
   `9f789d0d`) whose docstring names this framework as the root cause.

Supporting findings: no CI and 55 outstanding ruff errors (D1), ~80 `except Exception`
handlers that convert failures into successes in enforcement paths (Q3), an untested
1,149-line enforcement core (T1), a write-capable SQL surface exposed to LLMs (S1), and
a config module whose `configure()` silently doesn't work (A3).

**Why now:** this repository is about to be open-sourced and publicly promoted. The first
thing a skeptical reader will do is run the README quick start. Today it doesn't work.
Reputation is made or destroyed in that first five minutes.

## Decisions (resolved audit open questions — human may veto before execution)

| # | Question (audit §Open Questions) | Decision for this spec | Rationale |
|---|----------------------------------|------------------------|-----------|
| D1 | Iceberg-authoritative reads vs file rollback | **Dual-write hotfix:** restore file persistence in `_save()` AND keep Iceberg event emission; fix exporter shape to match. Full Iceberg-read migration is a follow-up spec. | Lowest-risk path to a working release; preserves the Iceberg investment; one consumer project keeps working |
| D2 | Grain ID semantics change | **Fix it** (escape delimiter, raise on missing fields). Ship as 0.3.0 with a CHANGELOG breaking-change note. | Pre-1.0, one known consumer; correctness beats compat here |
| D3 | Errored P0 rules | **Hard-block.** An errored P0 rule fails the gate; the existing `acknowledge` flow is the escape hatch. | A gate that can't fail is worse than no gate |
| D4 | Session logging mandate vs deleted `docs/sessions/` | **Drop the mandate** from README + CLAUDE.md workflow references. | Practice was already abandoned in `3dce94b`; docs must tell the truth |
| D5 | MCP SQL threat model | **Read-only enforcement now**; RLS/entitlements deferred until non-localhost deployment. | Cheap fix, closes the prompt-injection file-write hole |
| D6 | Legacy `GRIST_*` env vars | **Keep with a one-line DeprecationWarning**; removal targeted for 0.4.0. | Consumer project may still set them |
| D7 | Warehouse relocatability (A6) | **Detect + repair, don't change write format:** keep PyIceberg's spec-compliant absolute paths at write time; ship `python -m brightsmith.infra.relocate --check/--apply` (generalized from futureproof-data's field-proven script) and make stale-prefix reads fail loudly with the fix command. `--relative` mode offered for committing warehouses to git. | Relative paths are nonstandard per the Iceberg spec and fragile across PyIceberg versions; detection + a one-command repair covers clone/move/Docker without risking write-path regressions |

## Success Criteria

- [x] The audit's sandbox repro passes: `pipeline_gate init → complete → check` across three separate processes unblocks correctly, and a subprocess test encodes it permanently
- [x] `python -m brightsmith.run --zone bronze` against a seeded warehouse with one failing P0 rule **exits 1** and names the rule; with an *erroring* P0 rule it also exits 1
- [x] `contract generate → contract list → contract verify` round-trips green out of the box
- [x] GitHub Actions CI runs ruff + pytest on every push/PR; both green; README badges wired to it
- [x] `ruff check src tests` reports 0 errors
- [x] Zero `except Exception` in the enforcement paths of `run.py`, `pipeline_gate.py`, `dq_runner.py`, and `product.py:_query_table` without a narrowed type or explicit error-status propagation — enforced by a grep-based guard test
- [x] `BaseMCPServer.query_iceberg` rejects write-capable SQL (`COPY TO`, DDL, `INSTALL`); test proves it
- [x] `config.configure()` demonstrably changes paths used by `dq_runner`, `lineage`, and `iceberg_setup` after import — proven by test
- [x] `tests/infra/test_pipeline_gate.py` exists with ≥ 12 behavioral tests (subprocess-level)
- [x] In-batch duplicate records (same `record_id` twice in one promote call) append exactly once; missing grain fields raise
- [x] All three no-`--spec` forms of `dq_runner` CLI (`results`, `scorecard`, `badge`) produce output
- [x] A warehouse ingested under one project root, then moved to another, is detected (`relocate --check` exits non-zero naming the baked prefix) and repaired (`relocate --apply` → reads return the original rows) — proven by an end-to-end test
- [x] Reading a moved-but-not-relocated warehouse raises an actionable error naming the `relocate` command — never silently returns empty results
- [x] Every command printed in README.md works verbatim; README/CLAUDE.md factual drift items from the audit are fixed
- [x] Version bumped to 0.3.0 with a CHANGELOG.md documenting the grain-ID breaking change
- [x] Full suite green: `python -m pytest tests/` (existing 487 tests stay green — none weakened, skipped, or deleted)

## Model Routing

**Principle:** Opus owns anything requiring judgment about *intended semantics* (state
machines, error taxonomy, API contracts). Sonnet owns anything where this spec fully
specifies the change (mechanical fixes, scaffolding, doc edits). Reviews are always Opus.

| Work package | Model | Why |
|---|---|---|
| WP-0.1 CI workflow | sonnet | Fully specified scaffolding |
| WP-0.2 Ruff cleanup | sonnet | 51/55 auto-fixable |
| WP-0.3 Gate characterization tests | **opus** | Tests *define* intended behavior — highest-judgment artifact in the spec |
| WP-0.4 Contract round-trip tests | sonnet | Scenarios enumerated below |
| WP-1.1 Gate persistence repair | **opus** | State-machine semantics, dual-write design, exporter shape |
| WP-1.2 Real headless DQ gate | **opus** | Zone/alias mapping judgment, failure taxonomy |
| WP-1.3 Contract read/write unification | **opus** | Same design decision as WP-1.1 |
| WP-1.4 P0-error loophole | sonnet | Small, fully specified |
| WP-1.5 Read-only MCP SQL | sonnet | Fully specified |
| WP-1.6 Warehouse relocatability | **opus** | Four metadata layers, avro round-trip fidelity, detection semantics — and a reference implementation to generalize, not copy blindly |
| WP-2.1 Error-handling sweep | **opus** | Per-site judgment: re-raise vs UNKNOWN vs narrow |
| WP-2.2 Config refactor | **opus** | Cross-cutting API design |
| WP-2.3 Split product.py | sonnet | Mechanical move along existing section comments |
| WP-2.4 Grain hardening | **opus** | Hash-semantics change with compat implications (D2) |
| WP-2.5 dq_runner CLI fixes | sonnet | Fully specified |
| WP-3.1 Doc-truth pass | sonnet | Enumerated edits |
| WP-3.2 Zone-name canonicalization | sonnet | Alias table exists; mechanical |
| WP-3.3 Release packaging (0.3.0, CHANGELOG, badges) | sonnet | Mechanical |
| @governance-reviewer pre/post, @staff-engineer final | **opus** | Quality gates |

## Technical Design

> Audit finding IDs (A1, Q1, …) refer to `docs/technical-audit-2026-06-10.md`. Implementing
> agents MUST read the cited audit section before coding — it contains the file:line evidence
> and the reproduction steps.

### Phase 0 — Safety net

#### WP-0.1 CI pipeline (D1)
Create `.github/workflows/ci.yml`: trigger on push + PR; ubuntu-latest; Python 3.11 **and**
3.12 matrix; `astral-sh/setup-uv`; steps: `uv sync --dev`, `uv run ruff check src tests`,
`uv run pytest tests/ -q`. Cache uv. No release/publish jobs in this spec.
**Accept:** workflow file valid (run `act` dry parse or push to a branch); both steps green locally.

#### WP-0.2 Ruff cleanup (D1)
`ruff check --fix` for the 51 auto-fixables (F541, F401); hand-review the 4 F841 unused
variables — delete only if provably dead, otherwise escalate. Also delete the dead
`_COMPAT_DQ_RESULTS_DIR` alias (`dq_runner.py:32`) and its now-unused import if applicable.
**Accept:** `ruff check src tests` → 0 errors; full suite green.

#### WP-0.3 Gate characterization tests (T1, T3) — *encodes intended behavior; RED until WP-1.1*
New `tests/infra/test_pipeline_gate.py`. All gate-CLI tests run via
`subprocess.run([sys.executable, "-m", "brightsmith.infra.pipeline_gate", ...],
env={**os.environ, "BRIGHTSMITH_PROJECT_ROOT": str(tmp_path)})` — the bug class is
cross-process state, so in-process tests are insufficient. Required scenarios:
1. `init` creates durable state discoverable by a fresh process
2. `complete` then `check` of the dependent step → CLEAR (the audit's repro)
3. `check` of a step with unmet prereqs → exit 1, BLOCKED
4. `skip` of a non-skippable step → exit 1, refused
5. `skip` without reason/evidence → exit 1
6. `validate` with NOT_STARTED steps → exit 1, names them
7. `validate` with all steps complete + outputs present → exit 0
8. Output-hash tamper detection: modify an output file post-complete, `validate` flags it
9. `approve` records decision; visible to a fresh process
10. `check-transition` works against state regenerated by `export_pipeline_state_to_files()`
11. `audit --format json` parses and includes steps
12. In-process unit tests for `check_prerequisites` / `_get_step_def` edge cases (unknown step raises)
Plus `tests/infra/test_staging.py` basics (apply_gate matrix: 4 confidence×toggle combos; approve/reject round-trip).
**Authorized test modifications:** none — this WP only adds tests.
**Accept:** tests 1, 2, 9, 10 FAIL on current code with assertions that describe the bug; the rest pass or fail consistent with current behavior, each failure annotated `# RED until WP-1.1`.

#### WP-0.4 Contract round-trip tests (A2) — *RED until WP-1.3*
New `tests/infra/test_contract_roundtrip.py`: in a tmp project root, seed a minimal Iceberg
table, then `generate_contract → list_contracts → load_contract → verify_contract` must
round-trip; CLI-level subprocess variant for `generate` + `verify --all`.
**Accept:** failing tests that capture "generate then verify finds nothing," annotated `# RED until WP-1.3`.

### Phase 1 — Critical fixes

#### WP-1.1 Gate persistence repair (A1; decision D1)
Step zero — restore the bleed-stop: `PipelineGate._save()` writes the full legacy JSON shape
(`spec`, `zone`, `mode`, `started`, `steps{...}`, `skipped_steps`, `approvals`) to
`{spec}-pipeline.json`, exactly the shape of the committed
`governance/pipeline-state/brightgemma-deepagents-runtime-pipeline.json`. Keep all existing
Iceberg event emission (`_emit_governance_event`) — dual-write, file is authoritative for
gate reads in this release. Then: fix `export_pipeline_state_to_files()`
(`exporters.py:109-125`) to emit that same legacy shape (reconstruct `steps` by replaying
`get_pipeline_events`, latest event per step wins; key must be `spec`, not `spec_name`) so
`check_zone_transition` (`pipeline_gate.py:776`) and `audit_report` (`:873`) can consume
exporter output. Gotchas from the audit: `output_hash` is not in the event schema — when
replaying events, preserve hashes from an existing state file if present, else omit; never
cache `_state` across CLI invocations.
**Authorized test modifications:** remove the `# RED until WP-1.1` annotations in WP-0.3 tests.
**Accept:** all WP-0.3 tests green; full suite green; manual sandbox repro from the audit passes.

#### WP-1.2 Real headless DQ gate (Q1; decision D3)
Replace the body of `_run_dq_for_zone` (`run.py:328-352`): call `dq_runner.run_rules()`
filtered to rules whose `tables` reference the zone (match BOTH canonical and alias names
via `ZONE_ALIASES` so `raw.` and `bronze.` both hit). Derive `(p0_ok, passed, failed,
p0_failures)` from `result["results"]` plus rule priorities; **an errored P0 rule counts as
a failure**. Delete the "Simplified" comment and the `except Exception: return (True, 0, 0, [])`
— let infrastructure errors surface as `TRANSFORM_ERROR`/`CONFIG_ERROR` exits. Same
treatment for `_check_contracts_for_zone` (`run.py:375-376`): distinguish "no contracts"
(pass, with warning) from "verification errored" (fail). `--validate-only` must run real DQ.
Fix the module docstring (`run.py:10-11`) to use flags argparse actually accepts.
**New tests:** `tests/infra/test_pipeline_runner.py` gains behavioral tests with a seeded tmp
warehouse: failing P0 → exit `EXIT_DQ_FAILURE`; erroring P0 → exit ≠ 0; passing rules → exit 0.
**Authorized test modifications:** existing `test_pipeline_runner.py` dataclass tests may be
updated only where they asserted the placeholder behavior.
**Accept:** Success Criterion 2; suite green.

#### WP-1.3 Contract read/write unification (A2; decision D1)
`save_contract` (`contract.py:130-153`) writes the YAML file to `governance/data-contracts/`
**always** (creating the dir), in addition to the existing Iceberg `sync_contract` call —
mirroring WP-1.1's dual-write. `_cmd_generate` therefore produces a file `verify`/`list` can
read. Keep `export_contracts_to_files()` as the regeneration path and make it emit the same
YAML shape `load_contract` parses (round-trip test from WP-0.4 proves it).
**Accept:** WP-0.4 tests green; `attach_governance` in `base_mcp_server.py` finds contracts.

#### WP-1.4 Close the P0-error loophole (Q2; decision D3)
`validate_after_write` (`dq_runner.py:296-309`): errored P0 rules are included in
`p0_failures` (drop the errors-are-excused filter). Error text goes into the raised
`DQValidationError`. The `acknowledge` CLI remains the documented escape hatch.
**New test:** P0 rule referencing a nonexistent table → `validate_after_write` raises.
**Accept:** test green; no other behavior change.

#### WP-1.5 Read-only MCP SQL (S1; decision D5)
`query_iceberg` (`base_mcp_server.py:405-437`): create the DuckDB connection read-only in
effect — set `con.execute("SET enable_external_access=false")` after loading the iceberg
extension, and validate the statement (single statement; first keyword in
{SELECT, WITH, DESCRIBE, SHOW}; reject `COPY`, `INSTALL`, `LOAD`, `ATTACH`, DDL/DML) before
execution, returning a structured error dict on rejection. Document the trust boundary in
the class docstring (DQ rule SQL from governance JSON remains trusted-operator input;
MCP-client SQL is untrusted).
**New tests:** `tests/mcp/test_base_mcp_server.py` additions: `COPY ... TO` rejected;
`read_csv('/etc/hosts')`-style external access fails; plain SELECT still works.
**Accept:** tests green.

#### WP-1.6 Warehouse relocatability (A6; decision D7)
**Reference implementation to study first:**
`~/code/bright/futureproof-data/scripts/rebase_iceberg_paths.py` (field-proven on a
141-row catalog / 5,740-metadata-file / 5,674-avro warehouse; idempotent; crash-safe via
`.tmp` + `os.replace`). Generalize it — do not copy verbatim: brightsmith must derive roots
from `config` (`WAREHOUSE_PATH`, `CATALOG_PATH`, `GOVERNANCE_WAREHOUSE`), not a hardcoded
repo layout, and must handle the governance warehouse too.

Three deliverables:
1. **`python -m brightsmith.infra.relocate`** — new module with `--check` (read-only scan
   for path prefixes that don't match the current project root; exit non-zero listing the
   baked prefix and affected layer counts) and `--apply` (rewrite all four layers —
   SQLite catalog `metadata_location`/`previous_metadata_location` rows, `*.metadata.json`
   `location` + snapshot `manifest-list` paths, manifest-list `.avro`, manifest `.avro`
   data-file paths — to the current absolute project root; `--relative` flag rewrites to
   repo-root-relative instead, for warehouses committed to git). Idempotent; atomic per
   file; avro codec preserved (add `fastavro` to dependencies).
2. **Loud failure on stale reads:** in `get_catalog()`/table-load paths, when a loaded
   table's `metadata_location` exists in the catalog but the file does not exist on disk
   AND its prefix differs from the current warehouse root, raise
   `WarehouseRelocationError` with the exact `relocate --apply` command — never let
   `iceberg_scan` proceed to silently-empty results. Same check surfaced in
   `pipeline_gate._validate_warehouse_population` and `run.py` preflight.
3. **README section** "Moving or cloning a project" documenting the workflow, plus a
   `relocate --check` step in the CI workflow from WP-0.1 (guards committed example
   warehouses, no-ops when `data/` is absent).

**New tests** (`tests/infra/test_relocate.py`, ≥ 5): build a tiny warehouse in tmp root A
(reuse the `test_iceberg_roundtrip.py` fixtures), `shutil.move` it to root B → reading
raises `WarehouseRelocationError` naming the command; `relocate --check` exits non-zero;
`--apply` then reads back the original rows; `--apply` twice is a no-op; `--relative` mode
round-trips with CWD pinned to root B.
**Accept:** end-to-end move test green; futureproof-data's scramble is reproducible and
fixed by the framework command alone.

### Phase 2 — High-leverage improvements

#### WP-2.1 Error-handling sweep of enforcement paths (Q3)
Scope: `run.py`, `dq_runner.py`, `pipeline_gate.py`, `product.py:_query_table`,
`base_ingestor.py:_build_existing_grains`. For each `except Exception`: (a) narrow to the
expected exception type with a comment naming the expectation, or (b) re-raise, or
(c) return an **explicit** error value the caller must distinguish from "empty/success"
(e.g. `_query_table` raises a new `GovernanceReadError` instead of returning `[]`;
`_build_existing_grains` re-raises instead of silently disabling dedup). Out of scope:
the deliberately fault-tolerant lineage emission wrappers (`promote.py:87-88`,
`base_ingestor.py:262-263`) — these stay, they're annotated by design.
**Guard test:** add `tests/infra/test_no_swallowed_exceptions.py` that parses the scoped
files with `ast` and fails on any bare `except Exception` handler that neither raises nor
returns a sentinel from an allowlist — with an explicit, comment-justified allowlist in
the test file.
**Authorized test modifications:** any existing test that depended on swallow-and-continue
must be updated to assert the new loud behavior (list each in the Implementation Log).
**Accept:** guard test green; suite green.

#### WP-2.2 Config refactor (A3)
Introduce `brightsmith.config.get_config()` returning a frozen dataclass snapshot;
`configure()` and env vars feed it. Keep the module-level names as thin properties or
deprecated aliases for one release (domain packs import them). Migrate the six import-time
binders (`dq_runner.py:28`, `lineage.py:38`, `dq_scorecard.py:13`, `iceberg_setup.py:21`,
`domain_loader.py:28`, `glossary_loader.py:23`) to call-time access. Add the `GRIST_*`
DeprecationWarning (D6).
**New test:** `configure(project_root=tmp)` after importing `dq_runner` → rules load from
the new root.
**Accept:** test green; suite green; no caller outside `config.py` reads `config.<GLOBAL>` at module level.

#### WP-2.3 Split product.py (A4)
Mechanical split of `infra/governance/product.py` (2,637 lines) along its existing section
comments into `schemas.py`, `writers.py`, `queries.py`, `sync.py` (sync_from_files +
migration + mermaid parser), `cli.py`; `product.py` becomes a re-export façade so every
existing import keeps working. `governance_db.py` shim stops re-exporting private names —
update the two known private consumers (`dq_runner`, tests) to import from the new modules.
No behavior change, no signature change.
**Accept:** no file in `infra/governance/` exceeds ~700 lines; `git diff --stat` shows moves
not rewrites; suite green with zero test edits except import paths.

#### WP-2.4 Grain hardening (Q4, Q5; decision D2)
`compute_grain_id`: raise `ValueError` naming the field when a grain field is absent from
the row (`None` present is allowed and hashes as "None"); escape the delimiter
(`str(v).replace("|", "\\|")`) before joining. `filter_existing_records`
(`iceberg_setup.py:91-123`): dedupe within the incoming batch (`DISTINCT ON (id_field)` or
drop-duplicates on the Arrow table) before the anti-join; same in-batch dedup in
`BaseIngestor.ingest` against `self._make_grain`. `append_data` (`iceberg_setup.py:63`):
raise on record keys that match no schema field when strict mode is on (default on).
**Breaking change:** record_ids change for rows whose grain values contained `|`. CHANGELOG
entry required (WP-3.3).
**Accept:** new tests — missing grain field raises; `("a|b","c")` ≠ `("a","b|c")`; in-batch
duplicate promotes once; suite green.

#### WP-2.5 dq_runner CLI fixes (Q6)
Implement all-specs behavior for `get_latest_results(spec=None)` (aggregate latest run per
spec), so `results`, `scorecard` (its `_get_all_specs` loop already exists — let it be
reached), and `badge` work with no flags as documented.
**Accept:** subprocess tests for all three no-flag forms produce output, exit 0.

### Phase 3 — Release polish

#### WP-3.1 Doc-truth pass (Documentation findings 1–5; decision D4)
README: quick-start commands verified by literally running each; agent count corrected
(25); remove "Anthropic SDK" from Stack or mark it consumer-side; remove the Session
Logging section. CLAUDE.md: fix `src/config.py` → `src/brightsmith/config.py`; add
`src/brightsmith/infra/governance/` to Key Paths; remove the session-logging workflow
reference. `run.py` docstring already fixed in WP-1.2 — verify.
**Accept:** a fresh-clone walkthrough of README Option 2 (headless) succeeds end to end.

#### WP-3.2 Zone-name canonicalization (A5)
One canonical set internally (`bronze/silver/gold/mcp`); `raw/base/consumable/ai_ready`
accepted only at boundaries through `ZONE_ALIASES`/`normalize_zone`. Sweep
`_KNOWN_NAMESPACES`, CLI choices, docstrings; add alias-acceptance tests.
**Accept:** grep shows no internal logic branching on alias names except the alias tables.

#### WP-3.3 Release packaging
`pyproject.toml` → 0.3.0; create `CHANGELOG.md` (Keep-a-Changelog format) covering: gate
persistence fix, real headless DQ, contract round-trip, grain-ID breaking change (D2),
read-only MCP SQL, the new `relocate` command + `fastavro` dependency (A6), config
accessor, deprecations (GRIST_* vars, module-level config globals). Wire README badges
to the CI workflow.
**Accept:** `uv build` succeeds; badges render against the repo's CI.

## Testing Impact Analysis

- **Existing tests at risk:** `tests/infra/test_pipeline_runner.py` (asserts placeholder DQ
  summarization — authorized for update in WP-1.2 only); any test relying on
  swallow-and-continue (WP-2.1, must be enumerated in the Implementation Log); import-path
  edits only in WP-2.3.
- **Confirmed safe (STOP and escalate if they break):** all of `tests/infra/test_promote.py`,
  `test_grain.py` (until WP-2.4, which updates them deliberately), `test_governance_db.py`,
  `test_cab.py`, `test_contract.py`, all `tests/mcp/`, `tests/bronze/`, `tests/silver/`.
- **Never:** disable, skip, weaken, or delete a test to get green. Placeholder assertions
  (`assert True`-equivalents) are a rejection.
- **Minimum new tests:** WP-0.3 ≥ 12, WP-0.4 ≥ 4, WP-1.2 ≥ 3, WP-1.4 ≥ 1, WP-1.5 ≥ 3,
  WP-1.6 ≥ 5, WP-2.1 guard ≥ 1, WP-2.2 ≥ 1, WP-2.4 ≥ 4, WP-2.5 ≥ 3. (~37 total.)

## Agent Workflow

Infrastructure spec — no zone pipeline applies. Reviews are mandatory regardless of
`REQUIRE_HUMAN_APPROVAL` (per project memory: governance + staff-engineer reviews always
run before COMPLETE).

1. @governance-reviewer (opus) — pre-implementation review of this spec → `governance/reviews/audit-remediation-open-source-readiness-pre-review.md`
2. Orchestrator executes Phases 0–3 per the Claude Code Prompt (model routing table)
3. @governance-reviewer (opus) — post-implementation completeness check
4. @staff-engineer (opus) — final gate: re-runs Verification from scratch, spot-checks that
   the characterization tests actually assert behavior (test-theater check), verifies the
   sandbox repro by hand. Can send any WP back.

## DQ Rules

Not applicable (no data transformation). This spec *repairs* the DQ enforcement machinery
that all domain specs depend on.

## Governance Artifacts

- [ ] Pre-review: `governance/reviews/audit-remediation-open-source-readiness-pre-review.md` (NOT RUN — work began from this already-drafted spec)
- [x] Post-review: `governance/reviews/audit-remediation-open-source-readiness-post-review.md` (APPROVED)
- [x] Staff review: `governance/reviews/audit-remediation-open-source-readiness-staff-review.md` (APPROVED)
- [x] Audit trail: `governance/audit-trail/audit-remediation-open-source-readiness.json`
- [x] CHANGELOG.md (new, repo root)

## Verification (run from scratch by @staff-engineer)

1. `python -m pytest tests/ -q` — green, ≥ 524 tests (487 existing + ~37 new)
2. `ruff check src tests` — 0 errors
3. **Sandbox repro (the audit's smoking gun), in a fresh tmp dir:**
   `pipeline_gate init t --zone bronze` → state file exists →
   `complete t governance-reviewer-pre` → `check t primary-agent` → exit 0, CLEAR
4. **Headless DQ is real:** seed a tmp warehouse, add a P0 rule that must fail,
   `python -m brightsmith.run --zone bronze` → exit 1, rule named in output
5. **Contract round trip:** `contract generate` → `contract verify` → green, no flags beyond table/grain
6. **MCP read-only:** `query_iceberg("COPY (SELECT 1) TO '/tmp/x.csv'")` → rejected error dict; `/tmp/x.csv` not created
7. **Config:** `configure(project_root=X)` post-import changes where `dq_runner` loads rules
8. **README walkthrough:** every command in README quick start (Option 2) runs verbatim
9. **Relocation:** ingest into a tmp warehouse, `mv` the project dir, confirm the read
   raises `WarehouseRelocationError` (not empty results), run the printed
   `relocate --apply` command, confirm the original rows come back
10. **No theater:** open 3 randomly chosen new tests and confirm they assert behavior, not execution
11. CI workflow green on a pushed branch

## Discussion

```
(empty — agents append here per escalation rules)
```

## Out of Scope (explicit, per audit strategy)

- Full Iceberg-authoritative *reads* for the gate (follow-up spec; D1 chose dual-write)
- RLS/entitlements for MCP (deferred until non-localhost; D5)
- Governance-table compaction (audit P3 — defer until volume hurts)
- Perf items P1/P2 beyond what WP-2.4 touches incidentally
- Removing `GRIST_*` vars (deprecation only; removal in 0.4.0)
- Any rewrite of agent/skill markdown content beyond doc-truth fixes
