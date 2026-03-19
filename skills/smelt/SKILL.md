---
description: Run the Silver zone pipeline — clean, deduplicate, normalize, and model data. Use when Bronze zone is complete and ready to refine.
argument-hint: "<spec-name>"
allowed-tools: Read, Bash, Glob, Grep, Agent
---

Run the Silver zone (data refinement) for spec "$ARGUMENTS".

This is the "smelting" step — refining raw ore into clean metal.

## YOU ARE AN ORCHESTRATOR, NOT AN IMPLEMENTER

You run pipeline gate commands (Bash) and dispatch agents (Agent tool). You NEVER write code, edit files, or produce governance artifacts yourself. Every piece of real work is done by a named agent via `subagent_type`.

## MANDATORY: How to Dispatch Agents

Every agent MUST be invoked with `subagent_type` set to the agent name. This is what makes colored labels appear in the UI and loads the agent's dedicated context/instructions.

CORRECT (colored label, agent gets its own context):
```
Agent(
  description: "EDA for $ARGUMENTS",
  subagent_type: "data-analyst",
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

If you catch yourself about to invoke Agent() without `subagent_type`, STOP. You are doing it wrong. Fix it before proceeding.

## Pipeline Steps

1. Read the spec at `docs/specs/$ARGUMENTS.md`
2. Verify the spec targets the Silver zone
3. Check zone transition readiness: `python3 -m brightsmith.infra.pipeline_gate check-transition bronze silver`
4. Initialize pipeline gate: `python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone silver`
5. Detect greenfield vs backfill mode

6. Execute the Silver zone pipeline from CLAUDE.md:

   For each agent step:
   a. Gate check: `python3 -m brightsmith.infra.pipeline_gate check "$ARGUMENTS" <step-name>`
   b. Dispatch: `Agent(description: "<task>", subagent_type: "<agent-name>", prompt: "<full context>")`
   c. Register: `python3 -m brightsmith.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <path>`

   **Greenfield** pipeline order:
   - `governance-reviewer` (pre) → `data-steward` → `semantic-modeler` (conceptual) → `semantic-modeler` (logical) → `semantic-modeler` (physical) → `data-analyst` (EDA) → `dq-rule-writer` → `primary-agent` (implementation) → `dq-engineer` → `chaos-monkey` → `lineage-tracker` → `cde-tagger` → `doc-generator` → `governance-reviewer` (post) → `staff-engineer`

   **Backfill** pipeline order:
   - `semantic-modeler` (physical → logical) → `data-analyst` (EDA) → `dq-rule-writer` → `dq-engineer` → `chaos-monkey` → `semantic-modeler` (conceptual) → `data-steward` → `governance-reviewer` (post) → `staff-engineer`

   Conditionally skippable (with justification via pipeline gate skip):
   - `entity-resolver`, `pii-scanner`, `temporal-modeler`, `adversarial-auditor`

7. Report final status: `python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"`

## ✨ Zone Celebration (after pipeline validates)

After the pipeline gate validates successfully, gather real stats and print a celebration summary:

```
⚒️✨ SILVER ZONE SMELTED — "$ARGUMENTS" ⚒️✨

Raw ore refined into clean, modeled silver. Your data has structure, meaning, and trust.

📊 Tables: [count base.* tables in Iceberg catalog]
📏 Rows: [total row count across base tables]
📚 Business Terms: [count terms in governance/business-glossary.json]
🏗️ Data Models: [list conceptual/logical/physical model files created]
🔍 DQ Rules: [count rules for this spec] across [unique dimensions] dimensions
🛡️ Chaos Monkey: [X] hardening cycles survived
📜 Data Contracts: [count in governance/data-contracts/ for this spec]
🗺️ Concept Mappings: [count if concept normalization was performed]

📋 Artifacts Created:
  • Spec: docs/specs/$ARGUMENTS.md
  • Business Glossary: governance/business-glossary.json ([N] terms added)
  • Conceptual Model: governance/models/$ARGUMENTS-conceptual.md
  • Logical Model: governance/models/$ARGUMENTS-logical.md
  • Physical Model: governance/models/$ARGUMENTS-physical.md
  • EDA Report: governance/eda/[filename]
  • DQ Rules: governance/dq-rules/[filename]
  • DQ Scorecard: governance/dq-scorecards/[filename]
  • Chaos Manifest: governance/chaos-manifests/[filename]
  • Data Contract: governance/data-contracts/[filename]
  • Lineage: governance/lineage/[filename]
  • Staff Engineer Review: governance/reviews/[filename]

🔜 Next: Run /bs:cast to pour this silver into gold-grade data products.
   First, @principal-data-architect reviews the zone, then @insight-manager
   recommends what data products to build.
```

Replace bracketed values with real counts. Omit lines where count is zero or file doesn't exist.
