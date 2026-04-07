# Zone Transitions

At **every** zone boundary (bronze-to-silver, silver-to-gold, gold-to-mcp), after all specs in a zone are complete:

## Step 1: @principal-data-architect — Architecture Review

Runs at ALL transitions (including bronze-to-silver).

- Reviews all code, tests, governance artifacts, DQ results, and data in the completed zone
- Assesses: architecture decisions, data quality trust, governance proportionality, domain context accuracy, code quality
- Produces zone transition review: `governance/reviews/[zone]-architecture-review.md`
- Can flag risks that block progression to the next zone
- This is a checkpoint — catching structural issues is cheaper here than after the next zone is built

## Step 2: @insight-manager — Strategic Analysis

Runs at silver-to-gold and gold-to-mcp only. **NOT** bronze-to-silver.

- Queries real Iceberg tables (not just schemas)
- Builds on existing EDA reports, DQ scorecards, CDE catalog
- Recommends data products ranked by value/feasibility
- Identifies external data combination opportunities
- Recommends MCP server design (questions users will ask, tools needed, grounding context)
- Each recommendation includes **Verification Criteria** (what DQ rule confirms implementation)
- Suggests spec order for the next zone
- Output: `governance/insights/[source-zone]-to-[target-zone]-insights.md`

## Key Rules

- The Insight Report is the primary input for spec writing in the next zone. No spec should be written without it.
- The pipeline always produces a **MCP server** as the MCP zone deliverable — insight reports at the gold-to-mcp transition should focus on MCP server design.
- Zone transitions require: `python3 -m brightsmith.infra.pipeline_gate check-transition {from} {to}` must PASS
