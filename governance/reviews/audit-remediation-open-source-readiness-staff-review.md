# Staff Engineer Review

### Date: 2026-06-30
### Reviewer: @staff-engineer
### Spec: audit-remediation-open-source-readiness
### Status: APPROVED

### Verdict

This is production-quality and I'll put my name on it. I went in expecting the
usual AI failure mode — green tests that prove nothing, gates that "pass" because
they can't fail, error handling that swallows. I did not find it. The two Critical
regressions the audit empirically reproduced (A1 gate persistence, Q1 fake DQ gate)
are genuinely repaired, and — more importantly — they're now guarded by tests that
exercise the *real* cross-process CLI interface where the bugs actually lived, not
in-process shims. The fixes consistently convert silent success into loud failure,
which is the correct bias for a governance product. I re-ran the audit's smoking-gun
repro by hand across three separate processes and it CLEARs. I confirmed the MCP SQL
lockdown is real defense-in-depth, not a regex fig leaf. Nothing was weakened,
skipped, or deleted to get green.

### Verification Re-run (from scratch, isolated roots)

| # | Step | Result | Evidence |
|---|------|--------|----------|
| 1 | `pytest tests/` green, ≥524 | PASS | 585 passed in 65s. No skip/xfail/`pytest.skip` anywhere in `tests/` (grep clean). |
| 2 | `ruff check src tests` = 0 | PASS | `All checks passed!` |
| 3 | Gate sandbox repro, 3 processes | PASS | `init t` (sep proc) wrote `t-pipeline.json`; `complete t governance-reviewer-pre` (sep proc); `check t primary-agent` → `CLEAR: All prerequisites met`, exit 0. The exact A1 repro, fixed. |
| 4 | Headless DQ real: failing P0 → exit 1 & named; erroring P0 → exit ≠0 | PASS | `test_pipeline_runner.py` seeds a real `bronze.seed_facts` Iceberg table, writes a real P0 rule, runs `python -m brightsmith.run --zone bronze` via subprocess. `:194` failing rule → `EXIT_DQ_FAILURE` + `BRZ-FAIL` in stdout; `:218` missing-table rule → exit≠0 + `BRZ-ERR`. Both green on direct run. |
| 5 | Contract `generate→list→verify` round-trips green | PASS | `test_contract_roundtrip.py` 6/6 green on direct run. `save_contract` dual-writes YAML+Iceberg (confirmed in `test_governance_db.py` flip from `assert not path.exists()` → `assert path.exists()` + YAML round-trip). |
| 6 | `query_iceberg` rejects write-capable SQL | PASS | `_validate_read_only_sql`: REJECTs COPY, INSTALL, ATTACH, leading-comment DDL, `SELECT 1; <ddl>`, `SET enable_external_access=true`; ALLOWs `SELECT`, `WITH`. Layer 2 proven live: a DuckDB conn with `SET enable_external_access=false` raises `PermissionException` on `read_csv('/etc/hosts')`. `test_base_mcp_server.py` 20/20 green. |
| 7 | `configure()` takes effect post-import | PASS | `test_config.py` 7/7 green (imports consumers first, then `configure()`, asserts a rule under the new root loads). `dq_runner` uses an `_UNSET` sentinel resolving from live `config` at call time — sound. |
| 8 | README commands verbatim; doc drift fixed | PASS | `run.py --help` zone choices = `{bronze,silver,gold,mcp,all}` match README; "Moving or Cloning" section present (README:322) with the exact `relocate` commands; CI no-op note present. |
| 9 | Relocation: moved warehouse detected & repaired; never silent-empty | PASS | `test_relocate.py` 9/9 green. Live bonus: `relocate --check` against the developer's real local `data/` correctly fired CHECK FAILED on 2 rows baked with a foreign prefix (`/Users/jcernauske/code/grist/...`, files absent) — a genuine true positive. On a fresh root (`data/` absent) it exits 0 ("Nothing to do") — no false positive. |
| 10 | No theater (3+ new tests inspected) | PASS | Opened `test_pipeline_runner.py`, `test_relocate.py`, `test_no_swallowed_exceptions.py`, `test_pipeline_gate.py`, `test_grain.py`. All assert real behavior (exit codes, persisted JSON visible to fresh processes, original rows after repair, AST handler shape, raised `ValueError`). The "RED until WP-1.1" strings are assertion *failure messages*, not disabling annotations — tests are live and pass. |
| 11 | CI workflow valid | PASS | `.github/workflows/ci.yml`: push+PR, py3.11/3.12 matrix, `setup-uv`, `uv sync --dev`, `ruff check src tests`, `pytest tests/ -q`, plus `relocate --check` (no-ops when `data/` absent). |
| — | `uv build` (WP-3.3) | PASS | `brightsmith-0.3.0.tar.gz` + `-0.3.0-py3-none-any.whl`. `pyproject` version 0.3.0; CHANGELOG has explicit BREAKING entries for the grain-ID delimiter escape + missing-field raise. |

### Skeptical Findings (focus areas)

- **Real DQ gate, not a subtler placeholder.** `run.py:_run_dq_for_zone` (404-448) calls
  `dq_runner.run_rules()`, filters per-rule results to the zone (alias-aware via
  `_rule_matches_zone`), and counts P0 failures. The "Simplified: count rules as passed"
  comment and the `except: return (True,0,0,[])` are gone. An infra failure to *execute*
  DQ is caught at `run.py:362-369` and turned into a FAILED/TRANSFORM_ERROR exit, never a
  pass. Errored P0 genuinely blocks: `execute_sql_rule` records `passed=False` with the
  error captured (dq_runner.py:226-240), and `validate_after_write` (dq_runner.py:293-328)
  no longer filters errors out of `p0_failures` — the D3 hard-block is real.

- **Gate state machine: persistence is real and consumed.** `_save()` (pipeline_gate.py:281)
  writes the full legacy `{spec,zone,mode,started,steps,skipped_steps,approvals}` shape;
  `__init__` re-reads from disk every process (no `_state` caching). The exporter
  reconstruction (`exporters.py:_reconstruct_pipeline_state`, 109-204) emits the SAME shape
  keyed `spec` (not `spec_name`), replays events latest-per-step, and preserves `output_hash`
  from an existing file rather than fabricating it. Crucially it IS consumed:
  `check_zone_transition` (pipeline_gate.py:798-801) reads `data["spec"]`/`data.get("zone")`
  from those files, and `test_pipeline_gate.py` scenario 10 exercises `check-transition`
  against exporter-regenerated state. Dual-write is coherent, file-authoritative-for-reads
  as D1 dictates.

- **MCP SQL genuinely locked down.** Two independent layers. Layer 1 strips block/line
  comments and leading parens before matching the first keyword against
  {SELECT,WITH,DESCRIBE,SHOW}, and rejects embedded-semicolon multi-statements. Layer 2 sets
  `enable_external_access=false` — I verified live that this blocks `read_csv()` at execution,
  so even a SELECT-shaped exfiltration attempt that passes Layer 1 dies in DuckDB. A crafted
  leading-comment DDL and a `SELECT 1; <ddl>` both rejected.

- **Relocation loud-fail is correctly conditioned.** `detect_relocation` flags a row only
  when its `metadata_location` is absolute AND the file is missing AND the prefix is not
  under the current root — so fresh warehouses, in-place warehouses, new tables, and committed
  relative paths never raise. `get_catalog` uses a *per-table* guard (`_assert_table_not_relocated`)
  so one stale legacy row can't block healthy reads. Verified both directions: fresh root →
  exit 0; genuinely-moved real warehouse → fires with the exact repair command.

- **Error-handling sweep is enforced, not gamed.** `test_no_swallowed_exceptions.py` is an
  AST walk (not a string grep) over run.py/dq_runner.py/pipeline_gate.py/base_ingestor.py/
  promote.py + `queries.py:_query_table`. The allowlist carries a per-entry written
  justification, and `test_allowlist_entries_are_real` fails if any entry stops mapping to a
  real broad non-reraising handler — so the allowlist can't rot into a cheat. I read every
  allowlisted site; each is loud-by-design (records FAILED status / blocking issue / errored
  rule, or is the out-of-scope lineage wrapper). `_query_table` re-raises `GovernanceReadError`.

- **Breaking change documented.** CHANGELOG 0.3.0 has two explicit BREAKING entries for
  `compute_grain_id` (missing-field raise + `|` escaping) with re-derivation guidance. grain.py
  raises `ValueError` naming the field, escapes `\|`, allows explicit `None`. Tests prove
  `("a|b","c") != ("a","b|c")` and missing-key raises.

- **No test weakened to get green.** Modified tests: `test_grain.py` replaced a weak
  isinstance/len test with raise + determinism + collision assertions (stronger);
  `test_governance_db.py` flipped the contract assertion to `path.exists()` + YAML round-trip
  (stronger) plus WP-2.3 import-path moves; `test_cab.py` pushed a sunset date to 2099 to kill
  a wall-clock flake that would otherwise fail today (legitimate determinism fix, still tests
  DEPRECATED). All authorized by the spec's Testing Impact Analysis.

### Warehouse Population Check

N/A by spec design — this is an Infrastructure/cross-cutting spec that produces **no domain
zone tables** ("DQ Rules: Not applicable — no data transformation"). There is no target
namespace for it to populate. The spirit of the check (data actually persists to Iceberg and
reads back) is nonetheless proven: `test_relocate.py` writes a real two-snapshot Iceberg table,
moves it, repairs it, and reads the **original rows** back; the WP-1.2 runner tests seed a real
warehouse and execute rules against it via `iceberg_scan`. The warehouse machinery this spec
repairs is exercised end-to-end against real Iceberg data, not mocks.

### Issues (non-blocking advisories)

| # | Severity | File | Issue | Suggested Fix |
|---|----------|------|-------|---------------|
| 1 | ADVISORY | governance/reviews/ | Pre-implementation review artifact (`*-pre-review.md`) listed in the spec's Governance Artifacts checklist does not exist on the branch. Post-review, staff-review, and audit-trail JSON all exist. | Generate the pre-review or annotate it as skipped before marking COMPLETE. Process-only; does not affect code quality. |
| 2 | ADVISORY | infra/governance/sync.py (830), writers.py (723) | Both exceed the "~700 lines" soft target from WP-2.3 acceptance. The 2,637-line monolith was genuinely dismantled (max now 830), so intent is met. | Optional later split of `sync.py` (sync vs migration vs mermaid parser). |
| 3 | ADVISORY | infra/contract.py | `contract verify gold.widget` (table-qualified) reports not-found; only the bare name `widget` / `--all` work. Round-trip is green; mildly confusing UX. | Accept table-qualified names in `verify`, or document. |

### What's Acceptable

The whole thing. Two reproduced Criticals and five Highs fixed with behavioral tests at the
exact interface the regressions shipped through. The error-handling guard is self-policing.
The relocation detector demonstrably fires on a real moved warehouse and stays quiet on fresh
ones. Defense-in-depth on the MCP surface is real, verified at runtime. Fine work.
