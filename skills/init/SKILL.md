---
description: Scaffold a new Grist domain project. Use when starting a new data pipeline from scratch — creates directory structure, CLAUDE.md, pyproject.toml, ingestor skeleton, governance dirs, and first spec.
argument-hint: "[project-name]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

Scaffold a new Grist domain project named "$ARGUMENTS".

Run: `python -m grist.setup init --name "$ARGUMENTS"`

If no name was provided, ask the user for:
1. Project name (e.g., `sec-edgar`, `medicare-claims`, `shopify-orders`)
2. Data source description
3. How to fetch the data (API URL, file path, etc.)

After scaffolding, tell the user:
- What was created and where
- Next steps: `cd <project> && uv sync`
- Point them to the first spec in `docs/specs/`
- Remind them that @data-analyst will discover domain context — they don't need to know everything upfront
