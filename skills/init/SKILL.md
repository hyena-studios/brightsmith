---
description: Scaffold a new Grist domain project. Use when starting a new data pipeline from scratch — creates directory structure, CLAUDE.md, pyproject.toml, ingestor skeleton, governance dirs, and first spec.
argument-hint: "[data source description]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
context: fork
---

Scaffold a new Grist domain project for "$ARGUMENTS".

Infer everything you can from the data source description:
- Project name (derive from source — e.g., "SEC EDGAR" → `sec-edgar-grist`)
- API URLs, fetch methods (use your knowledge of known public APIs)
- Seed entities (well-known defaults for the domain)
- Domain standards (XBRL, ICD-10, etc. if recognizable)

The ONLY thing to ask the user is their **contact email** (required for API User-Agent headers).

Then scaffold the full project using `python -m grist.setup init` and create all config files, ingestor skeleton, and first spec.
