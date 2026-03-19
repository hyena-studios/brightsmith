---
description: Run the Bronze zone pipeline — extract raw data from source. Use when ready to ingest data from a domain source.
argument-hint: "<spec-name>"
allowed-tools: Read, Bash, Glob, Grep, Agent
---

Run the Bronze zone (data ingestion) for spec "$ARGUMENTS".

This is the "mining" step — extracting raw ore from the source.

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
2. Verify the spec targets the Bronze zone
3. Initialize pipeline gate: `python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone bronze`
4. Execute the Bronze zone pipeline from CLAUDE.md:

   For each agent step:
   a. Gate check: `python3 -m brightsmith.infra.pipeline_gate check "$ARGUMENTS" <step-name>`
   b. Dispatch: `Agent(description: "<task>", subagent_type: "<agent-name>", prompt: "<full context>")`
   c. Register: `python3 -m brightsmith.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <path>`

   Pipeline order:
   - `governance-reviewer` (pre) → `primary-agent` (implementation) → `data-analyst` (EDA) → `domain-context` → `dq-rule-writer` → `dq-engineer` → `chaos-monkey` → `lineage-tracker` → `cde-tagger` → `doc-generator` → `governance-reviewer` (post) → `staff-engineer`

   Conditionally skippable (with justification via pipeline gate skip):
   - `entity-resolver`, `pii-scanner`, `temporal-modeler`, `adversarial-auditor`

5. Report final status: `python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"`

## 🎉 Zone Celebration (after pipeline validates)

After the pipeline gate validates successfully, gather real stats and print a celebration summary. Use Bash/Glob/Grep to count actual artifacts, then output this to the user:

```
⛏️🔥 BRONZE ZONE FORGED — "$ARGUMENTS" ⛏️🔥

Congratulations! You've mined raw ore from the source and forged it into bronze.

📊 Tables: [count raw.* tables in Iceberg catalog]
📏 Rows: [total row count across raw tables]
🔍 DQ Rules: [count rules in governance/dq-rules/ for this spec] across [count unique dimensions] dimensions
🛡️ Chaos Monkey: [X] hardening cycles survived
📖 Domain Context: governance/domain-context.md
🧬 Lineage Events: [count in governance/lineage/]
🏷️ CDE Mappings: [count in governance/cde-catalog.json]

📋 Artifacts Created:
  • Spec: docs/specs/$ARGUMENTS.md
  • EDA Report: governance/eda/[filename]
  • Domain Context: governance/domain-context.md
  • DQ Rules: governance/dq-rules/[filename]
  • DQ Scorecard: governance/dq-scorecards/[filename]
  • Chaos Manifest: governance/chaos-manifests/[filename]
  • Lineage: governance/lineage/[filename]
  • Data Dictionary: governance/data-dictionary.json
  • Pipeline Checklist: governance/audit-trail/$ARGUMENTS-pipeline-checklist.md
  • Staff Engineer Review: governance/reviews/[filename]

🔜 Next: Run /bs:smelt to refine this raw ore into clean silver.
   First, @principal-data-architect will review the zone architecture.
```

Replace bracketed values with real counts from the filesystem. If a count is zero or a file doesn't exist, omit that line rather than showing zeros. Make the links clickable file paths.
