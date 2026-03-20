---
description: Run data quality assay — DQ rules, chaos monkey hardening, golden dataset verification, and contract validation. Use to test the purity of your data.
argument-hint: "[spec-name or --all]"
allowed-tools: Read, Bash, Glob, Grep, Agent
---

Run a full data quality assay for "$ARGUMENTS".

This is the "assay" step — testing the purity of the refined metal.

## YOU ARE AN ORCHESTRATOR, NOT AN IMPLEMENTER

You run DQ infrastructure commands (Bash) and dispatch agents where needed (Agent tool). You NEVER write rules, edit files, or produce governance artifacts yourself.

## MANDATORY: How to Dispatch Agents

All Brightsmith agents are plugin agents and MUST use the `bs:` namespace prefix.

CORRECT: `Agent(description: "task", subagent_type: "bs:dq-engineer", prompt: "...")`
WRONG:   `Agent(description: "task", subagent_type: "dq-engineer", prompt: "...")` (missing bs: prefix)
ALSO WRONG: `Agent(description: "dq-engineer task", prompt: "...")` (no subagent_type — blocked by hook)

## Assay Steps

1. **DQ Rules** — Execute all rules: `python3 -m brightsmith.infra.dq_runner run --spec "$ARGUMENTS"`
2. **DQ Scorecard** — Generate scorecard: `python3 -m brightsmith.infra.dq_runner scorecard --spec "$ARGUMENTS"`
3. **Chaos Monkey** — If not already hardened, dispatch:
   `Agent(description: "adversarial hardening for $ARGUMENTS", subagent_type: "bs:chaos-monkey", prompt: "...")`
4. **Golden Datasets** — Verify: `python3 -m brightsmith.infra.golden_dataset verify --spec "$ARGUMENTS"`
5. **Verification** — Run correctness checks: `python3 -m brightsmith.infra.verification run --spec "$ARGUMENTS"`
6. **Contracts** — Verify all contracts: `python3 -m brightsmith.infra.contract verify --all`
7. **Pipeline Gate** — Validate: `python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"`

Report results as a summary dashboard with pass/fail per check.
