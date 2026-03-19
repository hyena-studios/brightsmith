---
description: Run the Brightsmith pipeline for a specific spec. Orchestrates the full agent workflow from governance review through staff engineer sign-off. Use when ready to execute a spec.
argument-hint: "<spec-name>"
allowed-tools: Read, Bash, Glob, Grep, Agent
---

Run the Brightsmith pipeline for spec "$ARGUMENTS".

## YOU ARE AN ORCHESTRATOR, NOT AN IMPLEMENTER

You run pipeline gate commands (Bash) and dispatch agents (Agent tool). You NEVER write code, edit files, or produce governance artifacts yourself. Every piece of real work is done by a named agent via `subagent_type`.

## MANDATORY: How to Dispatch Agents

Every agent MUST be invoked with `subagent_type` set to the **namespaced** agent name (prefixed with `bs:`). This is what makes colored labels appear in the UI and loads the agent's dedicated context/instructions.

CORRECT (colored label appears in UI):
```
Agent(
  description: "DQ execution for $ARGUMENTS",
  subagent_type: "bs:dq-engineer",
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

ALSO WRONG (missing bs: namespace prefix — agent not found):
```
Agent(
  description: "DQ execution for $ARGUMENTS",
  subagent_type: "dq-engineer",
  prompt: "Execute DQ rules for spec '$ARGUMENTS'..."
)
```

The `bs:` prefix is required because Brightsmith agents are loaded as a plugin. Without it, the agent won't be found.

## Pipeline Execution Protocol

### Step 0: Initialize Pipeline Gate

Before any agent runs, initialize the pipeline state:

```bash
python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone <zone> [--mode greenfield|backfill]
```

This creates `governance/pipeline-state/$ARGUMENTS-pipeline.json` tracking every step.

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

1. **bs:principal-data-architect** — BLOCKING zone transition review. Output: `governance/reviews/{zone}-architecture-review.md`.
2. **bs:insight-manager** — Strategic analysis (silver->gold and gold->mcp ONLY, skip at bronze->silver). Output: `governance/insights/{from-zone}-to-{to-zone}-insights.md`.

Verify transition readiness:
```bash
python3 -m brightsmith.infra.pipeline_gate check-transition <from-zone> <to-zone>
```

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

## Agents That Run at Zone Transitions Only

- bs:principal-data-architect — BLOCKING review at every zone transition
- bs:insight-manager — at silver->gold and gold->mcp transitions
