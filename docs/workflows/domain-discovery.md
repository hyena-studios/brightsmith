# Domain Discovery

Brightsmith does not assume domain knowledge upfront. The discovery process works as follows:

1. **Domain pack provides raw access** — `domain/manifest.yaml` and `domain/sources/*.yaml` define HOW to get data (URLs, API endpoints, file paths, fetch methods), not what it means
2. **Bronze zone lands data as-is** — no interpretation, just storage with metadata
3. **@data-analyst discovers context** — after raw ingestion, the data analyst profiles the data to determine: what entities exist, what the grain is, what fields mean, what patterns emerge, what the domain vocabulary looks like
4. **@domain-context synthesizes domain knowledge** — takes the data analyst's EDA findings and produces `governance/domain-context.md`, the **canonical domain context document**. This replaces the hardcoded domain knowledge that would exist in a domain-specific pipeline. It covers: domain vocabulary, entity types, temporal patterns, applicable regulations, taxonomy/classification systems, edge cases, concept mapping guidance, and PII expectations.
5. **All downstream agents reference domain context** — @data-steward, @cde-tagger, @entity-resolver, @dq-rule-writer, @pii-scanner, @temporal-modeler, @insight-manager, @bcbs239-auditor, @adversarial-auditor, @content-strategist, @principal-data-architect, @doc-generator, @cab-agent, and @mcp-engineer all read `governance/domain-context.md` as their source of domain knowledge. No agent independently invents domain assumptions.

This is the key difference from a domain-specific pipeline: specs for Silver and Gold zones may be written AFTER discovery, not before. The domain context document is the bridge between "we don't know what this data is" and "every agent operates with full domain awareness."

## Project Bootstrapping

New domain projects are scaffolded by @setup — the first agent a user interacts with. It creates the full project structure (domain pack, governance directories, ingestor skeleton, first spec, CLAUDE.md, pyproject.toml with brightsmith dependency) from a few questions about the data source. After @setup finishes, the normal spec-driven pipeline takes over.
