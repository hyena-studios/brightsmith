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

All Brightsmith agents are plugin agents and MUST use the `smitty:` namespace prefix. This is non-negotiable.

CORRECT:
```
Agent(
  description: "EDA for $ARGUMENTS",
  subagent_type: "smitty:data-analyst",
  prompt: "..."
)
```

WRONG (agent not found — missing smitty: prefix):
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
2. Verify the spec targets the Silver zone
3. Check zone transition readiness: `python3 -m brightsmith.infra.pipeline_gate check-transition bronze silver`
4. Initialize pipeline gate: `python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone silver`
5. Detect greenfield vs backfill mode

6. Execute the Silver zone pipeline from CLAUDE.md:

   For each agent step:
   a. Gate check: `python3 -m brightsmith.infra.pipeline_gate check "$ARGUMENTS" <step-name>`
   b. Dispatch: `Agent(description: "<task>", subagent_type: "smitty:<agent-name>", prompt: "<full context>")`
   c. Register: `python3 -m brightsmith.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <path>`

   **Greenfield** pipeline order:
   - `smitty:governance-reviewer` (pre) → `smitty:data-steward` → `smitty:semantic-modeler` (conceptual) → `smitty:semantic-modeler` (logical) → `smitty:semantic-modeler` (physical) → `smitty:data-analyst` (EDA) → `smitty:dq-rule-writer` → `smitty:primary-agent` (implementation) → `smitty:dq-engineer` → `smitty:chaos-monkey` → `smitty:lineage-tracker` → `smitty:cde-tagger` → `smitty:doc-generator` → `smitty:governance-reviewer` (post) → `smitty:staff-engineer`

   **Backfill** pipeline order:
   - `smitty:semantic-modeler` (physical → logical) → `smitty:data-analyst` (EDA) → `smitty:dq-rule-writer` → `smitty:dq-engineer` → `smitty:chaos-monkey` → `smitty:semantic-modeler` (conceptual) → `smitty:data-steward` → `smitty:governance-reviewer` (post) → `smitty:staff-engineer`

   Conditionally skippable (with justification via pipeline gate skip):
   - `smitty:entity-resolver`, `smitty:pii-scanner`, `smitty:temporal-modeler`, `smitty:adversarial-auditor`

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

🔜 Next: Run /smitty:cast to pour this silver into gold-grade data products.
   First, @principal-data-architect reviews the zone, then @insight-manager
   recommends what data products to build.
```

Replace bracketed values with real counts. Omit lines where count is zero or file doesn't exist.
