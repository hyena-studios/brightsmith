---
description: Run the Brightsmith pipeline for a specific spec. Orchestrates the full agent workflow from governance review through staff engineer sign-off. Use when ready to execute a spec.
argument-hint: "<spec-name>"
allowed-tools: Read, Bash, Glob, Grep, Agent
---

Run the Brightsmith pipeline for spec "$ARGUMENTS".

## YOU ARE AN ORCHESTRATOR, NOT AN IMPLEMENTER

You run pipeline gate commands (Bash) and dispatch agents (Agent tool). You NEVER write code, edit files, or produce governance artifacts yourself. Every piece of real work is done by a named agent via `subagent_type`.

## MANDATORY: How to Dispatch Agents

Every agent MUST be invoked with `subagent_type` set to the agent name. This is what makes colored labels appear in the UI and loads the agent's dedicated context/instructions.

CORRECT (colored label appears in UI):
```
Agent(
  description: "DQ execution for $ARGUMENTS",
  subagent_type: "dq-engineer",
  prompt: "Execute DQ rules for spec '$ARGUMENTS'..."
)
```

WRONG (no colored label, agent name lost in description):
```
Agent(
  description: "dq-engineer DQ execution for $ARGUMENTS",
  prompt: "Execute DQ rules for spec '$ARGUMENTS'..."
)
```

If you catch yourself about to invoke Agent() without `subagent_type`, STOP. Fix it before proceeding.

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

- **Bronze Zone:** governance-reviewer → primary-agent → data-analyst → domain-context → dq-rule-writer → dq-engineer → chaos-monkey → [entity-resolver, pii-scanner, temporal-modeler, adversarial-auditor] → lineage-tracker → cde-tagger → doc-generator → governance-reviewer → staff-engineer
- **Base/Consumable Greenfield:** governance-reviewer → data-steward → semantic-modeler (conceptual → logical → physical) → data-analyst → dq-rule-writer → primary-agent → dq-engineer → chaos-monkey → [entity-resolver, pii-scanner, temporal-modeler, adversarial-auditor] → lineage-tracker → cde-tagger → doc-generator → governance-reviewer → staff-engineer
- **Base/Consumable Backfill:** semantic-modeler (physical → logical) → data-analyst → dq-rule-writer → dq-engineer → chaos-monkey → semantic-modeler (conceptual) → data-steward → governance-reviewer → staff-engineer

For each agent step:
1. Gate check: `python3 -m brightsmith.infra.pipeline_gate check "$ARGUMENTS" <step-name>`
2. Dispatch: `Agent(description: "<task>", subagent_type: "<agent-name>", prompt: "<full context>")`
3. Register: `python3 -m brightsmith.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <path>`

If not applicable, skip with justification:
```bash
python3 -m brightsmith.infra.pipeline_gate skip "$ARGUMENTS" <step-name> --reason "..." --evidence <path>
```

### Step 3: Zone Transition (after all specs in a zone complete)

After @staff-engineer signs off on the LAST spec in a zone:

1. **principal-data-architect** — BLOCKING zone transition review
2. **insight-manager** — Strategic analysis (silver->gold and gold->mcp ONLY)

### Step 4: Report Final Status

```bash
python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"
```

## Agents That Are NEVER Skippable

- governance-reviewer (pre and post)
- staff-engineer (final gate)
- data-analyst (EDA)
- dq-rule-writer
- dq-engineer (execution)
- chaos-monkey (adversarial hardening)
- lineage-tracker
- cde-tagger
- doc-generator

## Agents That Are Conditionally Skippable (with justification)

- entity-resolver — skip only if domain-context.md says entity resolution is trivial
- pii-scanner — skip only if domain-context.md PII section says no PII expected
- temporal-modeler — skip only if no temporal data exists
- adversarial-auditor — skip only if @chaos-monkey found no gaps in 5 cycles

## 🎉 Spec Celebration (after pipeline validates)

After the pipeline gate validates successfully, print the appropriate zone celebration based on which zone this spec belongs to. Use the same celebration format as the zone-specific skills (/bs:mine, /bs:smelt, /bs:cast). Gather real stats from the filesystem — don't guess or use placeholders.
