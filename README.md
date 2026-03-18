# Grist

AI agent data pipeline framework. Takes raw data from any source and grinds it into governed, AI-ready datasets — without knowing the domain upfront.

**Raw → Base → Consumable → AI-Ready** with full governance metadata at every step.

## What Makes Grist Different

Most data pipelines are built for a specific domain. Grist discovers the domain from the data itself.

1. You point it at a data source (API, files, database)
2. AI agents ingest the raw data, profile it, and determine what it is
3. A canonical **domain context document** is produced — vocabulary, entity types, temporal patterns, applicable regulations, taxonomy systems
4. Every downstream agent reads that same document — no independent assumptions, no drift
5. The pipeline builds governed data products through spec-driven development with human approval gates
6. The AI-Ready zone produces a **tool-use chat agent** that queries live Iceberg data

Grist was extracted from [sec_edgair](https://github.com/jcernauske/sec_edgair), a production-grade SEC EDGAR financial data pipeline. Everything domain-specific was replaced with a discovery mechanism. Same rigor, any data. Field-tested with [sec_edgar_grist](https://github.com/jcernauske/sec_edgar_grist).

## Install as Claude Code Plugin

```bash
# From any Claude Code session:
/plugin install    # point to this repo's git URL

# Or test locally:
claude --plugin-dir ~/code/grist
```

Once installed, three skills are available:

| Skill | What It Does |
|-------|-------------|
| `/grist:init my-project` | Scaffold a new domain project |
| `/grist:run raw-ingest-foo` | Run the pipeline for a spec |
| `/grist:status` | Dashboard of project state |

All 24 agents are available as subagents (`@setup`, `@chaos-monkey`, `@staff-engineer`, etc.).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Domain Pack                            │
│  manifest.yaml · sources/*.yaml · BaseIngestor subclass     │
└─────────────────┬───────────────────────────────────────────┘
                  │
    ┌─────────────▼─────────────┐
    │         Raw Zone          │  Ingest as-is, metadata enrichment, dedup
    │   Iceberg tables (DuckDB) │
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │   Domain Discovery        │  @data-analyst EDA → @domain-context synthesis
    │   + User Interview        │  → governance/domain-context.md
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │        Base Zone          │  Normalize, resolve entities, map concepts
    │   Governed, modeled       │  3-stage data models (conceptual → logical → physical)
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │     Consumable Zone       │  Data products: ratios, comparisons, aggregations
    │   Contracted, documented  │  Golden dataset validation
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │      AI-Ready Zone        │  Tool-use chat agent, grounding docs, eval sets
    │   Governed AI consumption │
    └───────────────────────────┘
```

## Agent Pipeline

Grist uses **24 specialized AI agents** orchestrated through a spec-driven workflow. Every piece of code, every governance artifact, every data transformation traces back to a spec.

### Raw Zone Pipeline
| Step | Agent | What It Does |
|------|-------|-------------|
| 1 | @governance-reviewer | Pre-implementation spec review |
| 2 | @primary-agent | Ingest raw data via BaseIngestor |
| 3 | @data-analyst | EDA + domain discovery |
| 4 | @domain-context | Synthesize domain knowledge + user interview |
| 5 | @dq-rule-writer | Write DQ rules from EDA evidence |
| 6 | @dq-engineer | Execute rules, produce scorecard |
| 7 | @chaos-monkey | 5-cycle adversarial hardening against shadow tables |
| 8 | @lineage-tracker | OpenLineage capture |
| 9 | @cde-tagger | CDE mapping |
| 10 | @doc-generator | Data dictionary + contracts |
| 11 | @governance-reviewer | Post-implementation completeness check |
| 12 | @staff-engineer | Final quality gate + data correctness spot-check |

### Base & Consumable Zone Pipeline
Same as Raw, plus:
- @data-steward — business term identification and glossary management
- @semantic-modeler — 3-stage data modeling (conceptual → logical → physical) with human approval gates
- @entity-resolver — canonical entity mapping across source identifiers
- @temporal-modeler — bitemporal schema design (valid time + Iceberg transaction time)

### Zone Transitions
@insight-manager runs at **base-to-consumable** and **consumable-to-ai-ready** transitions — analyzes completed data, recommends data products, suggests chat agent design, and provides verification criteria for each recommendation.

### Governance & Quality Agents
| Agent | Role |
|-------|------|
| @adversarial-auditor | Tests whether AI-built artifacts could be hallucinated |
| @bcbs239-auditor | Regulatory framework assessment (BCBS 239, SOX, GDPR, HIPAA) |
| @chaos-monkey | Schema-agnostic adversarial DQ testing with After-Action Reports |
| @pii-scanner | PII detection and sensitivity classification |
| @policy-engineer | Data access policy definitions (RLS, masking, retention) |
| @principal-data-architect | Independent full-pipeline architecture review |

### Content & Delivery Agents
| Agent | Role |
|-------|------|
| @content-strategist | Translates technical work into executive/architect/compliance narratives |
| @mcp-engineer | MCP server exposing governed data as AI-callable tools |
| @web-designer | Static site for project documentation and results |

## Key Framework Utilities

| Component | Path | Purpose |
|-----------|------|---------|
| Base ingestor | `grist.raw.base_ingestor` | Abstract ingestor with dedup, metadata, snapshots |
| **Period disambiguator** | `grist.infra.period_disambiguator` | **Temporal period classification using date-span analysis** — prevents the class of bug that corrupted 17% of sec_edgar_grist data |
| **Chaos monkey** | `grist.infra.chaos_monkey` | **Schema-agnostic adversarial testing** — introspects PyIceberg schemas, injects type-appropriate corruptions, reconciles with After-Action Reports |
| **Integration test harness** | `grist.infra.integration_test_harness` | **Golden dataset validation** — compares pipeline output to known-correct reference values |
| **Base chat agent** | `grist.ai_ready.base_chat_agent` | **AI-Ready zone base class** — Anthropic SDK setup, tool registration, Iceberg query execution |
| Iceberg setup | `grist.infra.iceberg_setup` | Table creation, append, read via PyIceberg + DuckDB |
| DQ runner | `grist.infra.dq_runner` | Execute SQL rules against Iceberg, threshold evaluation, P0 gating, `--shadow` flag for chaos monkey |
| DQ scorecard | `grist.infra.dq_scorecard` | Generate markdown scorecards from real results |
| Lineage | `grist.infra.lineage` | OpenLineage event emission to Iceberg |
| Staging | `grist.infra.staging` | Proposal staging, confidence-based approval gates |
| Glossary loader | `grist.infra.glossary_loader` | Three-tier glossary composition (standards → domains → project) |
| Domain loader | `grist.domain_loader` | Manifest parsing, source config, hints resolution |
| Concept normalizer | `grist.base.concept_normalization` | Tiered matching (exact → prefix → pattern → heuristic) |
| Setup CLI | `grist.setup` | `python -m grist.setup init` — scaffold domain projects |

## Domain Discovery → Domain Context

This is the core innovation. In a domain-specific pipeline, every agent knows the vocabulary. In Grist:

```
@data-analyst                    @domain-context
 ┌──────────────┐                ┌──────────────────────┐
 │ EDA Report   │───────────────▶│ User Interview       │
 │ - profiles   │                │ (5-10 EDA-informed   │
 │ - patterns   │                │  targeted questions)  │
 │ - anomalies  │                │         │             │
 │ - grain      │                │         ▼             │
 │ - taxonomies │                │ domain-context.md    │
 └──────────────┘                │ - vocabulary         │
                                 │ - entity types       │
                                 │ - temporal patterns   │
                                 │ - regulations        │
                                 │ - PII expectations   │
                                 │ - mapping guidance   │
                                 │ - edge cases         │
                                 │ - unresolved risks   │
                                 └──────────┬───────────┘
                                            │
                    ┌───────────────────────┬┴──────────────────────┐
                    ▼                       ▼                       ▼
              @data-steward          @cde-tagger            @dq-rule-writer
              @entity-resolver       @pii-scanner           @temporal-modeler
              @insight-manager       @bcbs239-auditor       @doc-generator
              @content-strategist    @mcp-engineer          @principal-data-architect
              @adversarial-auditor
```

All 13 downstream agents read the same `governance/domain-context.md`. No agent independently invents domain assumptions. Unanswered interview questions become mandatory DQ rule requirements.

## Data Quality

### DQ Rule Templates (Consumable Zone)

Every consumable spec must address four mandatory patterns:

| Pattern | Priority | What It Catches |
|---------|----------|-----------------|
| CONS-GRAIN-UNIQUE | P0 | Duplicate values at the business grain |
| CONS-IMPOSSIBLE-VALUE | P0 | Domain constraint violations (negative revenue, >100% margins) |
| CONS-CROSS-TABLE | P1 | Related tables disagree on shared dimensions |
| CONS-GOLDEN-DATASET | P0 | Pipeline output doesn't match known-correct reference values |

Templates at `governance/dq-rule-templates/consumable-patterns.json`.

### Chaos Monkey Hardening

Every zone pipeline includes a 5-cycle adversarial hardening loop:
1. Inject corruptions into shadow copy (escalating rates: 5% → 10%)
2. Run DQ rules against shadow tables (`--shadow` flag)
3. Generate After-Action Report (caught vs missed)
4. Patch rules for any gaps found
5. Repeat until no new gaps for 2 consecutive cycles

### Golden Datasets

Every consumable spec requires a golden dataset (`governance/golden-datasets/{spec}-golden.json`) with at least 3 independently verifiable values. @staff-engineer spot-checks actual output against reference data before approving.

## Spec-Driven Development

Every change goes through a spec in `docs/specs/`. Specs define:
- Problem statement and success criteria
- Technical design (schemas, business logic, algorithms)
- Agent workflow (which agents run, in what order)
- DQ rules and governance artifacts to produce

Specs have a lifecycle: `DRAFT → ARCH REVIEW → IMPLEMENTATION → TESTING → CODE REVIEW → VERIFICATION → COMPLETE`

No code gets written without a spec. No spec is marked complete without @staff-engineer's sign-off.

## Governance Artifacts

Every transformation produces governance metadata:

| Artifact | Location | Purpose |
|----------|----------|---------|
| Domain context | `governance/domain-context.md` | Canonical domain knowledge |
| Business glossary | `governance/business-glossary.json` | Approved business term definitions |
| CDE catalog | `governance/cde-catalog.json` | Critical Data Element mappings |
| DQ rules | `governance/dq-rules/*.json` | SQL-based validation (P0/P1/P2 priority) |
| DQ rule templates | `governance/dq-rule-templates/` | Mandatory patterns for consumable zone |
| DQ results | `governance/dq-results/` | Timestamped execution results |
| DQ scorecards | `governance/dq-scorecards/` | Markdown scorecards from real runs |
| Golden datasets | `governance/golden-datasets/` | Known-correct reference values |
| Data models | `governance/models/` | Conceptual, logical, physical (Mermaid ER diagrams) |
| Data dictionary | `governance/data-dictionary.json` | Plain-English field definitions |
| Data contracts | `governance/data-contracts/` | Schema + SLA + quality guarantees |
| Lineage | `governance/lineage/` | OpenLineage events per transformation |
| Entity registry | `governance/entity-registry.json` | Canonical entity mappings |
| PII scans | `governance/pii-scans/` | Sensitivity classifications |
| Access policies | `governance/policies/` | RLS, masking, retention, AI consumption |
| EDA reports | `governance/eda/` | Statistical profiling + domain discovery |
| Insight reports | `governance/insights/` | Zone transition analysis |
| Chaos manifests | `governance/chaos-manifests/` | Injection records + After-Action Reports |
| Reviews | `governance/reviews/` | Governance review reports |
| Audit trail | `governance/audit-trail/` | Every agent decision logged |

## Human-in-the-Loop

`REQUIRE_HUMAN_APPROVAL` in `src/grist/config.py` is the single global toggle (or set `GRIST_REQUIRE_HUMAN_APPROVAL=false` env var).

When `True`:
- Business terms require human approval before use in models
- Data models pause at each stage (conceptual → logical → physical) for review
- Entity resolution proposals below confidence 0.7 require human review
- DQ rules require approval before activation

When `False` (dev/demo mode):
- All artifacts are still produced, just auto-approved
- Approval audit trail still records what was auto-approved

## Quick Start

### Option 1: Claude Code Plugin (recommended)

```bash
# Install the plugin
/plugin install    # point to git URL

# Scaffold a domain project
/grist:init sec-edgar

# Work through specs with the agent pipeline
/grist:run raw-ingest-company-facts
/grist:status
```

### Option 2: Scaffolded by @setup agent

In a Claude Code session with Grist installed:

```
You: I want to ingest SEC EDGAR XBRL company facts
@setup: [asks a few questions about the data source, entities, fetch method]
@setup: [creates full project structure with domain pack, ingestor skeleton,
         first spec, governance directories, CLAUDE.md, pyproject.toml]
You: uv sync && uv run pytest  # works on first try
```

### Option 3: CLI scaffolding

```bash
pip install git+https://github.com/jcernauske/grist.git
python -m grist.setup init --name my-project
cd my-project && uv sync
```

### Option 4: Manual setup

```bash
mkdir my-project && cd my-project
uv init && uv add grist@git+https://github.com/jcernauske/grist.git

mkdir -p domain/sources governance src/raw docs/specs

# Write your ingestor extending BaseIngestor
# Configure domain/manifest.yaml and domain/sources/
# Write your first spec in docs/specs/
```

### Framework enhancements vs domain work

If you improve the framework (fix a bug in `dq_runner.py`, add a feature to `BaseIngestor`), push it to grist. If you build domain-specific artifacts (ingestors, governance, specs), those stay in your domain project. Clean separation — grist never gets polluted with domain data.

## What You Provide (Domain Pack)

| What | Where | Purpose |
|------|-------|---------|
| Manifest | `domain/manifest.yaml` | How to acquire your data |
| Source config | `domain/sources/*.yaml` | Entity IDs, fetch methods, dedup grain |
| Ingestor | `src/raw/my_ingestor.py` | `fetch()` and `flatten()` (imports `from grist.raw import BaseIngestor`) |
| Concept mappings | `domain/concept-mappings/*.json` | Taxonomy → business term mappings (optional — discovery mode if absent) |
| Glossaries | `glossaries/` | Standard/domain term definitions (optional) |
| DQ rules | `governance/dq-rules/*.json` | SQL validation rules (or let @dq-rule-writer generate from EDA) |

## Stack

- Python 3.11+
- DuckDB + Iceberg extension
- Apache Iceberg tables (local SQLite catalog, no server)
- Anthropic SDK (for AI-Ready zone chat agents)
- uv for dependency management

## Session Logging

Every Claude Code session is logged to `docs/sessions/` for transparency and continuity. Logs capture: exact prompt, specs referenced, files changed, decisions made, problems encountered.

## Project Structure

```
grist/
├── .claude-plugin/               Claude Code plugin manifest
│   └── plugin.json
├── skills/                       Plugin skills (/grist:init, /grist:run, /grist:status)
├── hooks/                        Plugin hooks (auto-install on session start)
├── src/grist/                    Framework package (pip-installable)
│   ├── config.py                 Global config (env var overrides for domain projects)
│   ├── domain_loader.py          Manifest + source config parsing
│   ├── setup.py                  Domain project scaffolding CLI
│   ├── raw/                      Raw zone (BaseIngestor)
│   ├── base/                     Base zone (concept normalization)
│   ├── ai_ready/                 AI-Ready zone (BaseChatAgent)
│   └── infra/                    Cross-cutting infrastructure
│       ├── period_disambiguator.py  Temporal period classification
│       ├── chaos_monkey/            Adversarial DQ testing
│       ├── integration_test_harness.py  Golden dataset validation
│       ├── dq_runner.py             DQ execution engine
│       ├── dq_scorecard.py          Scorecard generator
│       ├── iceberg_setup.py         PyIceberg + DuckDB bridge
│       ├── lineage.py               OpenLineage events
│       └── staging.py               Proposal staging
├── .claude/
│   └── agents/                   24 agent definitions (also served by plugin)
├── domain/                       Domain pack (your data source config)
├── governance/                   All governance artifacts
│   ├── dq-rule-templates/        Mandatory consumable zone patterns
│   ├── golden-datasets/          Known-correct reference values
│   ├── chaos-manifests/          Injection records + After-Action Reports
│   └── ...                       (20+ artifact directories)
├── docs/
│   ├── specs/                    Spec-driven development
│   └── sessions/                 Claude Code session logs
├── tests/                        Tests by zone + integration
├── CLAUDE.md                     Master pipeline instructions
└── pyproject.toml                uv-managed dependencies
```

## License

MIT
