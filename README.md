# Grist

AI agent data pipeline framework. Takes raw data and grinds it into something usable.

**Raw → Base → Consumable → AI-Ready** with governance metadata at every step.

## What It Does

Grist is a template you clone and fill in with your data source. The framework provides:

- **Zone pipeline** — four processing stages with clear contracts between them
- **Iceberg tables** — Apache Iceberg on DuckDB for versioned, time-travel-capable storage
- **DQ engine** — SQL-based data quality rules with P0/P1/P2 priority gating
- **Concept normalization** — tiered matching engine (exact → prefix → pattern → heuristic) that maps any taxonomy to business terms
- **Glossary system** — three-tier business term registry (standards → domains → project)
- **Lineage tracking** — OpenLineage-compatible event trail for every data movement
- **Staging & approval gates** — human-in-the-loop review with configurable auto-promote
- **Discovery mode** — when no mappings exist, the pipeline still runs and presents unmapped concepts for iterative human mapping

## Quick Start

```bash
# Clone the template
git clone https://github.com/jcernauske/grist.git my-project
cd my-project

# Set up Python environment
uv sync

# Configure your data source
cp domain/manifest.yaml.example domain/manifest.yaml
cp domain/sources/my_source.yaml.example domain/sources/my_source.yaml
# Edit both files with your data source details

# Update PROJECT_NAME in src/config.py
# Write your ingestor (extend BaseIngestor)
# Add concept mappings to domain/concept-mappings/ (or use discovery mode)
# Write DQ rules in governance/dq-rules/

# Run tests
uv run pytest
```

## What You Provide (Domain Pack)

| What | Where | Purpose |
|------|-------|---------|
| Manifest | `domain/manifest.yaml` | How to acquire your data |
| Source config | `domain/sources/*.yaml` | Entity IDs, fetch methods, dedup grain |
| Ingestor | `src/raw/my_ingestor.py` | `fetch()` and `flatten()` for your API/files |
| Concept mappings | `domain/concept-mappings/*.json` | Taxonomy → business term mappings (optional) |
| Glossaries | `glossaries/` | Standard/domain term definitions (optional) |
| DQ rules | `governance/dq-rules/*.json` | SQL validation rules |

## What The Framework Provides

| Component | Path | Purpose |
|-----------|------|---------|
| Iceberg setup | `src/infra/iceberg_setup.py` | Table creation, append, read, dedup |
| DQ runner | `src/infra/dq_runner.py` | Execute rules against Iceberg tables |
| DQ scorecard | `src/infra/dq_scorecard.py` | Generate markdown scorecards |
| Lineage | `src/infra/lineage.py` | OpenLineage event emission |
| Staging | `src/infra/staging.py` | Proposal staging, approval gates |
| Glossary loader | `src/infra/glossary_loader.py` | Three-tier glossary composition |
| Domain loader | `src/domain_loader.py` | Manifest parsing and source config |
| Base ingestor | `src/raw/base_ingestor.py` | Abstract ingestor with dedup and metadata |
| Concept normalizer | `src/base/concept_normalization/` | Tiered concept matching engine |

## Stack

- Python 3.11+
- DuckDB + Iceberg extension
- Apache Iceberg tables (local SQLite catalog)
- uv for dependency management

## Zones

| Zone | Purpose | Example |
|------|---------|---------|
| **Raw** | Ingest and store source data as-is | API responses, file dumps |
| **Base** | Normalize, resolve entities, map concepts | Deduped, typed, business-term-mapped |
| **Consumable** | Build data products | Ratios, comparisons, aggregations |
| **AI-Ready** | Optimized for AI consumption | Embeddings, chat context, eval sets |

## License

MIT
