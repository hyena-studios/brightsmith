---
description: Run the Brightsmith pipeline for a specific spec. Orchestrates the full agent workflow from governance review through staff engineer sign-off. Use when ready to execute a spec.
argument-hint: "<spec-name>"
allowed-tools: Read, Bash, Glob, Grep, Agent
---

Run the Brightsmith pipeline for spec "$ARGUMENTS".

## YOU ARE AN ORCHESTRATOR, NOT AN IMPLEMENTER

You run pipeline gate commands (Bash) and dispatch agents (Agent tool). You NEVER write code, edit files, or produce governance artifacts yourself. Every piece of real work is done by a named agent via `subagent_type`.

## MANDATORY: How to Dispatch Agents

All Brightsmith agents are plugin agents and MUST use the `bs:` namespace prefix. This is non-negotiable.

CORRECT:
```
Agent(
  description: "DQ execution for $ARGUMENTS",
  subagent_type: "bs:dq-engineer",
  prompt: "Execute DQ rules for spec '$ARGUMENTS'..."
)
```

WRONG (agent not found — missing bs: prefix):
```
Agent(
  description: "DQ execution for $ARGUMENTS",
  subagent_type: "dq-engineer",
  prompt: "..."
)
```

ALSO WRONG (no subagent_type at all — blocked by hook):
```
Agent(
  description: "dq-engineer DQ execution for $ARGUMENTS",
  prompt: "..."
)
```

## Pipeline Execution Protocol

### Step 0: Initialize Pipeline Gate

Before any agent runs, initialize the pipeline state:

```bash
python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone <zone> [--mode greenfield|backfill]
```

### Step 1: Read the Spec & Detect Zone

1. Read the spec at `docs/specs/$ARGUMENTS.md`
2. Determine the zone (Raw, Base, Consumable, AI-Ready) from the spec
3. For Base/Consumable: detect greenfield vs backfill (do target tables exist?)

### Step 2: Execute the Pipeline

Execute the appropriate pipeline from CLAUDE.md:

- **Bronze Zone:** bs:governance-reviewer → bs:primary-agent → bs:data-analyst → bs:domain-context → bs:dq-rule-writer → bs:dq-engineer → bs:chaos-monkey → [bs:entity-resolver, bs:pii-scanner, bs:temporal-modeler, bs:adversarial-auditor] → bs:lineage-tracker → bs:cde-tagger → bs:doc-generator → bs:governance-reviewer → bs:staff-engineer
- **Base/Consumable Greenfield:** bs:governance-reviewer → bs:data-steward → bs:semantic-modeler (conceptual → logical → physical) → bs:data-analyst → bs:dq-rule-writer → bs:primary-agent → bs:dq-engineer → bs:chaos-monkey → [bs:entity-resolver, bs:pii-scanner, bs:temporal-modeler, bs:adversarial-auditor] → bs:lineage-tracker → bs:cde-tagger → bs:doc-generator → bs:governance-reviewer → bs:staff-engineer
- **Base/Consumable Backfill:** bs:semantic-modeler (physical → logical) → bs:data-analyst → bs:dq-rule-writer → bs:dq-engineer → bs:chaos-monkey → bs:semantic-modeler (conceptual) → bs:data-steward → bs:governance-reviewer → bs:staff-engineer

For each agent step:
1. Gate check: `python3 -m brightsmith.infra.pipeline_gate check "$ARGUMENTS" <step-name>`
2. Dispatch: `Agent(description: "<task>", subagent_type: "bs:<agent-name>", prompt: "<full context>")`
3. Register: `python3 -m brightsmith.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <path>`

If not applicable, skip with justification:
```bash
python3 -m brightsmith.infra.pipeline_gate skip "$ARGUMENTS" <step-name> --reason "..." --evidence <path>
```

### Step 3: Zone Transition (after all specs in a zone complete)

After @staff-engineer signs off on the LAST spec in a zone:

1. **bs:principal-data-architect** — BLOCKING zone transition review
2. **bs:insight-manager** — Strategic analysis (silver->gold and gold->mcp ONLY)

### Step 4: Report Final Status

```bash
python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"
```

## Agents That Are NEVER Skippable

- bs:governance-reviewer (pre and post)
- bs:staff-engineer (final gate)
- bs:data-analyst (EDA)
- bs:dq-rule-writer
- bs:dq-engineer (execution)
- bs:chaos-monkey (adversarial hardening)
- bs:lineage-tracker
- bs:cde-tagger
- bs:doc-generator

## Agents That Are Conditionally Skippable (with justification)

- bs:entity-resolver — skip only if domain-context.md says entity resolution is trivial
- bs:pii-scanner — skip only if domain-context.md PII section says no PII expected
- bs:temporal-modeler — skip only if no temporal data exists
- bs:adversarial-auditor — skip only if @chaos-monkey found no gaps in 5 cycles

## 🎉 Spec Celebration (after pipeline validates)

After the pipeline gate validates successfully, print the appropriate zone celebration based on which zone this spec belongs to. Use the same celebration format as the zone-specific skills (/bs:mine, /bs:smelt, /bs:cast). Gather real stats from the filesystem — don't guess or use placeholders.
