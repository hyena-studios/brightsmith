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

Grist was extracted from [sec_edgair](https://github.com/jcernauske/sec_edgair), a production-grade SEC EDGAR financial data pipeline. Everything domain-specific was replaced with a discovery mechanism. Same rigor, any data.

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
    │   governance/domain-       │  → governance/domain-context.md
    │   context.md              │
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │        Base Zone          │  Normalize, resolve entities, map concepts
    │   Governed, modeled       │  3-stage data models (conceptual → logical → physical)
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │     Consumable Zone       │  Data products: ratios, comparisons, aggregations
    │   Contracted, documented  │
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │      AI-Ready Zone        │  MCP server, embeddings, chat context, eval sets
    │   Governed AI consumption │
    └───────────────────────────┘
```

## Agent Pipeline

Grist uses **23 specialized AI agents** orchestrated through a spec-driven workflow. Every piece of code, every governance artifact, every data transformation traces back to a spec.

### Raw Zone Pipeline
| Step | Agent | What It Does |
|------|-------|-------------|
| 1 | @governance-reviewer | Pre-implementation spec review |
| 2 | @primary-agent | Ingest raw data via BaseIngestor |
| 3 | @data-analyst | EDA + domain discovery |
| 4 | @domain-context | Synthesize canonical domain knowledge |
| 5 | @dq-rule-writer | Write DQ rules from EDA evidence |
| 6 | @dq-engineer | Execute rules, produce scorecard |
| 7 | @lineage-tracker | OpenLineage capture |
| 8 | @cde-tagger | CDE mapping |
| 9 | @doc-generator | Data dictionary + contracts |
| 10 | @governance-reviewer | Post-implementation completeness check |
| 11 | @staff-engineer | Final quality gate |

### Base & Consumable Zone Pipeline
Same as Raw, plus:
- @data-steward — business term identification and glossary management
- @semantic-modeler — 3-stage data modeling (conceptual → logical → physical) with human approval gates
- @entity-resolver — canonical entity mapping across source identifiers
- @temporal-modeler — bitemporal schema design (valid time + Iceberg transaction time)

### Zone Transitions
@insight-manager runs between zones — analyzes completed data, recommends data products, suggests spec order for the next zone.

### Governance & Quality Agents
| Agent | Role |
|-------|------|
| @adversarial-auditor | Tests whether AI-built artifacts could be hallucinated |
| @bcbs239-auditor | Regulatory framework assessment (BCBS 239, SOX, GDPR, HIPAA) |
| @chaos-monkey | Adversarial DQ testing — injects corruption behind an information barrier |
| @pii-scanner | PII detection and sensitivity classification |
| @policy-engineer | Data access policy definitions (RLS, masking, retention) |
| @principal-data-architect | Independent full-pipeline architecture review |

### Content & Delivery Agents
| Agent | Role |
|-------|------|
| @content-strategist | Translates technical work into executive/architect/compliance narratives |
| @mcp-engineer | MCP server exposing governed data as AI-callable tools |
| @web-designer | Static site for project documentation and results |

## Domain Discovery → Domain Context

This is the core innovation. In a domain-specific pipeline, every agent knows the vocabulary. In Grist:

```
@data-analyst                    @domain-context
 ┌──────────────┐                ┌──────────────────────┐
 │ EDA Report   │───────────────▶│ domain-context.md    │
 │ - profiles   │                │ - vocabulary         │
 │ - patterns   │                │ - entity types       │
 │ - anomalies  │                │ - temporal patterns   │
 │ - grain      │                │ - regulations        │
 │ - taxonomies │                │ - PII expectations   │
 └──────────────┘                │ - mapping guidance   │
                                 │ - edge cases         │
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

All 13 downstream agents read the same `governance/domain-context.md`. No agent independently invents domain assumptions.

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
| DQ results | `governance/dq-results/` | Timestamped execution results |
| DQ scorecards | `governance/dq-scorecards/` | Markdown scorecards from real runs |
| Data models | `governance/models/` | Conceptual, logical, physical (Mermaid ER diagrams) |
| Data dictionary | `governance/data-dictionary.json` | Plain-English field definitions |
| Data contracts | `governance/data-contracts/` | Schema + SLA + quality guarantees |
| Lineage | `governance/lineage/` | OpenLineage events per transformation |
| Entity registry | `governance/entity-registry.json` | Canonical entity mappings |
| PII scans | `governance/pii-scans/` | Sensitivity classifications |
| Access policies | `governance/policies/` | RLS, masking, retention, AI consumption |
| EDA reports | `governance/eda/` | Statistical profiling + domain discovery |
| Insight reports | `governance/insights/` | Zone transition analysis |
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

### Option 1: As a dependency (recommended for domain projects)

```bash
# Create your domain project
mkdir my-sec-edgar-project && cd my-sec-edgar-project
uv init && uv add grist@git+https://github.com/jcernauske/grist.git

# Set up your domain pack
mkdir -p domain/sources governance src/raw
# Create domain/manifest.yaml, domain/sources/my_source.yaml

# Write your ingestor
cat > src/raw/my_ingestor.py << 'EOF'
from grist.raw.base_ingestor import BaseIngestor

class MyIngestor(BaseIngestor):
    def fetch(self, entities, method, **kwargs):
        ...
    def flatten(self, raw_data, entity_id):
        ...
    def get_schema(self):
        ...
EOF

# Configure (optional — defaults to cwd as project root)
export GRIST_PROJECT_NAME="my-sec-edgar"

# Run DQ rules
python -m grist.infra.dq_runner run
```

### Option 2: Clone and fill in (for exploring/contributing)

```bash
git clone https://github.com/jcernauske/grist.git my-project
cd my-project
uv sync

# Configure your data source
cp domain/manifest.yaml.example domain/manifest.yaml
cp domain/sources/my_source.yaml.example domain/sources/my_source.yaml

# Run tests
uv run pytest
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

## What The Framework Provides

| Component | Path | Purpose |
|-----------|------|---------|
| Base ingestor | `grist.raw.base_ingestor` | Abstract ingestor with dedup, metadata, snapshots |
| Iceberg setup | `grist.infra.iceberg_setup` | Table creation, append, read via PyIceberg + DuckDB |
| DQ runner | `grist.infra.dq_runner` | Execute SQL rules against Iceberg, threshold evaluation, P0 gating |
| DQ scorecard | `grist.infra.dq_scorecard` | Generate markdown scorecards from real results |
| Lineage | `grist.infra.lineage` | OpenLineage event emission to Iceberg |
| Staging | `grist.infra.staging` | Proposal staging, confidence-based approval gates |
| Glossary loader | `grist.infra.glossary_loader` | Three-tier glossary composition (standards → domains → project) |
| Domain loader | `grist.domain_loader` | Manifest parsing, source config, hints resolution |
| Concept normalizer | `grist.base.concept_normalization` | Tiered matching (exact → prefix → pattern → heuristic) |

## Stack

- Python 3.11+
- DuckDB + Iceberg extension
- Apache Iceberg tables (local SQLite catalog, no server)
- uv for dependency management

## Session Logging

Every Claude Code session is logged to `docs/sessions/` for transparency and continuity. Logs capture: exact prompt, specs referenced, files changed, decisions made, problems encountered.

## Project Structure

```
grist/
├── src/grist/                     Framework package (pip-installable)
│   ├── config.py                  Global config (env var overrides for domain projects)
│   ├── domain_loader.py           Manifest + source config parsing
│   ├── raw/                       Raw zone (BaseIngestor)
│   ├── base/                      Base zone (concept normalization)
│   └── infra/                     Cross-cutting infrastructure
├── domain/                        Domain pack (your data source config)
│   ├── manifest.yaml              What to ingest
│   ├── sources/                   Per-source configuration
│   └── concept-mappings/          Taxonomy mappings (optional)
├── governance/                    All governance artifacts
│   ├── domain-context.md          Canonical domain knowledge
│   ├── business-glossary.json     Approved business terms
│   ├── cde-catalog.json           Critical Data Element mappings
│   ├── dq-rules/                  SQL-based DQ rule definitions
│   ├── dq-results/                Timestamped execution results
│   ├── dq-scorecards/             Markdown scorecards
│   ├── models/                    Data models (conceptual/logical/physical)
│   ├── eda/                       EDA reports
│   ├── insights/                  Zone transition analysis
│   ├── lineage/                   OpenLineage events
│   ├── policies/                  Access policies
│   ├── pii-scans/                 PII scan reports
│   ├── reviews/                   Governance review reports
│   └── audit-trail/               Every agent decision logged
├── glossaries/                    Three-tier glossary system
├── docs/
│   ├── specs/                     Spec-driven development
│   └── sessions/                  Claude Code session logs
├── tests/                         Tests by zone
├── .claude/
│   └── agents/                    23 agent definitions
├── CLAUDE.md                      Master pipeline instructions
└── pyproject.toml                 uv-managed dependencies
```

## License

MIT
