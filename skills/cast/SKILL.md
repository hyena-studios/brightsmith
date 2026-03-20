---
description: Run the Gold zone pipeline — shape data into business-ready products. Use when Silver zone is complete and ready to produce consumable data.
argument-hint: "<spec-name>"
allowed-tools: Read, Bash, Glob, Grep, Agent
---

Run the Gold zone (data product creation) for spec "$ARGUMENTS".

This is the "casting" step — pouring refined metal into a useful mold.

## YOU ARE AN ORCHESTRATOR, NOT AN IMPLEMENTER

You run pipeline gate commands (Bash) and dispatch agents (Agent tool). You NEVER write code, edit files, or produce governance artifacts yourself. Every piece of real work is done by a named agent via `subagent_type`.

## MANDATORY: How to Dispatch Agents

All Brightsmith agents are plugin agents and MUST use the `bs:` namespace prefix. This is non-negotiable.

CORRECT:
```
Agent(
  description: "EDA for $ARGUMENTS",
  subagent_type: "bs:data-analyst",
  prompt: "..."
)
```

WRONG (agent not found — missing bs: prefix):
```
Agent(
  description: "EDA for $ARGUMENTS",
  subagent_type: "data-analyst",
  prompt: "..."
)
```

ALSO WRONG (no subagent_type at all — blocked by hook):
```
Agent(
  description: "data-analyst EDA for $ARGUMENTS",
  prompt: "..."
)
```

## Pipeline Steps

1. Read the spec at `docs/specs/$ARGUMENTS.md`
2. Verify the spec targets the Gold zone
3. Check zone transition readiness: `python3 -m brightsmith.infra.pipeline_gate check-transition silver gold`
4. Initialize pipeline gate: `python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone gold`
5. Detect greenfield vs backfill mode

6. Execute the Gold zone pipeline from CLAUDE.md (same agent structure as Silver zone):

   For each agent step:
   a. Gate check: `python3 -m brightsmith.infra.pipeline_gate check "$ARGUMENTS" <step-name>`
   b. Dispatch: `Agent(description: "<task>", subagent_type: "bs:<agent-name>", prompt: "<full context>")`
   c. Register: `python3 -m brightsmith.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <path>`

   **Greenfield** pipeline order:
   - `bs:governance-reviewer` (pre) → `bs:data-steward` → `bs:semantic-modeler` (conceptual) → `bs:semantic-modeler` (logical) → `bs:semantic-modeler` (physical) → `bs:data-analyst` (EDA) → `bs:dq-rule-writer` → `bs:primary-agent` (implementation) → `bs:dq-engineer` → `bs:chaos-monkey` → `bs:lineage-tracker` → `bs:cde-tagger` → `bs:doc-generator` → `bs:governance-reviewer` (post) → `bs:staff-engineer`

   **Backfill** pipeline order:
   - `bs:semantic-modeler` (physical → logical) → `bs:data-analyst` (EDA) → `bs:dq-rule-writer` → `bs:dq-engineer` → `bs:chaos-monkey` → `bs:semantic-modeler` (conceptual) → `bs:data-steward` → `bs:governance-reviewer` (post) → `bs:staff-engineer`

   Conditionally skippable (with justification via pipeline gate skip):
   - `bs:entity-resolver`, `bs:pii-scanner`, `bs:temporal-modeler`, `bs:adversarial-auditor`

7. Generate data contract: `python3 -m brightsmith.infra.contract generate --table {table} --spec {spec}`
8. Verify golden dataset: `python3 -m brightsmith.infra.golden_dataset verify --spec "$ARGUMENTS"`
9. Report final status: `python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"`

## 🏆 Zone Celebration (after pipeline validates)

After the pipeline gate validates successfully, gather real stats and print a celebration summary:

```
🥇🔥 GOLD ZONE CAST — "$ARGUMENTS" 🥇🔥

Silver has been cast into business-ready gold. Your data products are verified,
contracted, and ready for consumption.

📊 Tables: [count consumable.* tables in Iceberg catalog]
📏 Rows: [total row count across consumable tables]
📚 Business Terms: [total count in governance/business-glossary.json]
🔍 DQ Rules: [total count across ALL specs] across [unique dimensions] dimensions
🛡️ Chaos Monkey: [X] hardening cycles survived
📜 Data Contracts: [count ACTIVE contracts in governance/data-contracts/]
✅ Golden Dataset: [count verified values in governance/golden-datasets/$ARGUMENTS-golden.json]
🏗️ Data Models: [count total model files across all specs]
🧬 Lineage Events: [total count in governance/lineage/]

📋 Artifacts Created:
  • Spec: docs/specs/$ARGUMENTS.md
  • Data Models: governance/models/$ARGUMENTS-*.md
  • DQ Rules: governance/dq-rules/[filename]
  • DQ Scorecard: governance/dq-scorecards/[filename]
  • Golden Dataset: governance/golden-datasets/$ARGUMENTS-golden.json
  • Data Contract: governance/data-contracts/[filename]
  • Lineage: governance/lineage/[filename]
  • Staff Engineer Review: governance/reviews/[filename]

🏭 Full Pipeline Summary:
  • Bronze → [N] raw tables from [source]
  • Silver → [N] base tables, [N] business terms, [N] data models
  • Gold → [N] consumable tables, [N] data contracts, [N] golden dataset values

🔜 Next: Run /bs:serve to fire up the MCP server and make this data AI-ready.
   First, @principal-data-architect reviews the zone, then @insight-manager
   designs the MCP server's tools and grounding context.
```

Replace bracketed values with real counts. The "Full Pipeline Summary" should aggregate across ALL zones. Omit lines where count is zero.
