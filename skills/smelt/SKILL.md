---
description: Run the Silver zone pipeline — clean, deduplicate, normalize, and model data. Use when Bronze zone is complete and ready to refine.
argument-hint: "<spec-name>"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
context: fork
---

Run the Silver zone (data refinement) for spec "$ARGUMENTS".

This is the "smelting" step — refining raw ore into clean metal.

1. Read the spec at `docs/specs/$ARGUMENTS.md`
2. Verify the spec targets the Silver zone
3. Check zone transition readiness: `python3 -m brightsmith.infra.pipeline_gate check-transition bronze silver`
4. Initialize pipeline gate: `python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone silver`
5. Detect greenfield vs backfill mode
6. Execute the Silver zone pipeline from CLAUDE.md:
   - Greenfield: governance review → data steward → semantic modeler (conceptual → logical → physical) → EDA → DQ rules → implementation → DQ execution → chaos monkey → lineage → CDE → docs → governance review → staff engineer
   - Backfill: semantic modeler (physical → logical) → EDA → DQ rules → DQ execution → chaos monkey → conceptual model → data steward → governance review → staff engineer
7. Use the Agent tool for every agent step
8. Register each step completion with the pipeline gate
9. Report final status
