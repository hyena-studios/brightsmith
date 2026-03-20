---
description: Run the Brightsmith pipeline for a specific spec. Orchestrates the full agent workflow from governance review through staff engineer sign-off. Use when ready to execute a spec.
argument-hint: "<spec-name>"
allowed-tools: Read, Bash, Glob, Grep, Agent
---

Run the Brightsmith pipeline for spec "$ARGUMENTS".

## YOU ARE AN ORCHESTRATOR, NOT AN IMPLEMENTER

You run pipeline gate commands (Bash) and dispatch agents (Agent tool). You NEVER write code, edit files, or produce governance artifacts yourself. Every piece of real work is done by a named agent via `subagent_type`.

## MANDATORY: How to Dispatch Agents

All Brightsmith agents are plugin agents and MUST use the `smitty:` namespace prefix. This is non-negotiable.

CORRECT:
```
Agent(
  description: "DQ execution for $ARGUMENTS",
  subagent_type: "smitty:dq-engineer",
  prompt: "Execute DQ rules for spec '$ARGUMENTS'..."
)
```

WRONG (agent not found — missing smitty: prefix):
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

- **Bronze Zone:** smitty:governance-reviewer → smitty:primary-agent → smitty:data-analyst → smitty:domain-context → smitty:dq-rule-writer → smitty:dq-engineer → smitty:chaos-monkey → [smitty:entity-resolver, smitty:pii-scanner, smitty:temporal-modeler, smitty:adversarial-auditor] → smitty:lineage-tracker → smitty:cde-tagger → smitty:doc-generator → smitty:governance-reviewer → smitty:staff-engineer
- **Base/Consumable Greenfield:** smitty:governance-reviewer → smitty:data-steward → smitty:semantic-modeler (conceptual → logical → physical) → smitty:data-analyst → smitty:dq-rule-writer → smitty:primary-agent → smitty:dq-engineer → smitty:chaos-monkey → [smitty:entity-resolver, smitty:pii-scanner, smitty:temporal-modeler, smitty:adversarial-auditor] → smitty:lineage-tracker → smitty:cde-tagger → smitty:doc-generator → smitty:governance-reviewer → smitty:staff-engineer
- **Base/Consumable Backfill:** smitty:semantic-modeler (physical → logical) → smitty:data-analyst → smitty:dq-rule-writer → smitty:dq-engineer → smitty:chaos-monkey → smitty:semantic-modeler (conceptual) → smitty:data-steward → smitty:governance-reviewer → smitty:staff-engineer

For each agent step:
1. Gate check: `python3 -m brightsmith.infra.pipeline_gate check "$ARGUMENTS" <step-name>`
2. Dispatch: `Agent(description: "<task>", subagent_type: "smitty:<agent-name>", prompt: "<full context>")`
3. Register: `python3 -m brightsmith.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <path>`

If not applicable, skip with justification:
```bash
python3 -m brightsmith.infra.pipeline_gate skip "$ARGUMENTS" <step-name> --reason "..." --evidence <path>
```

### Step 3: Zone Transition (after all specs in a zone complete)

After @staff-engineer signs off on the LAST spec in a zone:

1. **smitty:principal-data-architect** — BLOCKING zone transition review
2. **smitty:insight-manager** — Strategic analysis (silver->gold and gold->mcp ONLY)

### Step 4: Report Final Status

```bash
python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"
```

## Agents That Are NEVER Skippable

- smitty:governance-reviewer (pre and post)
- smitty:staff-engineer (final gate)
- smitty:data-analyst (EDA)
- smitty:dq-rule-writer
- smitty:dq-engineer (execution)
- smitty:chaos-monkey (adversarial hardening)
- smitty:lineage-tracker
- smitty:cde-tagger
- smitty:doc-generator

## Agents That Are Conditionally Skippable (with justification)

- smitty:entity-resolver — skip only if domain-context.md says entity resolution is trivial
- smitty:pii-scanner — skip only if domain-context.md PII section says no PII expected
- smitty:temporal-modeler — skip only if no temporal data exists
- smitty:adversarial-auditor — skip only if @chaos-monkey found no gaps in 5 cycles

## 🎉 Spec Celebration (after pipeline validates)

After the pipeline gate validates successfully, print the appropriate zone celebration based on which zone this spec belongs to. Use the same celebration format as the zone-specific skills (/smitty:mine, /smitty:smelt, /smitty:cast). Gather real stats from the filesystem — don't guess or use placeholders.
