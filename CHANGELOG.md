# Changelog

All notable changes to Brightsmith are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Brightsmith uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Changed

- **BREAKING — `log_agent_finding` now raises on a governance-DB write failure by default** (was fault-tolerant: logged a warning and returned `None`). The governance DB is authoritative for the agent-activity feed, so a dropped finding must fail the calling pipeline step rather than silently disappear — the same loud-failure doctrine the DQ and contract gates follow. Pass `strict=False` to restore best-effort logging where a logging hiccup must not fail the surrounding work. `write_agent_activity` (the underlying writer) already raised and is unchanged.
- **Pipeline-gate completion is now gated on the governance-DB write.** `complete_step` / `skip_step` commit the authoritative JSON state file *last* — only after `_emit_governance_event` (and any `--finding` write) succeeds. Previously the file was saved *before* the Iceberg write, so a failed governance write left the step marked `COMPLETED` on disk and the next `check` cleared anyway. Now a failed governance write leaves the file un-advanced, so the next step's `check` stays `BLOCKED` and the pipeline stops deterministically — no agent cooperation required. Retrying `complete` is idempotent (governance writes dedup on their grain `record_id`).

### Added

- **`pipeline_gate complete --finding "<summary>"`** — records an end-of-step summary to the governance `agent_activity` feed atomically with completion. The finding write is strict: if it fails, completion is not recorded and the next `check` stays `BLOCKED`.
- **Session logs restored as an Iceberg-authoritative `sessions` governance table** (the deleted `docs/sessions/` practice). New `sessions` schema, `write_session()` / `log_session()` writers (strict-by-default, idempotent on `session_id`), and `get_sessions()` query. The markdown file under `docs/sessions/` becomes an optional human-readable export; the table is the record of truth. `docs/workflows/session-logging.md` updated accordingly.

### Internal

- **Agent definitions consolidated to a single source of truth (`agents/`).** The repo previously carried two copies of all 25 agents — the plugin-shipped `agents/` and a project-local `.claude/agents/` — which had drifted 1,087 lines apart. Because pipeline skills dispatch via the `bs:` plugin namespace (which resolves from `agents/`), the shipped copy was the stale one: it lacked the governance-DB logging blocks and the `temporal-modeler` / `lineage-tracker` / `mcp-engineer` rewrites. The maintained `.claude/agents/` content was promoted into `agents/` and the project-local copy was removed. Dogfood in-repo via `claude --plugin-dir .` so agents resolve as `bs:*`, exactly as an installed user sees them.
- **Plugin manifest version bumped `0.2.0 → 0.3.0`** to match `pyproject.toml`.
- **Ruff ruleset expanded** to `E, F, B, I, UP, SIM` (was default `E/F` only). `E501` and a few opinionated `SIM`/`UP042` rules are ignored with documented reasons; 173 findings auto-fixed (import sorting, `datetime.UTC`, explicit `zip(strict=...)`), the rest fixed by hand. CI now enforces the expanded set.
- **No-swallowed-exceptions guard extended** to full-scope coverage of `governance/queries.py`, `infra/lineage.py`, and `infra/cab.py`. The `cab.py` blast-radius file-parse loops were narrowed to specific exception types; observability/CLI handlers are allowlisted with written justifications.
- **CI: coverage reporting** (`pytest --cov`), a **blocking `pyright` type-check gate** (`pyrightconfig.json`, basic mode over `src`), and a `paths-ignore` filter so docs/agent/skill-only commits don't spend Actions minutes.
- **`pyright` driven to zero errors and gated in CI.** Cut 164 → 25 false positives by declaring the dynamically-served `config` legacy names under `TYPE_CHECKING`; then fixed the remaining 25 real issues — an `_env` overload, None-guards (`relocate` avro schema, `domain_loader` cache_dir fallback, `chaos_monkey` corruption-fn guard), a `Callable` (was builtin `callable`) misannotation, a walrus narrowing in `contract`, `evaluate_threshold(raw_result: Any)`, sentinel `cast`s in `glossary_loader`, and justified `# type: ignore`s for two upstream stub gaps (`pyiceberg` `Table.identifier`, `mcp` `AnyUrl` handler typing). No runtime behavior change — verified by the full suite.
- **Removed committed framework self-development artifacts** (`governance/reviews/`, `audit-trail/`, `runtime-artifacts/`, a stray `pipeline-state` file, a test scorecard) and gitignored those runtime governance dirs. Only `governance/dq-rule-templates/` (shipped content) remains tracked.
- **Decomposed the two governance god-functions.** `sync_from_files` (384 lines) and `migrate_files_to_iceberg` (258 lines) are now thin orchestrators over one `_sync_*` / `_migrate_*` helper per source artifact type (largest helper ~85 lines). Behavior, return-dict keys, and ordering are unchanged — verified by the existing sync/migration tests. Adding a new source is now a helper + one line in the orchestrator tuple.

---

## [0.3.0] — 2026-06-30

### Fixed

- **Pipeline gate state was silently discarded across processes** (was a critical regression from the Iceberg-authoritative migration — `_save()` had been turned into a no-op while `_load()` still read the JSON file; every `check` after a `complete` reported BLOCKED). Restored dual-write: file is now authoritative for reads in this release; Iceberg event emission is preserved. The three-process repro (`init → complete → check`) now unblocks correctly.
- **Headless runner DQ gate never executed a rule** — `_run_dq_for_zone` replaced all rule execution with `# Simplified: count rules as passed`, meaning the P0 gate could never fail. It now calls `dq_runner.run_rules()` against real Iceberg data, honours the `ZONE_ALIASES` table so both canonical (`bronze`) and legacy (`raw`) table prefixes match, and propagates failures with exit code `EXIT_DQ_FAILURE`.
- **Contract `generate` → `verify` split-brain** — `save_contract` wrote to Iceberg only; `verify` / `list` read from files; the result was "No contracts found" out of the box. Both paths now dual-write YAML files under `governance/data-contracts/`, making round-trips work without running the exporter.
- **Errored P0 DQ rules were excused from blocking** — `validate_after_write` filtered out errored rules before evaluating P0 failures, so a rule that raised an exception was treated as a pass. Errored P0 rules are now included in `p0_failures` and block the gate. The `acknowledge` CLI remains the documented escape hatch.
- **`dq_runner` CLI subcommands `results` / `scorecard` / `badge` required `--spec`** — `get_latest_results(spec=None)` now aggregates the latest run per spec, so all three no-flag forms produce output and exit 0.
- **`_verify_contracts_for_zone` skipped alias-namespaced contracts** — contracts whose table names used legacy zone prefixes (`raw.`, `base.`, `consumable.`, `ai_ready.`) were not matched. The lookup now resolves both canonical and alias names.

### Changed

- **BREAKING — `compute_grain_id` raises `ValueError` on a missing grain field** (was silently hashing the empty string `""`). A missing field now raises immediately with the field name. `None` values are allowed and hash as `"None"`.
- **BREAKING — `compute_grain_id` escapes the `|` delimiter** — grain values containing a literal `|` are now escaped as `\|` before joining, so `("a|b", "c")` and `("a", "b|c")` produce different record IDs. **Any row whose grain values contained `|` will have a different `record_id` in 0.3.0.** Re-run the affected pipeline zones to regenerate record IDs; duplicate detection will reactivate correctly on the first re-promote.
- **`append_data` is now strict by default** — passing a column key that does not exist in the target schema raises `ValueError`. Pass `strict=False` to opt out (legacy behaviour).
- **Config is now accessed via `get_config()` / call-time** — `configure()` now takes effect even after `dq_runner`, `lineage`, `iceberg_setup`, and related modules have been imported. The six import-time bindings that previously froze config at import have been converted to call-time access.

### Added

- **`python -m brightsmith.infra.relocate`** — detects and repairs absolute warehouse paths baked into Iceberg metadata. Flags: `--check` (read-only scan, exits non-zero if any stale prefixes found), `--apply` (rewrites all four metadata layers — SQLite catalog rows, `*.metadata.json`, manifest-list `.avro`, manifest `.avro` — atomically per file, idempotent), `--relative` (rewrites to repo-root-relative paths for warehouses committed to git).
- **`WarehouseRelocationError`** — reading from a warehouse whose baked prefix differs from the current project root now raises this error with the exact `relocate --apply` command printed; silent empty-result reads are no longer possible.
- **`fastavro` dependency** (>=1.9) — required for the `relocate` module's Avro codec-preserving rewrites.
- **GitHub Actions CI** — `.github/workflows/ci.yml` runs `ruff check src tests` and `pytest tests/` on Python 3.11 and 3.12 on every push and pull request; also runs `relocate --check` to guard any committed example warehouses.
- **Characterization test suite for the pipeline gate** — `tests/infra/test_pipeline_gate.py` (≥ 12 subprocess-level tests) encoding the correct cross-process state-machine behaviour.
- **Contract round-trip test suite** — `tests/infra/test_contract_roundtrip.py` covering `generate → list → load → verify` in an isolated tmp project root.
- **No-swallowed-exceptions guard test** — `tests/infra/test_no_swallowed_exceptions.py` parses the enforcement-path modules with `ast` and fails on any bare `except Exception` handler that neither re-raises nor returns an explicit error sentinel.
- **`brightsmith.config.get_config()`** — returns a frozen dataclass snapshot of the current configuration; replaces direct module-level global access in all infrastructure modules.

### Security

- **`BaseMCPServer.query_iceberg` is now read-only** — the DuckDB connection sets `enable_external_access=false` immediately after loading the Iceberg extension; the statement is validated against an allowlist (first keyword must be in `{SELECT, WITH, DESCRIBE, SHOW}`; `COPY`, `INSTALL`, `LOAD`, `ATTACH`, DDL, and DML are rejected before execution). A rejected statement returns a structured error dict. This closes the prompt-injection file-exfiltration path (`COPY (SELECT ...) TO '/path'`). DQ rule SQL from governance JSON (trusted-operator input) continues to run through the separate `dq_runner` path, which is not subject to this allowlist.

### Deprecated

- **`GRIST_*` environment variables** (`GRIST_SERVER`, `GRIST_API_KEY`, `GRIST_DOC_ID`) — now emit a `DeprecationWarning` at first read. Targeted for removal in 0.4.0.
- **Module-level config globals** (e.g., `brightsmith.config.PROJECT_ROOT`) — deprecated in favour of `brightsmith.config.get_config()`. The module-level names remain as thin property aliases for 0.3.x compatibility and will be removed in 0.4.0.

### Internal

- **`governance/product.py` (2,637 lines) split** into `schemas.py`, `writers.py`, `queries.py`, `sync.py`, and `cli.py` under `src/brightsmith/infra/governance/`. `product.py` is retained as a re-export façade so every existing import keeps working. No behaviour change.

---

## [0.2.0] — 2026-05-xx

Initial public release with Iceberg-authoritative governance and zone naming.

---

[0.3.0]: https://github.com/hyena-studios/brightsmith/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/hyena-studios/brightsmith/releases/tag/v0.2.0
