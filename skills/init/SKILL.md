---
description: Scaffold a new Brightsmith domain project. Use when starting a new data pipeline from scratch — creates directory structure, CLAUDE.md, pyproject.toml, ingestor skeleton, governance dirs, and first spec.
argument-hint: "[data source description]"
allowed-tools: Agent
---

Scaffold a new Brightsmith domain project for "$ARGUMENTS".

## YOU ARE AN ORCHESTRATOR, NOT AN IMPLEMENTER

You do ONE thing: launch the @setup agent. You do NOT scaffold files, write code, or create directories yourself.

**Immediately** invoke the setup agent:

```
Agent(
  description: "scaffold project for $ARGUMENTS",
  subagent_type: "smitty:setup",
  prompt: "Scaffold a new Brightsmith domain project for: $ARGUMENTS\n\nThe Brightsmith framework is at: $CLAUDE_PROJECT_DIR\nThe plugin root is at: ${CLAUDE_PLUGIN_ROOT}\nDo NOT search for CLAUDE.md or other framework files — you already have everything you need in your agent definition.\n\nInfer everything you can from the data source description:\n- Project name (derive from source)\n- API URLs, fetch methods (use your knowledge of known public APIs)\n- Seed entities (well-known defaults for the domain)\n- Domain standards (XBRL, ICD-10, etc. if recognizable)\n\nThe ONLY thing to ask the user is their contact email (required for API User-Agent headers).\n\nThen scaffold the full project and run uv sync + uv run pytest before reporting back."
)
```

That's it. When the agent returns, relay its summary to the user. Do not add to it, do not do follow-up work.
