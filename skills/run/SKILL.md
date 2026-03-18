---
description: Run the Grist pipeline for a specific spec. Orchestrates the full agent workflow from governance review through staff engineer sign-off. Use when ready to execute a spec.
argument-hint: "<spec-name>"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
context: fork
---

Run the Grist pipeline for spec "$ARGUMENTS".

1. Read the spec at `docs/specs/$ARGUMENTS.md`
2. Determine the zone (Raw, Base, Consumable, AI-Ready) from the spec
3. Execute the appropriate pipeline from CLAUDE.md:
   - Raw Zone: governance review -> implementation -> EDA -> domain context -> DQ rules -> DQ execution -> chaos monkey -> lineage -> CDE -> docs -> governance review -> staff engineer
   - Base/Consumable Greenfield: governance review -> data steward -> semantic modeler (conceptual -> logical -> physical) -> EDA -> DQ rules -> implementation -> DQ execution -> chaos monkey -> lineage -> CDE -> docs -> governance review -> staff engineer
   - Base/Consumable Backfill: semantic modeler (physical -> logical) -> EDA -> DQ rules -> DQ execution -> chaos monkey -> conceptual model -> data steward -> governance review -> staff engineer
4. Invoke each agent in order using @agent-name
5. If any agent requests changes, loop back to the appropriate step
6. Report final status when complete or blocked
