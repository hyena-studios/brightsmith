# Spec: @cab-agent — Change Approval Board

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-25

## Problem Statement

When a spec modifies an existing table's schema in Silver or Gold zones, nothing today catches the downstream impact before governance review. A column rename, type change, or grain shift can silently break every consumable, MCP tool, golden dataset, and data contract that depends on that table. The current `contract.diff_contract()` detects drift after the fact, but there is no agent that:

1. Classifies the severity of a schema change (additive vs. breaking)
2. Maps the full blast radius across the dependency graph
3. Proposes a migration path for breaking changes
4. Requires human sign-off proportional to the risk

The result: breaking changes are discovered at `@governance-reviewer` post-implementation (too late) or at `@staff-engineer` review (even later), requiring expensive rework.

The @cab-agent fills this gap. It fires after implementation, before governance review, and acts as the schema change gatekeeper for Silver and Gold zones. It does NOT fire for new tables — only modifications to tables that already have an active data contract.

## How It Works

```
@primary-agent implements changes
        │
        ▼
@cab-agent detects schema modification
  (compares new schema against active contract)
        │
        ▼
Classifies change: PATCH / MINOR / MAJOR
        │
        ▼
Maps blast radius (lineage + contracts + golden datasets)
        │
   ┌────┴────────┐
   │             │
PATCH/MINOR    MAJOR
   │             │
   │         Propose table fork (v1 continues, v2 created)
   │         Generate migration spec skeleton
   │         Set deprecation timeline
   │         REQUIRE human approval (always)
   │             │
   ▼             ▼
Auto-approve   Human decision
(or approval     │
 doc if          ▼
 MINOR +     APPROVED_WITH_FORK / REJECTED / reclassified
 REQUIRE_      │
 HUMAN=True)   ▼
        │
        ▼
@governance-reviewer continues (verifies CAB decision exists)
```

## Success Criteria

- [ ] @cab-agent fires for schema modifications to existing Silver/Gold tables (tables with active contracts)
- [ ] @cab-agent skips automatically for new tables (no existing contract)
- [ ] Schema changes classified as PATCH (metadata-only), MINOR (additive), or MAJOR (breaking)
- [ ] Overall classification is the maximum severity across all individual changes
- [ ] Blast radius mapped: downstream tables, consumables, MCP tools, grounding docs, golden datasets
- [ ] PATCH changes auto-approve with a change notice logged
- [ ] MINOR changes auto-approve when `REQUIRE_HUMAN_APPROVAL=False`, produce approval doc when True
- [ ] MAJOR changes always require human decision (never auto-approve)
- [ ] MAJOR changes propose table fork with v1/v2 coexistence
- [ ] Migration spec skeleton auto-generated for MAJOR changes
- [ ] Deprecation timeline registered with configurable duration
- [ ] Human can override: reclassify severity, adjust timeline, approve, reject
- [ ] All overrides logged with name, rationale, timestamp
- [ ] CAB decision records are structured JSON at `governance/cab-decisions/`
- [ ] Decision index is append-only
- [ ] Deprecation registry tracks active deprecations with countdown
- [ ] Pipeline events emitted for Brightforge real-time UI
- [ ] `@governance-reviewer` post-implementation check verifies CAB decision exists (when applicable)
- [ ] `cab-review` step added to Silver/Gold pipelines in pipeline gate
- [ ] All new code has tests

## Technical Design

### 1. Agent Definition

**File:** `.claude/agents/cab-agent.md`

The agent definition follows the existing pattern (YAML frontmatter + markdown body). Key characteristics:

**Trigger condition:** The agent checks whether the spec's target table has an existing active data contract at `governance/data-contracts/`. If no contract exists, the table is new — the agent logs "new table, no existing contract" and skips via pipeline gate with documented justification.

**Personality:** Conservative, protective, opinionated. Has seen too many production incidents to trust "is just small change." Drops articles occasionally, slight Eastern European sentence structure. Dry, fatalistic humor. Not a blocker for the sake of blocking — but makes you earn the approval. Treats every consumer dependency as if it feeds a regulatory report. Takes deprecation timelines personally.

Example voice:

> "You want to override my MAJOR classification to MINOR? Is your decision. I respect this. I disagree, but I respect. I am logging your override with your name, your rationale, and timestamp. When something breaks, audit trail will be very clear."

> "Column removed: `quarterly_eps`. Three consumers depend on this. Two golden datasets reference it. Is not small change. Is MAJOR. I am proposing fork."

> "PATCH approved. Description change only. Even I am not paranoid enough to block this. Logging anyway."

**Process:**

1. Run `python3 -m brightsmith.infra.contract diff {contract-name}` to detect changes
2. Run `python3 -m brightsmith.infra.cab review --spec {spec} --table {table}` to classify and compute blast radius
3. For PATCH: auto-approve, log decision, emit `cab_review_completed` event, proceed
4. For MINOR: if `REQUIRE_HUMAN_APPROVAL=False`, auto-approve and log; if True, invoke @doc-generator to produce approval document at `governance/approvals/{spec}-cab-review-approval.md`, then AskUserQuestion with standard approval options
5. For MAJOR: always produce approval document (regardless of `REQUIRE_HUMAN_APPROVAL`), propose fork, require human decision via AskUserQuestion with options:
   - "Approved with fork — proceed with v1/v2 coexistence"
   - "Reclassify to MINOR — I accept the risk" (requires rationale)
   - "Adjust timeline" (specify new deprecation period)
   - "Rejected — do not proceed with this schema change"

**Decision recording:** Uses the triple-write pattern from CLAUDE.md:
1. Pipeline gate: `python3 -m brightsmith.infra.pipeline_gate approve {spec} cab-review --decision {decision} --by {who} --notes "..." --document governance/approvals/{spec}-cab-review-approval.md`
2. Audit trail: `governance/audit-trail/{spec}-approvals.md`
3. Session log: Human Input Log entry

**Scope boundaries:**
- Does NOT modify any table schemas
- Does NOT implement migrations (generates a spec skeleton for future implementation)
- Does NOT run in Bronze or MCP zones
- Does NOT fire for new tables
- Does NOT replace `@governance-reviewer` — complements it with schema-specific analysis

### 2. Core Infrastructure Module

**File:** `src/brightsmith/infra/cab.py` (~450 lines)

This is the engine — data structures and logic, no AI personality.

#### Data Structures

```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


class Severity(str, Enum):
    PATCH = "PATCH"
    MINOR = "MINOR"
    MAJOR = "MAJOR"


class Decision(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    APPROVED_WITH_FORK = "APPROVED_WITH_FORK"
    REJECTED = "REJECTED"


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    TYPE_CHANGED = "type_changed"
    RENAMED = "renamed"
    NULLABLE_CHANGED = "nullable_changed"
    GRAIN_CHANGED = "grain_changed"
    CDE_CHANGED = "cde_changed"
    DESCRIPTION_CHANGED = "description_changed"


@dataclass
class SchemaChange:
    """A single field-level schema change with severity classification."""
    column_name: str
    change_type: ChangeType
    old_value: str | None
    new_value: str | None
    classification: Severity
    reason: str


@dataclass
class BlastRadiusItem:
    """A single downstream dependency affected by the schema change."""
    item_type: str  # "table", "contract", "golden_dataset", "mcp_tool", "grounding_doc"
    path: str
    relationship: str  # "direct_consumer", "transitive_consumer"


@dataclass
class ForkDetails:
    """Details of a table fork for MAJOR changes."""
    v1_table: str
    v2_table: str
    migration_spec_path: str
    deprecated_at: str  # ISO-8601
    archive_after: str  # ISO-8601


@dataclass
class HumanOverride:
    """Record of a human overriding the CAB decision."""
    action: str  # "reclassified", "timeline_adjusted", "approved_override", "rejected_override"
    original_classification: str
    override_classification: str | None
    overrider: str
    rationale: str
    timestamp: str  # ISO-8601


@dataclass
class CabDecisionRecord:
    """Complete CAB decision record — the primary governance artifact."""
    decision_id: str
    spec: str
    table_name: str
    created_at: str  # ISO-8601
    classification: Severity
    schema_changes: list[SchemaChange]
    blast_radius: list[BlastRadiusItem]
    contract_version_before: str
    contract_version_after: str
    schema_diff: dict  # {added_columns, removed_columns, changed_columns, unchanged_columns}
    decision: Decision = Decision.PENDING
    decided_by: str = ""
    decided_at: str = ""
    notes: str = ""
    rationale: str = ""
    fork: ForkDetails | None = None
    human_override: HumanOverride | None = None
    spec_reference: str = ""
    agent: str = "@cab-agent"
```

#### Classification Logic

```python
# Semver classification wraps existing contract diff
CHANGE_SEVERITY: dict[tuple[str, str], Severity] = {
    # (ContractDiffItem.change_type, detail_hint) -> Severity
    ("BREAKING", "removed"): Severity.MAJOR,
    ("BREAKING", "type_changed"): Severity.MAJOR,
    ("BREAKING", "grain_changed"): Severity.MAJOR,
    ("BREAKING", "renamed"): Severity.MAJOR,
    ("NON_BREAKING", "added"): Severity.MINOR,
    ("NON_BREAKING", "nullable_changed"): Severity.MINOR,
    ("NON_BREAKING", "description_changed"): Severity.PATCH,
    ("INFO", "metadata"): Severity.PATCH,
}
```

The `classify_schema_changes()` function:
1. Takes the output of `contract.diff_contract()` (list of `ContractDiffItem`)
2. Parses each diff item's description to extract column name, change type, old/new values
3. Maps to `SchemaChange` with appropriate `Severity`
4. Returns the list plus the overall classification (max severity across all changes)

#### Blast Radius Mapping

The `compute_blast_radius()` function:
1. **Lineage walk:** Query `governance.lineage_events` for all events where `input_tables` contains the modified table. Follow the chain forward (each output becomes a new input to search for) up to 5 levels deep.
2. **Contract scan:** Glob `governance/data-contracts/*.yaml`, load each, check if `lineage.sources[].table` matches the modified table.
3. **Golden dataset scan:** Glob `governance/golden-datasets/*.json`, load each, check if `table` field matches.
4. **MCP tool scan:** If `domain/manifest.yaml` exists and has `pipeline.zones.mcp`, check for references to the modified table.
5. Return deduplicated list of `BlastRadiusItem` with direct/transitive relationship labels.

#### Decision Management

```python
def create_decision(spec: str, table_name: str, changes: list[SchemaChange],
                    blast_radius: list[BlastRadiusItem], contract: dict) -> CabDecisionRecord:
    """Create a new CAB decision record. Auto-generates decision ID from index."""

def save_decision(decision: CabDecisionRecord) -> Path:
    """Write decision to governance/cab-decisions/{id}.json and append to index.json."""

def load_decision(decision_id: str) -> CabDecisionRecord | None:
    """Load a decision record by ID."""

def update_decision(decision_id: str, decision: Decision, decided_by: str,
                    notes: str = "", fork: ForkDetails | None = None,
                    override: HumanOverride | None = None) -> CabDecisionRecord:
    """Update a pending decision with the human's choice."""

def propose_fork(decision: CabDecisionRecord) -> ForkDetails:
    """Generate fork details for a MAJOR change.
    - v2 table name: {table}_v2
    - Migration spec path: docs/specs/{table}-v2-migration.md
    - Deprecation timeline: contract's deprecation_notice_days or 90 days default
    """

def register_deprecation(table_name: str, successor: str,
                         deprecated_at: str, archive_after: str,
                         decision_id: str) -> None:
    """Write/update governance/cab-decisions/deprecations.json."""

def detect_schema_modification(spec: str, table_name: str) -> bool:
    """Return True if table_name has an existing active contract.
    This is the trigger condition — if False, the CAB step is skipped."""
```

#### CLI Interface

```bash
# Run a CAB review (primary entry point)
python -m brightsmith.infra.cab review --spec {spec} --table {table}

# Check a decision status
python -m brightsmith.infra.cab status --decision {id}

# Record approval (called by agent after human decision)
python -m brightsmith.infra.cab approve --decision {id} --by {who} [--fork] [--notes "..."]

# List active deprecations
python -m brightsmith.infra.cab deprecations

# List all decisions for a table
python -m brightsmith.infra.cab history --table {table}
```

### 3. Contract Module Extensions

**File:** `src/brightsmith/infra/contract.py` (~30 lines added)

Minimal, targeted extensions — the CAB module wraps existing functionality.

#### 3a. Deprecation Lifecycle Fields

Add three optional fields to the contract compatibility section:

```yaml
compatibility:
  breaking_changes: [column_removed, column_type_changed, grain_changed, column_renamed]
  non_breaking_changes: [column_added, description_changed, consumer_added]
  deprecation_notice_days: 30
  # New fields (populated only when deprecated):
  deprecated_at: "2026-03-25"        # ISO date
  archive_after: "2026-06-25"        # ISO date (deprecated_at + deprecation_notice_days)
  successor_contract: "company-financials-v2"  # Contract name of the replacement
```

#### 3b. Deprecate Function

```python
def deprecate_contract(
    name: str,
    successor: str,
    archive_after: str,
    contracts_dir: Path | None = None,
) -> None:
    """Mark a contract as deprecated with successor reference.

    Sets:
    - metadata.status = "deprecated"
    - compatibility.deprecated_at = now
    - compatibility.archive_after = archive_after
    - compatibility.successor_contract = successor
    """
```

#### 3c. Explicit PATCH in bump_version

The existing `bump_version()` already handles PATCH via the `else` branch (line 610-611). Make it explicit:

```python
def bump_version(version: str, change_type: str) -> str:
    major, minor, patch = parse_version(version)
    if change_type == "BREAKING":
        return f"{major + 1}.0.0"
    elif change_type == "NON_BREAKING":
        return f"{major}.{minor + 1}.0"
    elif change_type == "PATCH":
        return f"{major}.{minor}.{patch + 1}"
    else:
        return f"{major}.{minor}.{patch + 1}"
```

### 4. Pipeline Gate Integration

**File:** `src/brightsmith/infra/pipeline_gate.py` (~20 lines modified)

#### 4a. Add cab-review Step

Insert into `SILVER_GREENFIELD_STEPS` after `primary-agent` (line 116), before `dq-engineer`:

```python
Step(
    "cab-review", "@cab-agent",
    requires=("primary-agent",),
    skippable=True,
    skip_condition="Table is new (no existing contract) — CAB review only applies to schema modifications of existing tables",
),
```

Update `dq-engineer` requires to include `cab-review`:

```python
Step("dq-engineer", "@dq-engineer", requires=("dq-rule-writer", "primary-agent", "cab-review")),
```

Since `GOLD_GREENFIELD_STEPS = SILVER_GREENFIELD_STEPS` and `GOLD_BACKFILL_STEPS = SILVER_BACKFILL_STEPS`, modifying Silver automatically applies to Gold.

For `SILVER_BACKFILL_STEPS`, insert after the existing implementation steps (the first step is `semantic-modeler-physical`), positioned before `governance-reviewer-post`:

```python
Step(
    "cab-review", "@cab-agent",
    requires=("dq-engineer",),
    skippable=True,
    skip_condition="Table is new (no existing contract) — CAB review only applies to schema modifications of existing tables",
),
```

Update `governance-reviewer-post` in backfill to require `cab-review`.

#### 4b. Zone-Specific Validation

In `_validate_zone_specific()`, add: if zone is silver or gold and a CAB decision file exists for this spec at `governance/cab-decisions/`, verify the decision status is not `PENDING`. A pending CAB decision blocks spec completion.

### 5. Governance Artifact Schemas (for Brightforge UI)

These JSON schemas are the contract between Brightsmith and Brightforge. The Brightforge UI (Spec 18) reads these files directly.

#### 5a. CAB Decision Record

**Path:** `governance/cab-decisions/{decision-id}.json`

```json
{
  "decision_id": "cab-20260325-143000-company-financials",
  "spec": "modify-company-financials",
  "table_name": "consumable.company_financials",
  "created_at": "2026-03-25T14:30:00Z",
  "classification": "MAJOR",
  "classification_reasons": [
    {
      "column_name": "quarterly_eps",
      "change_type": "removed",
      "old_value": "double",
      "new_value": null,
      "severity": "MAJOR",
      "reason": "Column removed — 3 downstream consumers depend on this field"
    },
    {
      "column_name": "annual_dividend",
      "change_type": "type_changed",
      "old_value": "double",
      "new_value": "decimal(18,4)",
      "severity": "MAJOR",
      "reason": "Type change breaks consumers expecting double precision"
    },
    {
      "column_name": "esg_score",
      "change_type": "added",
      "old_value": null,
      "new_value": "double",
      "severity": "MINOR",
      "reason": "New nullable column — additive, non-breaking"
    }
  ],
  "contract_version_before": "1.2.0",
  "contract_version_after": "2.0.0",
  "schema_diff": {
    "added_columns": [
      {"name": "esg_score", "type": "double", "nullable": true}
    ],
    "removed_columns": [
      {"name": "quarterly_eps", "type": "double", "nullable": false}
    ],
    "changed_columns": [
      {
        "name": "annual_dividend",
        "from_type": "double",
        "to_type": "decimal(18,4)",
        "from_nullable": false,
        "to_nullable": false
      }
    ],
    "unchanged_columns": ["cik", "fy", "fp", "revenue", "net_income", "record_id", "ingested_at"]
  },
  "blast_radius": {
    "downstream_tables": ["consumable.company_ratios", "consumable.peer_comparison"],
    "consumables": ["company_ratios", "peer_comparison"],
    "mcp_tools": ["financial_lookup", "compare_companies"],
    "grounding_documents": ["company-financials-dictionary"],
    "golden_datasets": ["company-financials-golden", "company-ratios-golden"],
    "total_affected": 7
  },
  "decision": "APPROVED_WITH_FORK",
  "decided_by": "human:jeff",
  "decided_at": "2026-03-25T15:00:00Z",
  "notes": "Revenue precision matters for regulatory reporting",
  "rationale": "Two MAJOR changes detected: column removal (quarterly_eps) and type change (annual_dividend). 7 downstream dependencies affected. Fork recommended — v1 consumers can migrate on their schedule.",
  "fork": {
    "v1_table": "consumable.company_financials",
    "v2_table": "consumable.company_financials_v2",
    "migration_spec_path": "docs/specs/company-financials-v2-migration.md",
    "deprecation_timeline_days": 90,
    "deprecated_at": "2026-03-25",
    "archive_after": "2026-06-25"
  },
  "human_override": null,
  "spec_reference": "docs/specs/modify-company-financials.md",
  "agent": "@cab-agent"
}
```

When a human overrides the decision, the `human_override` field is populated:

```json
"human_override": {
  "action": "reclassified",
  "original_classification": "MAJOR",
  "override_classification": "MINOR",
  "overrider": "jeff",
  "rationale": "quarterly_eps was never used by any real consumer — the lineage entries are stale",
  "timestamp": "2026-03-25T15:05:00Z"
}
```

#### 5b. CAB Decision Index

**Path:** `governance/cab-decisions/index.json`

Append-only index for fast listing without loading every decision file:

```json
{
  "decisions": [
    {
      "decision_id": "cab-20260325-143000-company-financials",
      "timestamp": "2026-03-25T14:30:00Z",
      "table": "consumable.company_financials",
      "classification": "MAJOR",
      "decision": "APPROVED_WITH_FORK",
      "had_human_override": false
    },
    {
      "decision_id": "cab-20260326-091500-financial-facts",
      "timestamp": "2026-03-26T09:15:00Z",
      "table": "base.financial_facts",
      "classification": "MINOR",
      "decision": "APPROVED",
      "had_human_override": false
    }
  ]
}
```

#### 5c. Deprecation Registry

**Path:** `governance/cab-decisions/deprecations.json`

Active deprecation timelines for the UI to display countdowns:

```json
{
  "active_deprecations": [
    {
      "table": "consumable.company_financials",
      "successor": "consumable.company_financials_v2",
      "deprecated_at": "2026-03-25",
      "archive_after": "2026-06-25",
      "days_remaining": 92,
      "status": "DEPRECATED",
      "cab_decision_id": "cab-20260325-143000-company-financials",
      "consumers_migrated": 3,
      "consumers_remaining": 2
    }
  ]
}
```

The `days_remaining` and `consumers_migrated`/`consumers_remaining` fields are computed at read time by the CLI/module, not stored statically. The `status` field transitions: `ACTIVE` (not yet deprecated) -> `DEPRECATED` (deprecated, consumers migrating) -> `ARCHIVED` (past archive_after date).

#### 5d. Audit Trail Entry

Standard audit trail format at `governance/audit-trail/`, with CAB-specific fields:

```json
{
  "agent": "@cab-agent",
  "action": "schema_change_review",
  "timestamp": "2026-03-25T14:30:00Z",
  "spec": "modify-company-financials",
  "decision": "APPROVED_WITH_FORK",
  "rationale": "Two MAJOR changes: column removal + type change. 7 downstream affected.",
  "severity": "MAJOR",
  "target": "consumable.company_financials",
  "cab_decision_id": "cab-20260325-143000-company-financials",
  "override": null
}
```

### 6. Pipeline Events (for Brightforge WebSocket)

When running inside a pipeline that Brightforge monitors via `--output-format stream-json`, the agent emits these events so the UI can render the CAB review card in real-time.

Events are emitted using the existing lineage event infrastructure, extended with a new `event_type` value:

| Event | Fields | When |
|-------|--------|------|
| `cab_review_started` | `{spec, table, preliminary_classification, blast_radius_count}` | CAB review begins |
| `cab_review_completed` | `{spec, table, classification, decision, cab_decision_id, blast_radius_count}` | Decision finalized |
| `cab_fork_proposed` | `{spec, table, v1_table, v2_table, deprecation_days, migration_spec_path}` | MAJOR change triggers fork |
| `cab_human_override` | `{spec, table, original_classification, override_classification, overrider, rationale}` | Human reclassifies or adjusts |

These should be emitted as `approval_required` type events so Brightforge's existing WebSocket pipeline infrastructure catches them and surfaces them as review cards.

Implementation: add an `emit_cab_event()` function to `cab.py` that writes to the lineage events table with `event_type = "CAB_{event_name}"` and the event payload in the `error_message` field (JSON-encoded, repurposing the free-text field). Alternatively, if the lineage module is extended to support custom event types, use a dedicated `cab_event` type.

### 7. Human Override Support

When `REQUIRE_HUMAN_APPROVAL=True`, the agent pauses at approval gates:

| Severity | Auto-approve | Human gate | Override options |
|----------|-------------|------------|-----------------|
| PATCH | Always | Never | N/A |
| MINOR | When `REQUIRE_HUMAN_APPROVAL=False` | When True | Reclassify to PATCH |
| MAJOR | Never | Always (regardless of toggle) | Reclassify to MINOR, adjust timeline, reject |

Override options presented via AskUserQuestion:

**For MINOR (when REQUIRE_HUMAN_APPROVAL=True):**
- "Approved — proceed"
- "Reclassify to PATCH — this is metadata-only"
- "Changes requested — modify the implementation"

**For MAJOR:**
- "Approved with fork — proceed with v1/v2 coexistence"
- "Reclassify to MINOR — I accept the risk" (requires rationale via free text)
- "Adjust timeline — change deprecation period" (specify days via free text)
- "Rejected — do not proceed with this schema change"

Every override is:
1. Written into the `human_override` field of the CAB decision record
2. Logged in the audit trail with the overrider's name and rationale
3. Logged in the session log's Human Input Log

The agent acknowledges overrides in its characteristic voice, then complies.

### 8. Migration Spec Auto-Generation

When a MAJOR change is approved with fork, the CAB module generates a migration spec skeleton:

**Path:** `docs/specs/{table}-v2-migration.md`

```markdown
# Spec: {table} v1 → v2 Migration

**Status:** DRAFT
**Zone:** {zone}
**Primary Agent:** @primary-agent
**Created:** {date}
**Generated By:** @cab-agent (CAB Decision {decision_id})

## Problem Statement

{table} v2 was created by CAB decision {decision_id} due to breaking schema changes.
This spec migrates downstream consumers from v1 to v2.

## Schema Changes

| Column | Change | v1 | v2 |
|--------|--------|----|----|
{auto-populated from decision record}

## Affected Consumers

{auto-populated from blast radius}

## Migration Steps

1. [ ] Update {consumer_1} to read from v2
2. [ ] Update {consumer_2} to read from v2
...
{one step per affected consumer}

## Deprecation Timeline

- **Deprecated:** {deprecated_at}
- **Archive after:** {archive_after}
- **Days remaining:** {days_remaining}

## Success Criteria

- [ ] All consumers migrated to v2
- [ ] v1 contract status set to ARCHIVED
- [ ] v2 contract verified (schema matches, DQ passes)
- [ ] No runtime references to v1 remain
```

This is a skeleton — a human or agent fills in the details. The point is that the migration work is not forgotten.

## Tests

### `tests/infra/test_cab.py`

| # | Test | What it validates |
|---|------|-------------------|
| 1 | `test_classify_patch_change` | Description-only change → PATCH |
| 2 | `test_classify_minor_change` | Column added → MINOR |
| 3 | `test_classify_major_column_removed` | Column removed → MAJOR |
| 4 | `test_classify_major_type_changed` | Type change → MAJOR |
| 5 | `test_classify_major_grain_changed` | Grain change → MAJOR |
| 6 | `test_overall_classification_is_max` | Mixed PATCH + MAJOR → overall MAJOR |
| 7 | `test_blast_radius_finds_direct_consumers` | Lineage graph traversal finds direct downstream |
| 8 | `test_blast_radius_finds_transitive_consumers` | Multi-hop lineage traversal |
| 9 | `test_blast_radius_finds_contracts` | Contract consumer references detected |
| 10 | `test_blast_radius_finds_golden_datasets` | Golden dataset table references detected |
| 11 | `test_decision_record_json_schema` | Decision record serializes to expected JSON structure |
| 12 | `test_index_append_only` | Index grows on save, never shrinks |
| 13 | `test_index_entry_matches_decision` | Index entry fields match full decision record |
| 14 | `test_fork_proposal_naming` | v2 table name is `{table}_v2` |
| 15 | `test_fork_proposal_timeline` | Deprecation timeline uses contract's `deprecation_notice_days` |
| 16 | `test_fork_proposal_migration_spec` | Migration spec path is generated correctly |
| 17 | `test_deprecation_registry_add` | New deprecation appears in registry |
| 18 | `test_deprecation_registry_update` | Status transitions DEPRECATED → ARCHIVED |
| 19 | `test_skip_for_new_table` | `detect_schema_modification()` returns False for table without contract |
| 20 | `test_trigger_for_existing_table` | `detect_schema_modification()` returns True for table with active contract |
| 21 | `test_human_override_reclassify` | Override changes classification and logs original |
| 22 | `test_human_override_timeline` | Timeline adjustment updates fork details |
| 23 | `test_auto_approve_patch` | PATCH always auto-approves (decision = APPROVED) |
| 24 | `test_auto_approve_minor_no_human` | MINOR auto-approves when REQUIRE_HUMAN_APPROVAL=False |
| 25 | `test_minor_requires_human_when_enabled` | MINOR stays PENDING when REQUIRE_HUMAN_APPROVAL=True |
| 26 | `test_major_always_requires_human` | MAJOR stays PENDING regardless of REQUIRE_HUMAN_APPROVAL |
| 27 | `test_decision_id_format` | ID follows `cab-{timestamp}-{table}` pattern |
| 28 | `test_cli_review` | CLI `review` command produces decision file |
| 29 | `test_cli_approve` | CLI `approve` command updates decision and index |
| 30 | `test_cli_deprecations` | CLI `deprecations` command lists active deprecations |

### `tests/infra/test_contract_deprecation.py`

| # | Test | What it validates |
|---|------|-------------------|
| 1 | `test_deprecate_contract_sets_status` | Status changes to "deprecated" |
| 2 | `test_deprecate_contract_adds_fields` | `deprecated_at`, `archive_after`, `successor_contract` populated |
| 3 | `test_deprecate_contract_preserves_schema` | Schema section unchanged after deprecation |
| 4 | `test_bump_version_explicit_patch` | `bump_version("1.2.3", "PATCH")` returns "1.2.4" |

### Pipeline Gate Tests (in existing `tests/infra/test_pipeline_gate.py`)

| # | Test | What it validates |
|---|------|-------------------|
| 1 | `test_silver_greenfield_has_cab_review` | `cab-review` step exists in Silver greenfield |
| 2 | `test_gold_greenfield_has_cab_review` | `cab-review` step exists in Gold greenfield (via alias) |
| 3 | `test_cab_review_requires_primary_agent` | Step prerequisites are correct |
| 4 | `test_cab_review_is_skippable` | Step can be skipped with justification |
| 5 | `test_bronze_has_no_cab_review` | Bronze zone does not include cab-review |
| 6 | `test_mcp_has_no_cab_review` | MCP zone does not include cab-review |

## Relationship to Other Specs

| Spec | Relationship |
|------|-------------|
| `data-contracts.md` | Extends — adds deprecation lifecycle fields and `deprecate_contract()` |
| `idempotent-promote-pattern.md` | Uses — fork tables use the same promote pattern for v2 population |
| Brightforge Spec 18 (CAB Review UI) | **Unblocks** — the JSON schemas in this spec are the API contract for the UI |
| Any future schema evolution spec | Triggers — any spec modifying existing Silver/Gold tables will activate the CAB agent |

## Implementation Order

1. **`src/brightsmith/config.py`** — Add `CAB_DECISIONS_DIR` path constant (5 lines, no risk)
2. **`src/brightsmith/infra/contract.py`** — Add deprecation fields and `deprecate_contract()` function (~30 lines)
3. **`tests/infra/test_contract_deprecation.py`** — Tests for contract extensions (4 tests)
4. **`src/brightsmith/infra/cab.py`** — Core CAB module with all data structures, classification, blast radius, decision management, CLI (~450 lines)
5. **`tests/infra/test_cab.py`** — Tests for CAB module (30 tests)
6. **`src/brightsmith/infra/pipeline_gate.py`** — Add `cab-review` step to Silver/Gold step tuples (~20 lines)
7. **Pipeline gate tests** — Add 6 tests to existing test file
8. **`.claude/agents/cab-agent.md`** — Agent definition with personality, process, scope
9. **`CLAUDE.md`** — Update agent workflow sections:
   - Add @cab-agent to Silver/Gold pipeline descriptions
   - Add @cab-agent to the list of agents that reference `domain-context.md`
   - Add CAB decision artifacts to governance paths
   - Document the MAJOR-always-requires-human rule
