# Framework Spec: Brightsmith — Medallion Architecture Rename

**Status:** COMPLETE
**Zone:** Infrastructure (cosmetic refactor)
**Primary Agent:** @primary-agent
**Created:** 2026-03-19

## Problem Statement

The project is named "Grist" and uses non-standard zone names: `raw`, `base`, `consumable`, `ai_ready`. Two problems:

1. **Zone names don't match industry standard.** The medallion architecture (Databricks/lakehouse) uses Bronze, Silver, Gold. Anyone who knows the pattern doesn't recognize ours.
2. **Project name doesn't convey what it does.** "Grist" is abstract. The pipeline takes raw data ore, refines it through progressive zones, tests its purity, and produces governed data ready for AI consumption. That's metalworking.

## The Rename

### Project: Grist → Brightsmith

A **brightsmith** is a metalworker who polishes and finishes raw metal into something that shines. That's exactly what the pipeline does — take raw data ore, refine it, test it, and produce something bright and ready to use.

- Package: `brightsmith` (available on PyPI)
- Plugin prefix: `/bs:` (e.g., `/bs:mine`, `/bs:smelt`)
- Short, memorable, no conflicts

### Zones: Medallion Standard

| Current | New | Medallion | What It Does |
|---------|-----|-----------|-------------|
| `raw` | `bronze` | Bronze | Land data as-is, no interpretation |
| `base` | `silver` | Silver | Clean, deduplicate, normalize, model |
| `consumable` | `gold` | Gold | Business-ready, governed, queryable |
| `ai_ready` | `mcp` | (Brightsmith extension) | Serve via MCP server with governance metadata |

### Skill Commands: Metallurgy Vocabulary

| Command | Metallurgy | Pipeline Action |
|---------|-----------|-----------------|
| `/bs:init` | Set up the forge | Scaffold a new domain project |
| `/bs:mine` | Extract ore | Bronze zone — ingest raw data from source |
| `/bs:smelt` | Refine ore into metal | Silver zone — clean, deduplicate, normalize |
| `/bs:cast` | Pour metal into a mold | Gold zone — shape into business-ready products |
| `/bs:assay` | Test metal purity | DQ rules, chaos monkey, verification |
| `/bs:stamp` | Hallmark of authenticity | Data contracts, governance sign-off |
| `/bs:serve` | Deliver the product | MCP server — expose to AI clients |
| `/bs:run` | Full production run | Run the full pipeline (mine → smelt → cast) |
| `/bs:status` | Inspect the forge | Dashboard of project state |

## Scope

This is a **mechanical rename with zero logic changes**. The 4-zone pattern, governance gates, DQ enforcement, promote pattern, contracts, pipeline runner, and all agent workflows work identically. Only identifiers change.

## What Changes

### 1. Package Name

| Item | Old | New |
|------|-----|-----|
| Python package | `grist` | `brightsmith` |
| PyPI name | `grist` | `brightsmith` |
| Import prefix | `from grist.` | `from brightsmith.` |
| Config env vars | `GRIST_PROJECT_ROOT` | `BRIGHTSMITH_PROJECT_ROOT` |
| Config env vars | `GRIST_REQUIRE_HUMAN_APPROVAL` | `BRIGHTSMITH_REQUIRE_HUMAN_APPROVAL` |
| Config env vars | `GRIST_CONFIDENCE_FLOOR` | `BRIGHTSMITH_CONFIDENCE_FLOOR` |
| Config env vars | `GRIST_ENV` | `BRIGHTSMITH_ENV` |
| Plugin directory | `.claude-plugin/` | `.claude-plugin/` (unchanged, update plugin.json `name`) |
| CLI | `python -m grist.run` | `python -m brightsmith.run` |
| CLI | `python -m grist.serve` | `python -m brightsmith.serve` |
| CLI | `python -m grist.infra.dq_runner` | `python -m brightsmith.infra.dq_runner` |
| Skills prefix | `/grist:` | `/bs:` |

### 2. Python Source Directories

| Old Path | New Path |
|----------|----------|
| `src/grist/` | `src/brightsmith/` |
| `src/grist/raw/` | `src/brightsmith/bronze/` |
| `src/grist/base/` | `src/brightsmith/silver/` |
| `src/grist/ai_ready/` | `src/brightsmith/mcp/` |
| `src/grist/infra/` | `src/brightsmith/infra/` |
| `src/grist/ai_ready/base_chat_agent.py` | (deleted — superseded by MCP server) |

Note: `src/brightsmith/gold/` does not exist in the framework — domain projects create their gold zone code. @setup scaffolds it.

### 3. Test Directories

| Old Path | New Path |
|----------|----------|
| `tests/raw/` | `tests/bronze/` |
| `tests/base/` | `tests/silver/` |
| `tests/ai_ready/` | `tests/mcp/` |
| `tests/infra/` | `tests/infra/` (unchanged) |
| `tests/integration/` | `tests/integration/` (unchanged) |

### 4. Import Paths

Every `from grist.` import becomes `from brightsmith.`:

| Old Import | New Import |
|------------|-----------|
| `from grist.config import PROJECT_ROOT` | `from brightsmith.config import PROJECT_ROOT` |
| `from grist.raw.base_ingestor import BaseIngestor` | `from brightsmith.bronze.base_ingestor import BaseIngestor` |
| `from grist.base.concept_normalization import ConceptNormalizer` | `from brightsmith.silver.concept_normalization import ConceptNormalizer` |
| `from grist.ai_ready.base_mcp_server import BaseMCPServer` | `from brightsmith.mcp.base_mcp_server import BaseMCPServer` |
| `from grist.infra.promote import promote` | `from brightsmith.infra.promote import promote` |
| `from grist.infra.grain import compute_grain_id` | `from brightsmith.infra.grain import compute_grain_id` |
| `from grist.infra.pipeline_gate import PipelineGate` | `from brightsmith.infra.pipeline_gate import PipelineGate` |
| `from grist.infra.contract import verify_contract` | `from brightsmith.infra.contract import verify_contract` |
| `from grist.infra.dq_runner import load_rules` | `from brightsmith.infra.dq_runner import load_rules` |
| `from grist.domain_loader import load_manifest` | `from brightsmith.domain_loader import load_manifest` |

### 5. Type Definitions and Constants

**`src/brightsmith/infra/pipeline_gate.py`:**
```python
Zone = Literal["bronze", "silver", "gold", "mcp"]
```

Step registries rename: `RAW_ZONE_STEPS` → `BRONZE_ZONE_STEPS`, `BASE_GREENFIELD_STEPS` → `SILVER_GREENFIELD_STEPS`, `CONSUMABLE_GREENFIELD_STEPS` → `GOLD_GREENFIELD_STEPS`, `AI_READY_STEPS` → `MCP_ZONE_STEPS`.

**`src/brightsmith/run.py`:**
```python
ZONE_ORDER = ["bronze", "silver", "gold", "mcp"]
```

**`src/brightsmith/infra/dq_runner.py`:**
```python
_KNOWN_NAMESPACES = {"bronze", "silver", "gold", "mcp"}
```

### 6. Config and Environment Variables

**`src/brightsmith/config.py`:**
```python
PROJECT_ROOT = _resolve_project_root()  # reads BRIGHTSMITH_PROJECT_ROOT
PROJECT_NAME = os.environ.get("BRIGHTSMITH_PROJECT_NAME", "brightsmith")
REQUIRE_HUMAN_APPROVAL = os.environ.get("BRIGHTSMITH_REQUIRE_HUMAN_APPROVAL", "true").lower() == "true"
CONFIDENCE_FLOOR = float(os.environ.get("BRIGHTSMITH_CONFIDENCE_FLOOR", "0.7"))
WAREHOUSE_PATH = PROJECT_ROOT / "data" / "bronze" / "iceberg_warehouse"
```

**Backward compatibility:** Accept both `GRIST_*` and `BRIGHTSMITH_*` env vars during transition, with `BRIGHTSMITH_*` taking precedence.

### 7. Skill Files

| Old Path | New Path | Old Command | New Command |
|----------|----------|-------------|-------------|
| `skills/init/SKILL.md` | `skills/init/SKILL.md` | `/grist:init` | `/bs:init` |
| `skills/run/SKILL.md` | `skills/run/SKILL.md` | `/grist:run` | `/bs:run` |
| `skills/status/SKILL.md` | `skills/status/SKILL.md` | `/grist:status` | `/bs:status` |

New skills:
| Skill | Command | Maps To |
|-------|---------|---------|
| `skills/mine/SKILL.md` | `/bs:mine` | Run bronze zone (ingest) |
| `skills/smelt/SKILL.md` | `/bs:smelt` | Run silver zone (transform) |
| `skills/cast/SKILL.md` | `/bs:cast` | Run gold zone (shape) |
| `skills/assay/SKILL.md` | `/bs:assay` | Run DQ, chaos monkey, verification |
| `skills/stamp/SKILL.md` | `/bs:stamp` | Generate/verify contracts |
| `skills/serve/SKILL.md` | `/bs:serve` | Start MCP server |

### 8. Plugin Manifest

**`.claude-plugin/plugin.json`:**
```json
{
  "name": "brightsmith",
  "description": "AI agent data pipeline framework — Bronze to MCP with full governance",
  "version": "0.2.0"
}
```

### 9. Data Directories (new projects)

```
data/
├── bronze/
│   └── iceberg_warehouse/
├── silver/
├── gold/
├── mcp/
│   ├── grounding/
│   └── eval/
├── catalog/
│   └── catalog.db
└── governance/
    └── iceberg_warehouse/
```

### 10. Documentation and Agent Definitions

All `.claude/agents/*.md` files, `CLAUDE.md`, `README.md`:
- "Grist" → "Brightsmith" (project name)
- "grist" → "brightsmith" (package references)
- "Raw zone" → "Bronze zone"
- "Base zone" → "Silver zone"
- "Consumable zone" → "Gold zone"
- "AI-Ready zone" → "MCP zone"
- "raw-to-base" → "bronze-to-silver"
- "base-to-consumable" → "silver-to-gold"
- "consumable-to-ai-ready" → "gold-to-mcp"
- `/grist:` → `/bs:` (skill commands)

### 11. Governance Artifacts

| Old | New |
|-----|-----|
| `governance/dq-rule-templates/consumable-patterns.json` | `governance/dq-rule-templates/gold-patterns.json` |
| Zone field values in pipeline state JSON | `bronze`, `silver`, `gold`, `mcp` |
| Contract schema `namespace:` values | New projects use `bronze`, `silver`, `gold` |

### 12. pyproject.toml

```toml
[project]
name = "brightsmith"
description = "AI agent data pipeline framework — Bronze to MCP with full governance"

[tool.hatch.build.targets.wheel]
packages = ["src/brightsmith"]
```

### 13. Repository

| Item | Old | New |
|------|-----|-----|
| GitHub repo | `jcernauske/grist` | `jcernauske/brightsmith` (rename on GitHub) |
| CLAUDE.md project path | `~/code/grist` | `~/code/brightsmith` |

## Backward Compatibility

**Existing domain projects are NOT broken by zone renames.** The framework reads zone/namespace names from the domain project's configuration (`source_config`, `manifest.yaml`), not from hardcoded constants. A project using `raw.company_facts` as its Iceberg namespace continues to work.

**What existing projects must update:**
1. Import paths: `from grist.` → `from brightsmith.`
2. CLI commands: `python -m grist.` → `python -m brightsmith.`
3. Env vars: `GRIST_*` → `BRIGHTSMITH_*` (old vars accepted during transition)
4. Skill commands: `/grist:run` → `/bs:run`
5. Plugin reference: `--plugin-dir ~/code/grist` → `--plugin-dir ~/code/brightsmith`

This is a one-time migration for each domain project.

## Implementation Order

1. Rename GitHub repository
2. `git mv src/grist src/brightsmith` (then zone subdirectories)
3. `git mv` test directories
4. Global find-and-replace: `from grist.` → `from brightsmith.`
5. Global find-and-replace: zone names in code constants
6. Update `pyproject.toml` (package name, paths)
7. Update `config.py` (env var names, warehouse path)
8. Update all agent definitions (project name, zone names)
9. Update `CLAUDE.md`, `README.md`, skills
10. Rename/create skill files for new commands
11. Update `.claude-plugin/plugin.json`
12. Rename governance template files
13. Delete `base_chat_agent.py` and `test_base_chat_agent.py`
14. Run full test suite — must be 285/285 pass
15. Update domain project (sec_edgar_grist) imports

## Not In Scope

- Renaming existing Iceberg namespaces in domain project catalogs (they keep working as-is)
- Renaming `data/` subdirectories in existing projects
- Any logic changes — this is purely identifiers, names, and paths
- Publishing to PyPI (future, after rename stabilizes)
