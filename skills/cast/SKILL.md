---
description: Run the Gold zone pipeline — shape data into business-ready products. Use when Silver zone is complete and ready to produce consumable data.
argument-hint: "<spec-name>"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
context: fork
---

Run the Gold zone (data product creation) for spec "$ARGUMENTS".

This is the "casting" step — pouring refined metal into a useful mold.

1. Read the spec at `docs/specs/$ARGUMENTS.md`
2. Verify the spec targets the Gold zone
3. Check zone transition readiness: `python3 -m brightsmith.infra.pipeline_gate check-transition silver gold`
4. Initialize pipeline gate: `python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone gold`
5. Detect greenfield vs backfill mode
6. Execute the Gold zone pipeline from CLAUDE.md (same structure as Silver zone pipeline)
7. Use the Agent tool for every agent step
8. Register each step completion with the pipeline gate
9. Generate data contract: `python3 -m brightsmith.infra.contract generate --table {table} --spec {spec}`
10. Verify golden dataset: `python3 -m brightsmith.infra.golden_dataset verify --spec "$ARGUMENTS"`
11. Report final status
