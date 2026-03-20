# Brightsmith

AI agent data pipeline framework. Takes raw data from any source and forges it into governed, AI-ready datasets — without knowing the domain upfront.

**Bronze → Silver → Gold → MCP** with full governance metadata at every step.

## What Makes Brightsmith Different

Most data pipelines are built for a specific domain. Brightsmith discovers the domain from the data itself.

1. You point it at a data source (API, files, database)
2. AI agents ingest the raw data, profile it, and determine what it is
3. A canonical **domain context document** is produced — vocabulary, entity types, temporal patterns, applicable regulations, taxonomy systems
4. Every downstream agent reads that same document — no independent assumptions, no drift
5. The pipeline builds governed data products through spec-driven development with human approval gates
6. The MCP zone produces a **tool-use chat agent** that queries live Iceberg data

Brightsmith was extracted from [sec-edgar-pipeline](https://github.com/jcernauske/sec_edgair), a production-grade SEC EDGAR financial data pipeline. Everything domain-specific was replaced with a discovery mechanism. Same rigor, any data. Field-tested with [sec-edgar-brightsmith](https://github.com/jcernauske/sec_edgar_grist).

## Install as Claude Code Plugin

```bash
# From any Claude Code session:
/plugin install    # point to this repo's git URL

# Or test locally:
claude --plugin-dir ~/code/brightsmith
```

## Skills (Metallurgy-Themed)

| Skill | Metaphor | What It Does |
|-------|----------|-------------|
| `/bs:init SEC EDGAR` | — | Scaffold a new domain project |
| `/bs:mine raw-ingest-foo` | ⛏️ Mining | Run the Bronze zone pipeline |
| `/bs:smelt base-foo` | ⚒️ Smelting | Run the Silver zone pipeline |
| `/bs:cast consumable-foo` | 🥇 Casting | Run the Gold zone pipeline |
| `/bs:serve` | 🚀 Serving | Start the MCP server |
| `/bs:assay foo` | 🔬 Assaying | Full DQ audit (rules, chaos monkey, golden datasets, contracts) |
| `/bs:stamp foo` | 🔏 Stamping | Generate and verify data contracts |
| `/bs:run foo` | — | Auto-detect zone and run the right pipeline |
| `/bs:status` | — | Dashboard of project state |

Each zone skill prints a celebration summary on completion with real stats — tables created, DQ rules active, business terms defined, artifacts produced, and links to everything.

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                        Domain Pack                            │
│    manifest.yaml · sources/*.yaml · BaseIngestor subclass     │
└─────────────────────┬─────────────────────────────────────────┘
                      │
      ┌───────────────▼───────────────┐
      │        ⛏️ Bronze Zone          │  Ingest as-is, metadata enrichment, dedup
      │     Iceberg tables (DuckDB)   │
      └───────────────┬───────────────┘
                      │
      ┌───────────────▼───────────────┐
      │      Domain Discovery         │  @data-analyst EDA → @domain-context synthesis
      │      + User Interview         │  → governance/domain-context.md
      └───────────────┬───────────────┘
                      │
      ┌───────────────▼───────────────┐
      │        ⚒️ Silver Zone          │  Normalize, resolve entities, map concepts
      │     Governed, modeled         │  3-stage data models (conceptual → logical → physical)
      └───────────────┬───────────────┘
                      │
      ┌───────────────▼───────────────┐
      │        🥇 Gold Zone            │  Data products: ratios, comparisons, aggregations
      │     Contracted, documented    │  Golden dataset validation
      └───────────────┬───────────────┘
                      │
      ┌───────────────▼───────────────┐
      │        🤖 MCP Zone             │  Tool-use chat agent, grounding docs, eval sets
      │     Governed AI consumption   │
      └───────────────────────────────┘
```

## Agent Pipeline

Brightsmith uses **24 specialized AI agents** orchestrated through a spec-driven workflow. Every piece of code, every governance artifact, every data transformation traces back to a spec.

Each agent runs in its own context window with a dedicated persona. A PreToolUse hook enforces that every agent call includes `subagent_type` — it's physically impossible to launch a nameless agent.

### Bronze Zone Pipeline
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

### Silver & Gold Zone Pipeline
Same as Bronze, plus:
- @data-steward — business term identification and glossary management
- @semantic-modeler — 3-stage data modeling (conceptual → logical → physical) with human approval gates
- @entity-resolver — canonical entity mapping across source identifiers
- @temporal-modeler — bitemporal schema design (valid time + Iceberg transaction time)

### Zone Transitions
At every zone boundary:
1. @principal-data-architect — **blocking** architecture review of the completed zone
2. @insight-manager — strategic analysis recommending data products for the next zone (silver→gold and gold→mcp only)

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

| Component | Module | Purpose |
|-----------|--------|---------|
| Base ingestor | `brightsmith.bronze.base_ingestor` | Abstract ingestor with dedup, metadata, snapshots |
| Period disambiguator | `brightsmith.infra.period_disambiguator` | Temporal period classification using date-span analysis |
| Chaos monkey | `brightsmith.infra.chaos_monkey` | Schema-agnostic adversarial testing with type-appropriate corruptions |
| Integration test harness | `brightsmith.infra.integration_test_harness` | Golden dataset validation against known-correct reference values |
| Base MCP server | `brightsmith.mcp.base_mcp_server` | MCP zone base class — Anthropic SDK, tool registration, Iceberg queries |
| Iceberg setup | `brightsmith.infra.iceberg_setup` | Table creation, append, read via PyIceberg + DuckDB |
| DQ runner | `brightsmith.infra.dq_runner` | Execute SQL rules against Iceberg, threshold evaluation, P0 gating |
| DQ scorecard | `brightsmith.infra.dq_scorecard` | Markdown scorecards from real execution results |
| Lineage | `brightsmith.infra.lineage` | OpenLineage event emission to Iceberg |
| Staging | `brightsmith.infra.staging` | Proposal staging, confidence-based approval gates |
| Pipeline gate | `brightsmith.infra.pipeline_gate` | State machine tracking every agent step per spec |
| Promote | `brightsmith.infra.promote` | Idempotent table promotion with grain-based dedup |
| Grain | `brightsmith.infra.grain` | Deterministic record IDs via `compute_grain_id()` |
| Contract | `brightsmith.infra.contract` | Data contract generation, verification, diff, lifecycle |
| Golden dataset | `brightsmith.infra.golden_dataset` | Verify pipeline output against reference values |
| Verification | `brightsmith.infra.verification` | Correctness validation ("is this number right?") |
| Glossary validator | `brightsmith.infra.glossary_validator` | Validate 14-field business term completeness |
| Glossary loader | `brightsmith.infra.glossary_loader` | Three-tier glossary composition (standards → domains → project) |
| Concept normalizer | `brightsmith.silver.concept_normalization` | Tiered matching (exact → prefix → pattern → heuristic) |
| Domain loader | `brightsmith.domain_loader` | Manifest parsing, source config, hints resolution |
| Headless runner | `brightsmith.run` | Full pipeline without AI agents — `--zone`, `--validate-only`, `--dry-run` |
| Setup CLI | `brightsmith.setup` | `python -m brightsmith.setup init` — scaffold domain projects |

## Domain Discovery → Domain Context

This is the core innovation. In a domain-specific pipeline, every agent knows the vocabulary. In Brightsmith:

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

### DQ Rule Templates (Gold Zone)

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
| Business glossary | `governance/business-glossary.json` | Approved business term definitions (14 fields per term) |
| CDE catalog | `governance/cde-catalog.json` | Critical Data Element mappings |
| DQ rules | `governance/dq-rules/*.json` | SQL-based validation (P0/P1/P2 priority) |
| DQ rule templates | `governance/dq-rule-templates/` | Mandatory patterns for gold zone |
| DQ results | `governance/dq-results/` | Timestamped execution results |
| DQ scorecards | `governance/dq-scorecards/` | Markdown scorecards from real runs |
| Golden datasets | `governance/golden-datasets/` | Known-correct reference values |
| Data models | `governance/models/` | Conceptual, logical, physical (Mermaid ER diagrams) |
| Data dictionary | `governance/data-dictionary.json` | Plain-English field definitions |
| Data contracts | `governance/data-contracts/` | Schema + SLA + quality guarantees (DRAFT → ACTIVE → DEPRECATED) |
| Lineage | `governance/lineage/` | OpenLineage events per transformation |
| Entity registry | `governance/entity-registry.json` | Canonical entity mappings |
| PII scans | `governance/pii-scans/` | Sensitivity classifications |
| Access policies | `governance/policies/` | RLS, masking, retention, AI consumption |
| EDA reports | `governance/eda/` | Statistical profiling + domain discovery |
| Insight reports | `governance/insights/` | Zone transition analysis + data product recommendations |
| Chaos manifests | `governance/chaos-manifests/` | Injection records + After-Action Reports |
| Reviews | `governance/reviews/` | Architecture and governance review reports |
| Audit trail | `governance/audit-trail/` | Every agent decision, approval, and skip logged |
| Pipeline state | `governance/pipeline-state/` | Programmatic gate enforcement per spec |
| Run history | `governance/run-history/` | Headless pipeline execution logs |
| Approvals | `governance/approvals/` | Plain-English approval documents for human gates |

## Human-in-the-Loop

`REQUIRE_HUMAN_APPROVAL` in `src/brightsmith/config.py` is the single global toggle (or set `BRIGHTSMITH_REQUIRE_HUMAN_APPROVAL=false` env var).

When `True`:
- Business terms require human approval before use in models
- Data models pause at each stage (conceptual → logical → physical) for review
- Entity resolution proposals below confidence 0.7 require human review
- DQ rules require approval before activation
- Plain-English approval documents are generated at each gate

When `False` (dev/demo mode):
- All artifacts are still produced, just auto-approved
- Approval audit trail still records what was auto-approved

## Quick Start

### Option 1: Claude Code Plugin (recommended)

```bash
# Install the plugin
/plugin install    # point to git URL

# Scaffold a domain project
/bs:init SEC EDGAR financial filings

# The setup agent asks for your email, then scaffolds everything.
# cd into the project, then:

/bs:mine raw-ingest-company-facts    # Bronze zone
/bs:smelt base-financial-facts       # Silver zone
/bs:cast consumable-financial-ratios  # Gold zone
/bs:serve                            # Start MCP server

# Check status anytime:
/bs:status

# Run DQ audit:
/bs:assay raw-ingest-company-facts
```

### Option 2: Headless Pipeline

```bash
pip install git+https://github.com/jcernauske/brightsmith.git

# Run the full pipeline without AI agents
python -m brightsmith.run --zone bronze
python -m brightsmith.run --zone silver
python -m brightsmith.run --zone gold

# Validate only (no transforms)
python -m brightsmith.run --validate-only

# Check readiness
python -m brightsmith.run --headless-ready
```

### Framework vs Domain Work

If you improve the framework (fix a bug in `dq_runner.py`, add a feature to `BaseIngestor`), push it to brightsmith. If you build domain-specific artifacts (ingestors, governance, specs), those stay in your domain project. Clean separation — brightsmith never gets polluted with domain data.

## What You Provide (Domain Pack)

| What | Where | Purpose |
|------|-------|---------|
| Manifest | `domain/manifest.yaml` | How to acquire your data |
| Source config | `domain/sources/*.yaml` | Entity IDs, fetch methods, dedup grain |
| Ingestor | `src/raw/my_ingestor.py` | `fetch()` and `flatten()` (extends `brightsmith.bronze.BaseIngestor`) |
| Concept mappings | `domain/concept-mappings/*.json` | Taxonomy → business term mappings (optional — discovery mode if absent) |
| Glossaries | `glossaries/` | Standard/domain term definitions (optional) |

## Stack

- Python 3.11+
- DuckDB + Iceberg extension
- Apache Iceberg tables (local SQLite catalog, no server)
- Anthropic SDK (for MCP zone chat agents)
- uv for dependency management

## Session Logging

Every Claude Code session is logged to `docs/sessions/` for transparency and continuity. Logs capture: exact prompt, all human input (verbatim), specs referenced, files changed, decisions made, problems encountered. The Human Input Log is the authoritative record of human involvement — if it's not logged, it didn't happen.

## Project Structure

```
brightsmith/
├── .claude-plugin/               Claude Code plugin manifest
│   └── plugin.json
├── skills/                       Plugin skills (/bs:init, /bs:mine, /bs:smelt, etc.)
│   ├── init/                     Scaffold new domain projects
│   ├── mine/                     Bronze zone pipeline
│   ├── smelt/                    Silver zone pipeline
│   ├── cast/                     Gold zone pipeline
│   ├── serve/                    Start MCP server
│   ├── assay/                    Full DQ audit
│   ├── stamp/                    Data contract management
│   ├── run/                      Auto-detect zone and run
│   └── status/                   Project state dashboard
├── hooks/                        Plugin hooks
│   ├── hooks.json                Hook config (SessionStart, PreToolUse)
│   └── require-subagent-type.sh  Enforces subagent_type on all Agent calls
├── agents/                       Plugin agents (setup.md only — rest copied to consumer projects at init)
├── src/brightsmith/              Framework package (pip-installable)
│   ├── config.py                 Global config (env var overrides for domain projects)
│   ├── domain_loader.py          Manifest + source config parsing
│   ├── setup.py                  Domain project scaffolding CLI
│   ├── run.py                    Headless pipeline runner
│   ├── bronze/                    Bronze zone (BaseIngestor)
│   ├── silver/                    Silver zone (concept normalization)
│   ├── mcp/                       MCP zone (BaseMCPServer)
│   └── infra/                    Cross-cutting infrastructure
│       ├── pipeline_gate.py         State machine + CLI for spec tracking
│       ├── period_disambiguator.py  Temporal period classification
│       ├── chaos_monkey/            Adversarial DQ testing
│       ├── integration_test_harness.py  Golden dataset validation
│       ├── dq_runner.py             DQ execution engine
│       ├── dq_scorecard.py          Scorecard generator
│       ├── iceberg_setup.py         PyIceberg + DuckDB bridge
│       ├── lineage.py               OpenLineage events
│       ├── promote.py               Idempotent table promotion
│       ├── grain.py                 Deterministic record IDs
│       ├── contract.py              Data contract lifecycle
│       ├── golden_dataset.py        Reference value verification
│       ├── verification.py          Correctness validation
│       ├── glossary_validator.py    Business term completeness
│       ├── glossary_loader.py       Three-tier glossary composition
│       └── staging.py               Proposal staging
├── .claude/
│   └── agents/                   24 agent definitions (copied to consumer projects at init)
├── domain/                       Domain pack (your data source config)
├── governance/                   All governance artifacts (20+ directories)
├── docs/
│   ├── specs/                    Spec-driven development
│   └── sessions/                 Claude Code session logs
├── tests/                        Tests by zone + integration
├── CLAUDE.md                     Master pipeline instructions
└── pyproject.toml                uv-managed dependencies
```

## License

MIT
