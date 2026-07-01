# Changelog

All notable changes to Brightsmith are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Brightsmith uses [Semantic Versioning](https://semver.org/).

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
