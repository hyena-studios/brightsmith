# Bronze Zone Pipeline

Physical-only, quick and dirty. No data modeling gates.

## Agent Sequence

0. @document-extractor — **Document sources only** (PDFs, scans, spreadsheets). Runs BEFORE ingestion: turns source documents into structured, per-document row artifacts in `data/extracted/` that the step-2 `BaseIngestor` then loads. Skipped entirely for API/JSON/CSV sources, which `BaseIngestor.fetch()` reads directly. See "Document Sources" below.
1. @governance-reviewer — Pre-implementation review
2. @primary-agent — Implementation (ingest raw data via BaseIngestor)
3. @data-analyst — EDA on raw data (distributions, outliers, edge cases, threshold evidence, **domain discovery**)
4. @domain-context — Synthesize domain knowledge from EDA into `governance/domain-context.md` (canonical domain context for all downstream agents). Includes **EDA-informed user interview**: generates 5-10 targeted questions from EDA findings (temporal patterns, grain/uniqueness, domain semantics, known edge cases, external context), presents to user, flags unanswered questions as risks with mandatory DQ rule requirements.
5. @dq-rule-writer — Write raw DQ rules from EDA report + domain context (completeness, validity, volume, freshness)
6. @dq-engineer — Execute rules against real data, produce scorecard
7. @chaos-monkey — 5-cycle adversarial hardening:
   a. Inject corruptions into shadow copy (rates: 5%, 6%, 7%, 8%, 10%)
   b. Run DQ rules against shadow tables (`--shadow` flag)
   c. @chaos-monkey generates After-Action Report
   d. If gaps found: @dq-rule-writer patches rules, return to (a)
   e. After 5 cycles or no new gaps for 2 consecutive cycles: proceed
8. @lineage-tracker — OpenLineage capture
9. @cde-tagger — CDE mapping update (using domain context for taxonomy interpretation)
10. @doc-generator — Dictionary + contracts update (using domain context for plain-English definitions)
11. @governance-reviewer — Post-implementation completeness check
12. @staff-engineer — Final quality review (LAST gate before completion)

## Bronze-Specific Rules

- @domain-context MUST write `domain.name` to `manifest.yaml` after synthesizing domain knowledge (`python3 -m brightsmith.domain_loader assign-domain --name "..." --confidence "..."`). Brightforge reads this for sidebar display.
- Domain packs extend `BaseIngestor` and implement `fetch()`, `flatten()`, and `get_schema()` — the framework handles Iceberg table management, dedup, metadata enrichment, and snapshot management
- `BaseIngestor.ingest()` auto-emits runtime lineage events — no manual lineage creation needed for bronze zone. Base/consumable/MCP zone transformations should call `emit_start()`/`emit_complete()` from `brightsmith.infra.lineage`.
- @lineage-tracker is a verifier, not a creator — it checks that lineage events exist with runtime metadata (snapshot IDs, row counts, DQ metrics), not static templates.

## Domain Discovery

Brightsmith does not assume domain knowledge upfront. The discovery process works as follows:

1. **Domain pack provides raw access** — `domain/manifest.yaml` and `domain/sources/*.yaml` define HOW to get data (URLs, API endpoints, file paths, fetch methods), not what it means
2. **Bronze zone lands data as-is** — no interpretation, just storage with metadata
3. **@data-analyst discovers context** — after raw ingestion, the data analyst profiles the data to determine: what entities exist, what the grain is, what fields mean, what patterns emerge, what the domain vocabulary looks like
4. **@domain-context synthesizes domain knowledge** — takes the data analyst's EDA findings and produces `governance/domain-context.md`, the **canonical domain context document**. This replaces the hardcoded domain knowledge that would exist in a domain-specific pipeline. It covers: domain vocabulary, entity types, temporal patterns, applicable regulations, taxonomy/classification systems, edge cases, concept mapping guidance, and PII expectations.
5. **All downstream agents reference domain context** — @data-steward, @cde-tagger, @entity-resolver, @dq-rule-writer, @pii-scanner, @temporal-modeler, @insight-manager, @bcbs239-auditor, @adversarial-auditor, @content-strategist, @principal-data-architect, @doc-generator, @cab-agent, and @mcp-engineer all read `governance/domain-context.md` as their source of domain knowledge. No agent independently invents domain assumptions.

This is the key difference from a domain-specific pipeline: specs for Silver and Gold zones may be written AFTER discovery, not before. The domain context document is the bridge between "we don't know what this data is" and "every agent operates with full domain awareness."

## Project Bootstrapping

New domain projects are scaffolded by @setup — the first agent a user interacts with. It creates the full project structure (domain pack, governance directories, ingestor skeleton, first spec, CLAUDE.md, pyproject.toml with brightsmith dependency) from a few questions about the data source. After @setup finishes, the normal spec-driven pipeline takes over.

## Document Sources (step 0 — @document-extractor)

Most sources are structured (an API returning JSON, a CSV export) and `BaseIngestor.fetch()` reads them directly. Some sources are **semi-structured documents** — PDF statements, scanned images, exported spreadsheets — where the rows must first be extracted by an LLM because no headless parser can reliably read an arbitrary document layout. For those, @document-extractor runs as **step 0, before ingestion**.

The division of labor is deliberate and load-bearing:

- **@document-extractor (non-deterministic, once per document):** reads each source document, extracts fielded rows (never raw text), normalizes the grain fields consistently, verifies the extraction against the source's control total (e.g. a statement's `opening + Σ(amount) == closing`), and writes **one artifact per document** to `data/extracted/{source}/`. It hashes each source document and never re-extracts one it has already done. Documents that fail their control-total check are quarantined and flagged, never emitted.
- **`BaseIngestor` (deterministic, idempotent, re-runnable):** its `fetch()` globs `data/extracted/{source}/` and its `flatten()` passes the rows through; the framework then does grain dedup, the idempotent promote, the Iceberg write, and lineage — exactly as for any other source.

Keeping extraction on the *upstream* side of the artifact boundary is what makes re-runs safe: the fallible LLM step happens once per document under review, while the deterministic load can run over the whole accumulated artifact folder forever, collapsing overlapping documents (e.g. monthly statements that re-show prior transactions) to one clean copy. The `dedup_grain` and the optional `control_total` formula are declared once in `domain/sources/*.yaml` and reused by promote dedup, DQ uniqueness rules, contracts, and the extractor's self-check. See `agents/document-extractor.md` for the full doctrine and a worked financial-statements example.
