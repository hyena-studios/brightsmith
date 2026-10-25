# Spec: osi-semantic-model-export

**Status:** COMPLETE (2026-07-03 — all 3 WPs implemented; staff-engineer review APPROVED with 2 non-blocking findings, both addressed same-session; plus human-approved WP-4 follow-up (`get_semantic_model` tool). 862 tests green, ruff + pyright clean)
**Zone:** Infrastructure (cross-cutting) + MCP
**Primary Agent:** Claude Code (implementer), @staff-engineer (final review)
**Created:** 2026-07-03
**Source:** OSI assessment in session following `docs/technical-audit-2026-07-03.md`; OSI v1.0 spec (https://github.com/open-semantic-interchange/OSI, Apache-2, released 2026-01-27)
**Related Specs:** `data-contracts.md`, `governance-database-only.md`, `ai-ready-mcp-server.md`

---

## Claude Code Prompt

```
Read docs/specs/osi-semantic-model-export.md in its entirety.

Implement the three work packages in order (WP-1 exporter, WP-2 MCP resource,
WP-3 docs/changelog), then run the full verification block. Completion requires
a staff-engineer review before the spec is marked COMPLETE.

After every work package: uv run ruff check src tests && uv run pyright &&
uv run python -m pytest tests/ -q. A red suite blocks the next work package.
```

---

## Problem Statement

Brightsmith already produces every ingredient of a semantic model — data
contracts (datasets, fields, grain keys), a business glossary (descriptions,
synonyms, CDE/PII flags), Mermaid ER data models (relationships), domain
context (AI instructions), and optional metric hints — but stores them as five
separate proprietary artifacts. The only way to consume a Brightsmith gold zone
today is Brightsmith's own MCP server.

The Open Semantic Interchange (OSI) v1.0 specification (Snowflake, Salesforce/
Tableau, dbt Labs, Databricks + ~50 vendors; finalized 2026-01-27) is a
vendor-neutral YAML interchange format for exactly this content: `datasets`
(fields, keys, source mappings), `relationships`, `metrics`, `ai_context`
(instructions/synonyms) at every level, and `custom_extensions` for vendor
metadata. Emitting one OSI document per project makes every gold-zone data
product consumable by any OSI-aware platform, using artifacts the pipeline
already produces.

## Decisions

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| D1 | Internal format vs export boundary | **Export boundary only.** Contracts/glossary/models remain the internal sources of truth; the OSI doc is generated, never hand-edited. | Contracts carry DQ/freshness/lifecycle semantics OSI has no vocabulary for; glossary carries typed CDE/PII flags. |
| D2 | Which tables become datasets | **Gold zone by default** (alias-aware: `consumable` → gold), `--zones` CLI flag to widen. | Gold is the contracted data-product zone; bronze/silver are internal. |
| D3 | SQL dialect | **`ANSI_SQL` only.** | DuckDB-compatible; other dialects are a consumer concern. |
| D4 | Determinism | **No timestamps in the emitted YAML; all collections sorted.** Re-export with unchanged inputs is byte-identical. | Matches the idempotency doctrine; enables the drift check. |
| D5 | Relationships without resolvable columns | **Emit only relationships whose join columns can be resolved** (source entity has an FK column matching a PK column of the target entity, by name). Unresolvable ones are logged and skipped — OSI requires `from_columns`/`to_columns`. | Deterministic and honest; no invented join keys. |
| D6 | CDE/PII flags | **`custom_extensions` with `vendor_name: brightsmith`**, JSON payload per OSI spec. | OSI has no native governance-flag vocabulary; extensions are the designed escape hatch. |
| D7 | Missing vs malformed inputs | **Missing artifact → that section is empty (logged). Malformed artifact → raise `OSIExportError` (loud). Zero contracts → raise.** | Loud-failure doctrine: absence is a legitimate state, corruption is not, and an empty semantic model is useless. |
| D8 | Spec version pinning | Emit `version: "1.0"` from a single module constant `OSI_SPEC_VERSION`. | Spec is 5 months old and still moving; one-line bump when it revs. |
| D9 | domain-context.md size | **Full text** into model-level `ai_context.instructions`. | It is the canonical context; truncating it silently would betray the artifact's purpose. |
| D10 | OSI import (hints) | **Out of scope** — exporter first; importer waits for a real consumer with an OSI file in hand. | Effort vs. demonstrated demand. |

## Work Packages

### WP-1 — OSI exporter module (`src/brightsmith/infra/osi.py`)

- `build_semantic_model(zones=..., contracts_dir=..., ...) -> dict` — pure
  composer, no I/O side effects:
  - **datasets** ← `contract.list_contracts()` + `load_contract()` filtered to
    the requested zones. `source` = `"{PROJECT_NAME}.{namespace}.{table}"`;
    `primary_key` = grain columns; fields carry ANSI_SQL identity expressions,
    descriptions/synonyms resolved from the business glossary via
    `business_term_id` (fallback: case-insensitive name match), CDE/PII/type
    flags in a `brightsmith` custom extension; contract name/version/status and
    lineage inputs in a dataset-level `brightsmith` extension.
  - **relationships** ← `governance/models/*.md` via the existing
    `governance.parsers._parse_mermaid_erdiagram`, entity names matched to
    exported datasets case-insensitively, join columns per D5.
  - **metrics** ← `DomainHints.metrics` file (YAML/JSON; `metrics:` list or
    top-level list; entries carry `name` + `expression`/`sql`, optional
    `description`). Malformed → `OSIExportError`.
  - **ai_context** ← `governance/domain-context.md` full text (D9).
- `export_semantic_model(output_path=None) -> Path` — writes
  `governance/semantic-model.osi.yaml` (sorted, `sort_keys=False`, our
  ordering).
- `check_drift(output_path=None) -> list[str]` — rebuilds in memory, compares
  to the on-disk file; returns human-readable differences.
- CLI: `python -m brightsmith.infra.osi generate [--zones gold,...]
  [--output PATH]` and `... check` (exit 1 on drift or missing file).

### WP-2 — MCP resource

`BaseMCPServer._all_resources()` gains `brightsmith://semantic-model`
(mime `application/yaml`) served from
`PROJECT_ROOT/governance/semantic-model.osi.yaml` when the file exists —
same conditional pattern as the existing domain-context/glossary resources.

### WP-3 — Docs + changelog

- CHANGELOG `[Unreleased] Added` entry.
- README: one Features bullet + the two CLI commands.
- CLAUDE.md: add `osi` to the infra module list and the artifact path.

## Success Criteria

- [x] `python -m brightsmith.infra.osi generate` against a project with gold
  contracts emits a spec-shaped OSI YAML: `version`, `semantic_model.name`,
  `datasets[].{name,source,primary_key,fields[].expression.dialects}`,
  glossary-fed descriptions + `ai_context.synonyms`, CDE/PII custom extensions
- [x] Relationships from a Mermaid ER model appear with resolved
  `from_columns`/`to_columns`; unresolvable relationships are skipped, logged
- [x] Metrics from a domain metrics file appear as OSI metrics (ANSI_SQL)
- [x] Re-export with unchanged inputs is byte-identical (D4); `check` detects
  a hand-edit or a contract change (drift → exit 1)
- [x] Zero gold contracts → loud `OSIExportError`, not an empty file (D7)
- [x] MCP clients see `brightsmith://semantic-model` iff the file exists
- [x] Full suite green, ruff clean, pyright clean; no-swallowed-exceptions and
  connection-leak meta-tests still pass
- [x] Staff-engineer review completed and APPROVED before COMPLETE

## Testing Impact Analysis

- **Existing tests at risk:** `tests/mcp/test_base_mcp_server.py` (resource
  listing counts — low risk: new resource is conditional on a file that tests
  don't create). No other module is modified.
- **Authorized test modifications:** none expected; escalate if any existing
  test needs changing.
- **New tests:** `tests/infra/test_osi.py` (composer, glossary/relationship/
  metric mapping, determinism, drift, loud-failure paths, CLI) +
  `tests/mcp/test_base_mcp_server.py` additions (resource present/absent).

## Implementation Log

| Item | Status |
|------|--------|
| WP-1 exporter + CLI | ✅ `src/brightsmith/infra/osi.py` |
| WP-2 MCP resource | ✅ `base_mcp_server.py::_all_resources` |
| WP-3 docs | ✅ CHANGELOG, README, CLAUDE.md |
| WP-4 `get_semantic_model` tool (follow-up) | ✅ `base_mcp_server.py::_handle_get_semantic_model` |
| Tests | ✅ `tests/infra/test_osi.py`, `tests/mcp/test_base_mcp_server.py` |

Deviations: none.

## Verification

```
uv run ruff check src tests
uv run pyright
uv run python -m pytest tests/ -q
```

Results: ruff clean · pyright 0 errors · **854 passed** in 118s (suite was 825
before this spec; +29 tests: 27 in `tests/infra/test_osi.py`, 2 in
`tests/mcp/test_base_mcp_server.py`). No-swallowed-exceptions and
connection-leak meta-tests green.

## Reviews

- **Staff engineer review: APPROVED** (2026-07-03, faang-staff-engineer agent).
  Verified empirically: determinism claim (D4) holds through the single shared
  render path and sorted collections; metrics-hint paths are project-root
  resolved (no CWD dependency); glossary keys match the canonical schema;
  MCP resource tests are not theater (live-config re-import inside
  `_all_resources`); all exception handlers narrowed; no hardcoded entity data.
  Two non-blocking findings, both closed same-session:
  1. Glossary with a valid-JSON-but-non-dict top level leaked a raw
     `AttributeError` instead of `OSIExportError` → `isinstance` guard added
     + test (`test_glossary_non_dict_top_level_raises`).
  2. Corrupt contract in ANY zone blocks a gold-only export → **confirmed
     intended**: an unreadable contract reports `table="?"` so its zone is
     unknowable; refusing to export while a contract is unreadable is the
     loud-failure doctrine working as designed.
  Reviewer questions answered: `check` must mirror `generate`'s `--zones`
  (documented in `check_drift`'s docstring); duplicate metric names now raise
  `OSIExportError` + test (`test_duplicate_metric_names_raise`).
- **Governance post-implementation check: PASS** (scope-adjusted — this is a
  framework spec producing no data tables, so DQ-rule/warehouse checks are
  N/A). Verified: spec exists and matches implementation; CHANGELOG entry
  present; CLAUDE.md key paths updated; the emitted artifact is
  generated-only (never hand-edited) with a drift gate enforcing that; loud
  failure semantics tested for every malformed-input path (D7).

## Follow-up (2026-07-03): `get_semantic_model` tool (WP-4)

MCP resources are client-pulled and not every client auto-attaches them, so a
model could reach its first query without ever having read
`brightsmith://semantic-model`. Human-approved follow-up: a framework **tool**
guarantees the model can fetch semantic context mid-conversation.

- `get_semantic_model` added to `BaseMCPServer._all_tools()`; handler
  `_handle_get_semantic_model` follows the advisory-tool pattern
  (structured `{"error": ...}` / generate-hint message, never a handler crash;
  narrow `except (OSError, yaml.YAMLError)`).
- Unscoped call → full document including model-level `ai_context`.
- Scoped call (`table`: `namespace.table` or bare dataset name) → that dataset
  + the relationships touching it + the model's metrics; the model-level
  `ai_context` (full domain-context text, the token hog) is omitted.
- Unknown table → error + `available_datasets`; missing file → message naming
  the `generate` command.
- Tests: `tests/mcp/test_base_mcp_server.py::TestSemanticModelTool` (8 tests:
  registration, unscoped/scoped/bare-name, no-relationship dataset, unknown
  table, missing file, malformed YAML, wrong shape).
- Authorized test modification: `test_base_class_default_tools`'s framework
  tool count updated 5 → 6 (the new tool is a framework tool by design —
  this is the intended behavior change, not a regression).

## Discussion

```
[2026-07-03] @staff-engineer → implementer
Finding 2 (corrupt contract in any zone blocks gold export): confirm intent.
[2026-07-03] implementer → @staff-engineer
Intended. An unreadable contract has an unknowable zone; exporting a public
semantic model while any contract is corrupt would risk publishing a wrong
model. Loud failure with the file path is the correct behavior. Left as-is.
```
