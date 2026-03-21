# Framework Spec: Lineage Maturity

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-20

## Problem Statement

Brightsmith has solid runtime lineage emission — `emit_start()`, `emit_complete()`, `emit_fail()` write to a `governance.lineage_events` Iceberg table, `BaseIngestor` auto-emits for bronze, and `promote()` auto-emits for silver/gold. The hard part is done.

But everything downstream of emission is missing or broken. The SEC EDGAIR project revealed the gap: it needed a CLI to query lineage events, a doc generator to produce governance JSON from runtime data, column-level lineage, contract integration, and verification logic. All of that was built as domain-specific code in EDGAIR — but none of it is domain-specific. It's all framework-level infrastructure.

### What Exists (Good)

| Capability | Status |
|---|---|
| Runtime emission API (`emit_start/complete/fail`) | Complete, fault-tolerant, tested |
| Iceberg-backed event storage (`governance.lineage_events`) | Complete, 17-field schema |
| Auto-emission in `BaseIngestor.ingest()` | Complete |
| Auto-emission in `promote()` | Complete |
| Metadata: snapshot ID, row count, dedup count, DQ metrics, duration | Complete |
| @lineage-tracker agent definition | Exists (verification role defined) |

### What's Missing (The Gap)

| Capability | Current State | EDGAIR Had It? |
|---|---|---|
| **CLI to query lineage events** | No CLI at all | Yes — `lineage status`, `lineage generate-docs` |
| **Governance doc generation** from runtime events | `governance/lineage/` is always empty | Yes — 13 JSON files auto-updated from Iceberg |
| **Column-level lineage** | Not captured — table-level only | Yes — every output field maps to input fields + transformation type |
| **Transformation step details** | Not captured | Yes — ordered step descriptions in facets |
| **Agent attribution in lineage** | Not captured | Yes — which agent, why |
| **Spec reference in lineage** | Not captured | Yes — links to spec file and version |
| **DQ facet in lineage docs** | Not in docs (only in Iceberg events) | Yes — rule counts, file reference, pass status |
| **MCP `get_lineage` tool** reads Iceberg | Reads empty `governance/lineage/` files | Should query Iceberg table directly |
| **Contract `lineage.sources`** populated | Always `[]` | Should auto-populate from lineage events |
| **Verification logic** in @lineage-tracker | Defined in agent doc, not implemented | Implicit in the pipeline |
| **Completeness gate** | No enforcement | Governance-reviewer checklist mentions it |

The core problem: Brightsmith emits runtime lineage events to Iceberg but has no way to query them, no way to turn them into governance documents, no way to capture column-level transformations, and no enforcement that lineage is complete before a spec is marked done.

## What Changes

Seven changes, ordered by dependency:

### Change 1: Lineage CLI

**File:** `src/brightsmith/infra/lineage.py` (extend existing module)

Add CLI commands to the existing lineage module:

```bash
# Show latest event per job — formatted table
python -m brightsmith.infra.lineage status

# Show all events for a specific job
python -m brightsmith.infra.lineage history <job_name>

# Show lineage graph — which tables feed which
python -m brightsmith.infra.lineage graph

# Generate/update governance JSON docs from runtime data
python -m brightsmith.infra.lineage generate-docs

# Verify lineage completeness for a spec
python -m brightsmith.infra.lineage verify --spec <spec_name>
```

**`status` command output:**
```
Job                                      Last Run               Rows    Duration  Status
────────────────────────────────────────────────────────────────────────────────────────
ingest:my_source                         2026-03-20 17:55:00    5000      2340ms  COMPLETE
promote:base-facts                       2026-03-20 17:56:12    3200       890ms  COMPLETE
promote:consumable-financials            2026-03-20 17:57:01    1500       450ms  COMPLETE
```

**`graph` command output:**
```
raw.my_source_data
  └─→ base.facts (promote:base-facts)
       └─→ consumable.financials (promote:consumable-financials)
            └─→ [MCP server: 3 tools]
```

The graph is built entirely from `input_tables` and `output_table` in lineage events — no configuration needed.

### Change 2: OpenLineage Facets

**File:** `src/brightsmith/infra/lineage.py` (extend schema and emission)

Add framework-standard facets to lineage events. These replace EDGAIR's `secEdgair_*` facets with domain-agnostic `brightsmith_*` facets.

**New fields on `governance.lineage_events` schema:**

| # | Column | Type | Required | Purpose |
|---|--------|------|----------|---------|
| 18 | spec_reference | String | No | Path to spec file (e.g., `docs/specs/base-facts.md`) |
| 19 | agent_id | String | No | Which agent ran this (e.g., `@primary-agent`) |
| 20 | transformation_steps | String | No | JSON array of step descriptions |

**Updated `emit_start()` and `emit_complete()` signatures:**

```python
def emit_start(
    job_name: str,
    input_tables: list[str],
    output_table: str,
    producer: str,
    spec_reference: str | None = None,     # NEW
    agent_id: str | None = None,           # NEW
) -> str:

def emit_complete(
    run_id: str,
    job_name: str,
    output_table: str,
    producer: str,
    snapshot_id: int | None = None,
    row_count: int | None = None,
    skipped_duplicates: int | None = None,
    dq_passed: int | None = None,
    dq_total: int | None = None,
    dq_p0_passed: bool | None = None,
    duration_ms: int | None = None,
    transformation_steps: list[dict] | None = None,  # NEW
) -> None:
```

**Transformation steps format:**
```python
[
    {"order": 0, "name": "filter_nulls", "description": "Remove rows where entity_id IS NULL"},
    {"order": 1, "name": "dedup", "description": "Grain-based dedup on (entity_id, metric, period)"},
    {"order": 2, "name": "join_dimensions", "description": "Join entity dimension for canonical names"},
]
```

These are optional. Simple promotes (BaseIngestor, plain promote()) don't need them. Complex transformations (conformation, collision resolution, ratio computation) document their steps.

**Backward compatible:** All new fields are optional. Existing `emit_start()` and `emit_complete()` calls continue to work with no changes.

### Change 3: Column-Level Lineage

**File:** `src/brightsmith/infra/lineage.py` (new function)

Column-level lineage captures which source columns feed which target columns and how. This is NOT emitted at runtime (it's structural, not runtime metadata) — it's captured by the @lineage-tracker agent after implementation and stored in governance docs.

**New function:**

```python
def build_column_lineage(
    output_table: str,
    output_fields: list[str],
    mappings: list[ColumnMapping],
) -> dict:
    """Build an OpenLineage columnLineage facet.

    Args:
        output_table: Target table name.
        output_fields: List of output column names.
        mappings: Column-level transformation mappings.

    Returns:
        OpenLineage columnLineage facet dict.
    """
```

**ColumnMapping dataclass:**

```python
@dataclass
class ColumnMapping:
    """Maps one output column to its source columns and transformation."""
    target_field: str
    input_fields: list[dict]       # [{"namespace": "...", "name": "table", "field": "col"}]
    transformation_type: str        # DIRECT | AGGREGATION | DERIVED
    transformation_description: str | None = None
```

**Transformation types (OpenLineage standard):**

| Type | Meaning | Example |
|------|---------|---------|
| `DIRECT` | 1:1 copy, possibly renamed | `source.revenue` → `target.revenue` |
| `AGGREGATION` | Many:1 reduction | `SUM(source.quarterly_revenue)` → `target.annual_revenue` |
| `DERIVED` | Computed from multiple fields | `SHA-256(entity_id, metric, period)` → `target.record_id` |

Column-level lineage is stored in governance docs (Change 4), not in the Iceberg events table. It's structural metadata, not runtime telemetry.

### Change 4: Governance Doc Generation

**File:** `src/brightsmith/infra/lineage.py` (new `generate-docs` CLI command)

The `generate-docs` command reads runtime events from the Iceberg table and produces/updates OpenLineage JSON files in `governance/lineage/`. These are the human-readable governance artifacts that auditors, architects, and downstream agents reference.

**Doc structure (OpenLineage-compatible with Brightsmith facets):**

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-03-20T17:55:00Z",
  "run": {
    "runId": "uuid",
    "facets": {
      "brightsmith_specReference": {
        "specFile": "docs/specs/spec-name.md"
      },
      "brightsmith_agentAttribution": {
        "agentId": "@primary-agent"
      },
      "brightsmith_dataQuality": {
        "rulesFile": "governance/dq-rules/spec-name.json",
        "rulesPassed": 12,
        "rulesTotal": 12,
        "p0Passed": true
      },
      "brightsmith_runtimeMetrics": {
        "lastRunId": "uuid",
        "lastEventTime": "ISO-8601",
        "rowCount": 5000,
        "snapshotId": 1234567890,
        "durationMs": 2340,
        "skippedDuplicates": 150
      }
    }
  },
  "job": {
    "namespace": "project-name",
    "name": "zone.table_name",
    "facets": {
      "documentation": {
        "description": "What this transformation does"
      },
      "sourceCode": {
        "sourceCodeLocation": "src/zone/module.py"
      },
      "brightsmith_transformationDetail": {
        "steps": [...]
      }
    }
  },
  "inputs": [...],
  "outputs": [
    {
      "namespace": "project-name",
      "name": "zone.table_name",
      "facets": {
        "schema": {"fields": [...]},
        "columnLineage": {"fields": {...}}
      }
    }
  ]
}
```

**How it works:**

1. Reads latest COMPLETE event per `job_name` from `governance.lineage_events`
2. For each event, builds the OpenLineage JSON structure:
   - `run.facets` — populated from event metadata (spec_reference, agent_id, DQ metrics, runtime metrics)
   - `job.facets` — populated from event metadata (transformation_steps) + code inspection (source location)
   - `inputs` — populated from `input_tables` field (JSON array of table names)
   - `outputs` — populated from `output_table` + table schema (read from Iceberg catalog)
   - `outputs.columnLineage` — if a column lineage file exists at `governance/lineage/{spec}-columns.json`, merge it in
3. Writes to `governance/lineage/{job-name-slugified}.json`

**Key design: no hardcoded job-to-file mapping.** EDGAIR had a hardcoded dict mapping job names to filenames. The framework uses a slug of the job_name: `base.financial_facts` → `base-financial-facts.json`. If multiple jobs write to the same file (e.g., multi-table promotes), each gets its own file.

**Schema auto-capture:** Output table schemas are read directly from the Iceberg catalog at doc-generation time. No manual schema definition needed. If the table exists, its schema goes into the doc. If it doesn't (e.g., table was dropped), the schema section is omitted.

### Change 5: Lineage Verification

**File:** `src/brightsmith/infra/lineage.py` (new `verify` CLI command)

The `verify` command checks lineage completeness for a spec. This is what @lineage-tracker's verification role actually executes.

```bash
python -m brightsmith.infra.lineage verify --spec base-facts
```

**Checks:**

| Check | Pass Condition | Severity |
|-------|---------------|----------|
| Events exist | At least one COMPLETE event for this spec's job_name | P0 (blocking) |
| Row count present | `row_count > 0` on the latest COMPLETE event | P0 (blocking) |
| Snapshot ID present | `output_snapshot_id IS NOT NULL` | P1 (warning) |
| DQ metrics present | `dq_rules_total > 0` | P1 (warning) |
| Duration recorded | `duration_ms > 0` | P2 (info) |
| No FAIL events after last COMPLETE | Latest event is COMPLETE, not FAIL | P0 (blocking) |
| Governance doc exists | `governance/lineage/{job-name}.json` exists | P1 (warning) |

**Output format:**
```
Lineage verification for spec: base-facts
──────────────────────────────────────────
[PASS] Events exist: 2 events (1 START, 1 COMPLETE)
[PASS] Row count: 5000 rows
[PASS] Snapshot ID: 1234567890
[PASS] DQ metrics: 12/12 rules passed
[PASS] Duration: 2340ms
[PASS] No failures: latest event is COMPLETE
[WARN] Governance doc: governance/lineage/base-facts.json not found (run generate-docs)

Result: PASS (6/7 checks passed, 1 warning)
```

**Integration with pipeline gate:** @lineage-tracker runs `python -m brightsmith.infra.lineage verify --spec {spec}` and records the result via `pipeline_gate complete`. P0 failures block spec completion.

### Change 6: MCP Lineage Tool Fix

**File:** `src/brightsmith/mcp/base_mcp_server.py` (modify `_handle_get_lineage`)

The current `get_lineage` MCP tool reads from `governance/lineage/*.json` files. These files are always empty because nothing writes to them. Fix: query the Iceberg table first, fall back to files.

**New handler logic:**

```python
def _handle_get_lineage(self, input_dict: dict) -> dict:
    """Get lineage for a table. Queries Iceberg events, falls back to governance docs."""
    table_name = input_dict["table"]

    # 1. Try Iceberg lineage_events table (runtime data)
    events = query_lineage_events(table_name)
    if events:
        return self.attach_governance({
            "table": table_name,
            "source": "runtime",
            "latest_event": events[0],  # Most recent COMPLETE
            "input_tables": json.loads(events[0].get("input_tables", "[]")),
            "row_count": events[0].get("row_count"),
            "snapshot_id": events[0].get("output_snapshot_id"),
            "event_count": len(events),
        }, table_name)

    # 2. Fall back to governance/lineage/ files
    ...existing file-based lookup...
```

**New utility function:**

```python
def query_lineage_events(
    table_name: str,
    event_type: str = "COMPLETE",
    limit: int = 10,
) -> list[dict]:
    """Query lineage events for a specific output table.

    Returns events sorted by event_time descending.
    """
```

This function is also useful for contract integration (Change 7) and the lineage graph CLI command.

### Change 7: Contract Lineage Integration

**File:** `src/brightsmith/infra/contract.py` (modify `generate_contract`)

Data contracts have a `lineage.sources` section that's always `[]`. Fix: auto-populate from lineage events when generating contracts.

**Current:**
```yaml
lineage:
  sources: []
```

**After:**
```yaml
lineage:
  sources:
    - table: raw.my_source_data
      relationship: direct_input
    - table: base.entity_mappings
      relationship: dimension_lookup
  latest_run:
    run_id: "uuid"
    event_time: "2026-03-20T17:55:00Z"
    row_count: 5000
    snapshot_id: 1234567890
```

**How:** During `generate_contract()`, query `governance.lineage_events` for the latest COMPLETE event where `output_table` matches the contract's table. Parse `input_tables` JSON array to populate `sources`. Include latest run metadata for freshness context.

## Success Criteria

- [ ] `python -m brightsmith.infra.lineage status` shows formatted table of latest events
- [ ] `python -m brightsmith.infra.lineage history <job>` shows event history for a job
- [ ] `python -m brightsmith.infra.lineage graph` shows table dependency graph
- [ ] `python -m brightsmith.infra.lineage generate-docs` produces OpenLineage JSON in `governance/lineage/`
- [ ] `python -m brightsmith.infra.lineage verify --spec <spec>` validates lineage completeness
- [ ] Generated docs include Brightsmith facets: specReference, agentAttribution, dataQuality, runtimeMetrics, transformationDetail
- [ ] Generated docs include output table schema (auto-captured from Iceberg catalog)
- [ ] `ColumnMapping` dataclass and `build_column_lineage()` function for column-level lineage
- [ ] Column lineage stored in governance docs when provided by @lineage-tracker
- [ ] `emit_start()` and `emit_complete()` accept new optional params (spec_reference, agent_id, transformation_steps)
- [ ] All new params are optional — existing callers unchanged (backward compatible)
- [ ] Schema evolution: `governance.lineage_events` table gains 3 new nullable columns
- [ ] MCP `get_lineage` tool queries Iceberg events, not empty files
- [ ] Contract `lineage.sources` auto-populated from lineage events
- [ ] @lineage-tracker agent definition updated to reference `verify` command
- [ ] Existing tests pass, new tests for CLI commands and verification

## Files to Create

| File | Purpose |
|------|---------|
| `tests/infra/test_lineage_cli.py` | Tests for status, history, graph, generate-docs, verify commands |
| `tests/infra/test_lineage_column.py` | Tests for ColumnMapping and build_column_lineage |
| `tests/infra/test_lineage_verify.py` | Tests for verification checks |

## Files to Modify

| File | What Changes |
|------|-------------|
| `src/brightsmith/infra/lineage.py` | Schema evolution (3 new fields), updated emit signatures, CLI commands (status, history, graph, generate-docs, verify), `query_lineage_events()` utility, `ColumnMapping`, `build_column_lineage()` |
| `src/brightsmith/mcp/base_mcp_server.py` | `_handle_get_lineage` queries Iceberg events instead of empty files |
| `src/brightsmith/infra/contract.py` | `generate_contract` auto-populates lineage.sources from events |
| `.claude/agents/lineage-tracker.md` | Reference `verify` command, column lineage capture role, updated verification protocol |
| `CLAUDE.md` | Add lineage CLI commands to Key Paths, update @lineage-tracker description |
| `tests/infra/test_lineage.py` | Update for new emit_start/emit_complete params |

## Implementation Order

1. Schema evolution — add 3 new nullable columns to `LINEAGE_EVENTS_SCHEMA`
2. Update `emit_start()` and `emit_complete()` signatures (backward compatible)
3. `query_lineage_events()` utility function
4. CLI: `status` command
5. CLI: `history` command
6. CLI: `graph` command
7. `ColumnMapping` dataclass and `build_column_lineage()` function
8. CLI: `generate-docs` command (produces governance JSON with facets + column lineage)
9. CLI: `verify` command
10. MCP `get_lineage` tool fix
11. Contract `lineage.sources` integration
12. Update @lineage-tracker agent definition
13. Tests for everything
14. Update CLAUDE.md

## Design Decisions

### Why not column-level lineage in the Iceberg table?

Column lineage is structural metadata — it describes how a transformation is designed, not how a specific run performed. It doesn't change per execution. Storing it in the runtime events table would duplicate the same column mappings on every run. Instead: column lineage lives in the governance JSON docs (the structural view) and runtime metrics live in the Iceberg table (the operational view). `generate-docs` merges both.

### Why framework facets instead of domain facets?

EDGAIR used `secEdgair_*` facets. The framework uses `brightsmith_*` facets. Same structure, domain-agnostic names. The facet content is populated from whatever the domain provides — spec references, agent IDs, DQ rule files. The framework doesn't assume what a spec looks like or what agents exist; it just records what it's given.

### Why auto-populate contract lineage instead of requiring manual entry?

If lineage events exist in Iceberg and contracts exist for the same table, the relationship is deterministic. The framework can always compute `lineage.sources` from the `input_tables` field. Manual entry adds work and creates drift risk. Auto-population is more reliable and zero-effort.

### Why a slug-based file naming instead of a hardcoded mapping?

EDGAIR's `generate-docs` had a hardcoded `job_to_file` dict — adding a new spec required editing the lineage module. The framework uses `job_name.replace(".", "-") + ".json"` — any job name maps to a filename automatically. No framework changes needed per domain.

## Relationship to Other Specs

- **ai-ready-mcp-server.md**: MCP `get_lineage` tool is fixed to query real data
- **ai-ready-intelligence-layer.md**: System prompt can include lineage summary section
- **data-contracts.md**: Contract `lineage.sources` auto-populated from events
- **headless-pipeline-runner.md**: `lineage verify` can be added to headless pipeline validation
- **framework-quality-parity.md**: Closes a known gap in governance artifact completeness

## Future Considerations

- **OpenLineage server integration** — The JSON docs are OpenLineage-compatible. A future spec could push events to Marquez or DataHub for visualization.
- **Cross-project lineage** — When one Brightsmith project feeds another, lineage events could be federated via shared Iceberg catalogs.
- **Lineage-aware DQ** — DQ rules that reference upstream lineage (e.g., "if source table had DQ failures, flag downstream tables").
