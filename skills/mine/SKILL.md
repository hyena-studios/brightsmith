---
description: Run the Bronze zone pipeline — extract raw data from source. Use when ready to ingest data from a domain source.
argument-hint: "<spec-name>"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
context: fork
---

Run the Bronze zone (data ingestion) for spec "$ARGUMENTS".

This is the "mining" step — extracting raw ore from the source.

1. Read the spec at `docs/specs/$ARGUMENTS.md`
2. Verify the spec targets the Bronze zone
3. Initialize pipeline gate: `python3 -m brightsmith.infra.pipeline_gate init "$ARGUMENTS" --zone bronze`
4. Execute the Bronze zone pipeline from CLAUDE.md:
   governance review → implementation (BaseIngestor) → EDA → domain context → DQ rules → DQ execution → chaos monkey → lineage → CDE → docs → governance review → staff engineer
5. Use the Agent tool for every agent step (colored labeled blocks in UI)
6. Register each step completion with the pipeline gate
7. Report final status: `python3 -m brightsmith.infra.pipeline_gate validate "$ARGUMENTS"`
