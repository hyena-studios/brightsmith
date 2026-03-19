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

Every agent MUST be invoked with `subagent_type` set to the **namespaced** agent name (prefixed with `bs:`). This is what makes colored labels appear in the UI and loads the agent's dedicated context/instructions.

CORRECT (colored label, agent gets its own context):
```
Agent(
  description: "EDA for $ARGUMENTS",
  subagent_type: "bs:data-analyst",
  prompt: "..."
)
```

WRONG (generic agent, no persona loaded, no colored label):
```
Agent(
  description: "data-analyst EDA for $ARGUMENTS",
  prompt: "..."
)
```

ALSO WRONG (missing bs: namespace prefix — agent not found):
```
Agent(
  description: "EDA for $ARGUMENTS",
  subagent_type: "data-analyst",
  prompt: "..."
)
```

If you catch yourself about to invoke Agent() without `subagent_type`, STOP. You are doing it wrong. Fix it before proceeding.

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
