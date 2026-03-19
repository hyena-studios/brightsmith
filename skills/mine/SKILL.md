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
2. Verify the spec targets the Bronze zone
3. Initialize pipeline gate: `python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone bronze`
4. Execute the Bronze zone pipeline from CLAUDE.md:

   For each agent step:
   a. Gate check: `python3 -m brightsmith.infra.pipeline_gate check "$ARGUMENTS" <step-name>`
   b. Dispatch: `Agent(description: "<task>", subagent_type: "bs:<agent-name>", prompt: "<full context>")`
   c. Register: `python3 -m brightsmith.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <path>`

   Pipeline order:
   - `bs:governance-reviewer` (pre) → `bs:primary-agent` (implementation) → `bs:data-analyst` (EDA) → `bs:domain-context` → `bs:dq-rule-writer` → `bs:dq-engineer` → `bs:chaos-monkey` → `bs:lineage-tracker` → `bs:cde-tagger` → `bs:doc-generator` → `bs:governance-reviewer` (post) → `bs:staff-engineer`

   Conditionally skippable (with justification via pipeline gate skip):
   - `bs:entity-resolver`, `bs:pii-scanner`, `bs:temporal-modeler`, `bs:adversarial-auditor`

5. Report final status: `python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"`
