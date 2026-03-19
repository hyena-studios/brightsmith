---
description: Start the MCP server — expose governed data to AI clients. Use when the Gold zone is complete and ready to serve.
allowed-tools: Bash
context: fork
---

Start the Brightsmith MCP server.

This is the "serve" step — delivering the finished product.

1. Check headless readiness: `python3 -m brightsmith.run --headless-ready`
2. Start the MCP server: `python3 -m brightsmith.serve`

The server exposes:
- **Tools**: query_table, list_tables, get_data_quality, get_lineage, get_contract (plus domain-specific tools)
- **Resources**: domain context, business glossary, data dictionary, grounding docs

Connect from Claude Desktop or Claude Code via MCP config.
