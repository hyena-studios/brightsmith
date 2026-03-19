---
description: Run data quality assay — DQ rules, chaos monkey hardening, golden dataset verification, and contract validation. Use to test the purity of your data.
argument-hint: "[spec-name or --all]"
allowed-tools: Read, Bash, Glob, Grep, Agent
context: fork
---

Run a full data quality assay for "$ARGUMENTS".

This is the "assay" step — testing the purity of the refined metal.

1. **DQ Rules** — Execute all rules: `python3 -m brightsmith.infra.dq_runner run --spec "$ARGUMENTS"`
2. **DQ Scorecard** — Generate scorecard: `python3 -m brightsmith.infra.dq_runner scorecard --spec "$ARGUMENTS"`
3. **Chaos Monkey** — Run 5-cycle adversarial hardening if not already done
4. **Golden Datasets** — Verify: `python3 -m brightsmith.infra.golden_dataset verify --spec "$ARGUMENTS"`
5. **Verification** — Run correctness checks: `python3 -m brightsmith.infra.verification run --spec "$ARGUMENTS"`
6. **Contracts** — Verify all contracts: `python3 -m brightsmith.infra.contract verify --all`
7. **Pipeline Gate** — Validate: `python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"`

Report results as a summary dashboard with pass/fail per check.
