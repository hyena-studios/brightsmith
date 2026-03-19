---
description: Start the MCP server — expose governed data to AI clients. Use when the Gold zone is complete and ready to serve.
allowed-tools: Read, Bash, Glob, Grep
---

Start the Brightsmith MCP server.

This is the "serve" step — delivering the finished product.

1. Check headless readiness: `python3 -m brightsmith.run --headless-ready`
2. Before starting, print the MCP celebration summary (see below)
3. Start the MCP server: `python3 -m brightsmith.serve`

The server exposes:
- **Tools**: query_table, list_tables, get_data_quality, get_lineage, get_contract (plus domain-specific tools)
- **Resources**: domain context, business glossary, data dictionary, grounding docs

Connect from Claude Desktop or Claude Code via MCP config.

## 🚀 MCP Celebration (before starting the server)

After headless readiness passes, gather real stats from the full pipeline and print:

```
🚀🤖 MCP SERVER READY — YOUR DATA IS NOW AI-POWERED 🚀🤖

The full Brightsmith pipeline is complete. From raw source to AI-ready,
your data has been mined, smelted, cast, and is now ready to serve.

⛏️  Bronze (Raw):
   • [N] raw tables ingested from [source name]
   • [N] total raw rows
   • Domain context discovered and documented

⚒️  Silver (Base):
   • [N] base tables — cleaned, deduplicated, normalized
   • [N] business terms defined in the glossary
   • [N] data models (conceptual → logical → physical)
   • [N] concept mappings applied

🥇 Gold (Consumable):
   • [N] consumable data products
   • [N] data contracts (all ACTIVE)
   • [N] golden dataset values verified
   • [N] total DQ rules protecting the pipeline

🤖 MCP (AI-Ready):
   • Tools exposed: [list tool names from MCP spec]
   • Resources available: [list resource names]
   • Eval set: [N] Q&A cases across [N] categories
   • Grounding docs: [list grounding doc filenames]

🛡️ Governance:
   • [N] total DQ rules across [N] dimensions
   • [N] chaos monkey cycles survived
   • [N] lineage events tracked
   • [N] CDE mappings
   • [N] data contracts enforced
   • All specs reviewed by @staff-engineer ✅

📋 All Specs:
  [list every spec file with its status: COMPLETE ✅]

📋 All Governance Artifacts:
  [list every file in governance/ organized by type]

🔗 Connect to this MCP server:
   Claude Desktop: Add to claude_desktop_config.json
   Claude Code: claude mcp add brightsmith -- python3 -m brightsmith.serve

This pipeline was built with Brightsmith — from raw data to AI-ready,
with governance at every step.
```

Replace ALL bracketed values with real counts from the filesystem. Read actual files to get accurate numbers. This is the grand finale — make it count.