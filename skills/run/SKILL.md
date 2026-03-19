---
description: Run the Grist pipeline for a specific spec. Orchestrates the full agent workflow from governance review through staff engineer sign-off. Use when ready to execute a spec.
argument-hint: "<spec-name>"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
context: fork
---

Run the Grist pipeline for spec "$ARGUMENTS".

## Pipeline Execution Protocol

### Step 0: Initialize Pipeline Gate

Before any agent runs, initialize the pipeline state:

```bash
python3 -m grist.infra.pipeline_gate init "$ARGUMENTS" --zone <zone> [--mode greenfield|backfill]
```

This creates `governance/pipeline-state/$ARGUMENTS-pipeline.json` tracking every step.

### Step 1: Read the Spec & Detect Zone

1. Read the spec at `docs/specs/$ARGUMENTS.md`
2. Determine the zone (Raw, Base, Consumable, AI-Ready) from the spec
3. For Base/Consumable: detect greenfield vs backfill (do target tables exist?)

### Step 2: Execute the Pipeline

Execute the appropriate pipeline from CLAUDE.md:

- **Raw Zone:** governance review → implementation → EDA → domain context → DQ rules → DQ execution → chaos monkey → [entity-resolver, pii-scanner, temporal-modeler, adversarial-auditor] → lineage → CDE → docs → governance review → staff engineer
- **Base/Consumable Greenfield:** governance review → data steward → semantic modeler (conceptual → logical → physical) → EDA → DQ rules → implementation → DQ execution → chaos monkey → [entity-resolver, pii-scanner, temporal-modeler, adversarial-auditor] → lineage → CDE → docs → governance review → staff engineer
- **Base/Consumable Backfill:** semantic modeler (physical → logical) → EDA → DQ rules → DQ execution → chaos monkey → conceptual model → data steward → governance review → staff engineer

### Step 3: Zone Transition (after all specs in a zone complete)

After @staff-engineer signs off on the LAST spec in a zone:

1. **@principal-data-architect** — BLOCKING zone transition review. Output: `governance/reviews/{zone}-architecture-review.md`. Must be COMPLETED before proceeding.
2. **@insight-manager** — Strategic analysis (base→consumable and consumable→ai-ready ONLY, skip at raw→base). Output: `governance/insights/{from-zone}-to-{to-zone}-insights.md`.

Verify transition readiness:
```bash
python3 -m grist.infra.pipeline_gate check-transition <from-zone> <to-zone>
```

### Step 4: Report Final Status

When complete or blocked, report status and validate:
```bash
python3 -m grist.infra.pipeline_gate validate "$ARGUMENTS"
```

## Agent Execution Rules

Every agent in the pipeline MUST be either **executed** or **explicitly skipped with documented justification**. Silent omission is not allowed.

For each agent in the pipeline:

1. **Gate check** — before invoking the agent:
   ```bash
   python3 -m grist.infra.pipeline_gate check "$ARGUMENTS" <step-name>
   ```
   If BLOCKED, STOP and report which prerequisites are missing. Do NOT proceed by skipping the check.

2. **Execute or skip:**
   - If applicable: invoke the agent via `@agent-name` and capture output
   - If not applicable: skip with justification:
     ```bash
     python3 -m grist.infra.pipeline_gate skip "$ARGUMENTS" <step-name> --reason "..." --evidence <path>
     ```

3. **Register completion** — after an agent completes:
   ```bash
   python3 -m grist.infra.pipeline_gate complete "$ARGUMENTS" <step-name> --output <artifact-path>
   ```

4. **Loop back** — if any agent requests changes, return to the appropriate step. The gate tracks current state.

### Agents That Are NEVER Skippable

These agents run on every spec, no exceptions:
- @governance-reviewer (pre and post)
- @staff-engineer (final gate)
- @data-analyst (EDA)
- @dq-rule-writer
- @dq-engineer (execution)
- @chaos-monkey (adversarial hardening)
- @lineage-tracker
- @cde-tagger
- @doc-generator

### Agents That Are Conditionally Skippable (with justification)

- @entity-resolver — skip only if domain-context.md says entity resolution is trivial (e.g., stable IDs, no name matching needed)
- @pii-scanner — skip only if domain-context.md PII section says no PII expected
- @temporal-modeler — skip only if no temporal data exists
- @adversarial-auditor — skip only if @chaos-monkey found no gaps in 5 cycles

### Agents That Run at Zone Transitions Only

- @principal-data-architect — BLOCKING review at every zone transition
- @insight-manager — at base→consumable and consumable→ai-ready transitions

## Pipeline Completion Gate

Before marking a spec COMPLETE, run validation:

```bash
python3 -m grist.infra.pipeline_gate validate "$ARGUMENTS"
```

If validation fails, the spec CANNOT be marked complete. @staff-engineer's final review MUST include a passing gate validation.

## Pipeline Completion Checklist

This checklist is auto-populated by the pipeline gate. Verify every row before completion:

| Agent | Status | Output Location | Skip Reason (if skipped) |
|-------|--------|-----------------|-------------------------|
| @governance-reviewer (pre) | EXECUTED / SKIPPED | governance/reviews/ | |
| @data-steward | EXECUTED / SKIPPED | governance/business-glossary.json | |
| @semantic-modeler (conceptual) | EXECUTED / SKIPPED | governance/models/ | |
| @semantic-modeler (logical) | EXECUTED / SKIPPED | governance/models/ | |
| @data-analyst (EDA) | EXECUTED / SKIPPED | governance/eda/ | |
| @dq-rule-writer | EXECUTED / SKIPPED | governance/dq-rules/ | |
| @semantic-modeler (physical) | EXECUTED / SKIPPED | governance/models/ | |
| @primary-agent (implementation) | EXECUTED / SKIPPED | src/ | |
| @dq-engineer (execution) | EXECUTED / SKIPPED | governance/dq-results/ | |
| @chaos-monkey (5 cycles) | EXECUTED / SKIPPED | governance/chaos-manifests/ | |
| @entity-resolver | EXECUTED / SKIPPED | | |
| @pii-scanner | EXECUTED / SKIPPED | governance/pii-scans/ | |
| @temporal-modeler | EXECUTED / SKIPPED | | |
| @lineage-tracker | EXECUTED / SKIPPED | governance/lineage/ | |
| @cde-tagger | EXECUTED / SKIPPED | governance/cde-catalog.json | |
| @doc-generator | EXECUTED / SKIPPED | governance/data-dictionary.json | |
| @adversarial-auditor | EXECUTED / SKIPPED | | |
| @governance-reviewer (post) | EXECUTED / SKIPPED | governance/reviews/ | |
| @staff-engineer | EXECUTED / SKIPPED | governance/reviews/ | |

This checklist is written to `governance/audit-trail/{spec}-pipeline-checklist.md` and verified by @governance-reviewer in the post-implementation review.
