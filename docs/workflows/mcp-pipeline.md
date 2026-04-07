# MCP Zone Pipeline

The MCP zone produces the final deliverable: an MCP (Model Context Protocol) server that makes the data product queryable by AI agents.

## MCP-Specific Rules

- MCP zone specs MUST include an evaluation set (`data/ai_ready/eval/{spec}-eval.json`) with at least 50 mechanically verifiable Q&A cases before @staff-engineer review
- Eval cases must span at least 5 categories: point lookup, comparison, ranking, trend, and edge case
- Every eval case must include: question, expected_answer, source_table, source_filters, source_column — so answers can be verified programmatically against consumable tables
- The eval set is a DQ artifact — @dq-engineer validates that all expected answers match pipeline output
- Verification framework (`python3 -m brightsmith.infra.verification run`) validates correctness: "is this number right?" not just "is this column non-null?". Pass rate >= 80% required for MCP zone.
- Headless pipeline runner: `python -m brightsmith.run` executes the full pipeline without AI agents. Supports `--zone`, `--validate-only`, `--dry-run`, `--output json`. Exit codes: 0=success, 1=DQ failure, 2=transform error, 3=contract violation, 4=config error.
- DQ gates between zones: if P0 fails after raw, base doesn't run. Contract verification between zones.
- Run history logged to `governance/run-history/{timestamp}.json` for audit trail.
- Headless readiness check: `python -m brightsmith.run --headless-ready` verifies all specs complete, contracts valid, golden datasets pass, no LLM imports in zone code.
- Zone transformers register via `domain/manifest.yaml` under `pipeline.zones.{zone}.module` and `pipeline.zones.{zone}.function`.
