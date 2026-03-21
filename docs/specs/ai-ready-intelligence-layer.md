# Framework Spec: AI-Ready Intelligence Layer

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting, AI-Ready zone enhancement)
**Primary Agent:** @primary-agent
**Created:** 2026-03-20

## Problem Statement

The AI-Ready zone has a solid MCP server base class (`BaseMCPServer`) that handles protocol, tool registration, Iceberg queries, and governance metadata. But there's a gap between "raw query results" and "AI-ready responses" that every domain project has to fill ad-hoc.

The SEC EDGAIR project exposed this gap clearly. After building the MCP server, it still needed:
- **Value formatters** — `$394.3B` instead of `394328000000`. Built as `formatters.py` with 6 format functions and 34 tests.
- **Anomaly detection** — "Boeing has negative equity, which makes leverage ratios misleading." Built as `anomaly_checker.py` with 7 rules and 19 tests.
- **System prompt generation** — A dynamic ~3K token prompt built from real data: company roster, metric catalog, known anomalies, fiscal year rules. Built as `system_prompt.py`.

All three were implemented as one-off domain code. The next domain project would need the same three components with different content but identical architecture. And the one after that. And the one after that.

The framework provides `BaseIngestor` so bronze zone ingestion isn't reinvented per domain. It provides `BaseMCPServer` so MCP servers aren't reinvented per domain. But it's missing the intelligence layer between raw data and AI-ready responses — the formatters, anomaly checkers, and system prompt builders that turn query results into something an LLM can actually use well.

## What Changes

The AI-Ready zone deliverable expands from "an MCP server" to "a governed AI query layer" — five components, three of which are new:

| Component | Status | What It Does |
|-----------|--------|-------------|
| **MCP Server** | EXISTS (`BaseMCPServer`) | Tool/resource registration, Iceberg queries, MCP protocol |
| **Formatter** | NEW (`BaseFormatter`) | Type-dispatched value formatting — raw values → human-readable strings |
| **Anomaly Checker** | NEW (`BaseAnomalyChecker`) | Query-time rules that flag edge cases and known data issues |
| **System Prompt** | NEW (`BaseSystemPrompt`) | Section-based prompt assembly from real data + governance artifacts |
| **Response Pipeline** | NEW (`enrich_response()`) | Chains format → flag → govern on every tool response |

The framework provides the mechanism. Domain projects provide the content.

## Design Principles

1. **Domain-agnostic** — The framework doesn't know what a currency is, what a diagnosis code is, or what a stock ticker is. It knows that values have types, types have format functions, and domain projects register the mapping.

2. **Optional by default** — A domain project that doesn't need formatters, anomaly checking, or a custom system prompt gets a working MCP server with raw values and governance metadata. The intelligence layer is additive, not mandatory.

3. **Composable** — Each component works independently. A domain project can use formatters without anomaly checking, or anomaly checking without a custom system prompt. They compose but don't depend on each other.

4. **Data-driven** — System prompts and anomaly rules are built from real data and governance artifacts, not hardcoded strings. If the data changes, the intelligence layer changes with it.

5. **No LLM dependencies** — Like the rest of the framework, zero `anthropic` or other LLM SDK imports. The intelligence layer prepares data for LLM consumption; it doesn't call LLMs.

## Component 1: BaseFormatter

**File:** `src/brightsmith/mcp/base_formatter.py`

### What It Does

Maps value types to format functions. A tool handler returns raw values; the formatter converts them to human-readable strings that LLMs can use directly in responses.

### Why It Matters

LLMs are bad at formatting numbers. They'll say "revenue was 394328000000" instead of "$394.3B". They'll say "margin was 0.253" instead of "25.3%". Formatting at the data layer means every LLM client gets clean values without each one implementing its own formatting logic.

### Design

```python
from brightsmith.mcp.base_formatter import BaseFormatter

class FinancialFormatter(BaseFormatter):
    def get_format_rules(self) -> list[FormatRule]:
        return [
            FormatRule(
                match=lambda col, val, row: row.get("unit") == "USD",
                format_fn=format_currency,
            ),
            FormatRule(
                match=lambda col, val, row: col.endswith("_pct") or row.get("unit") == "percent",
                format_fn=format_percentage,
            ),
            FormatRule(
                match=lambda col, val, row: col.endswith("_ratio"),
                format_fn=format_ratio,
            ),
        ]
```

### Base Class API

```python
@dataclass
class FormatRule:
    """A formatting rule: match predicate + format function."""
    match: Callable[[str, Any, dict], bool]  # (column_name, value, full_row) -> should_format
    format_fn: Callable[[Any], str]          # (value) -> formatted_string

class BaseFormatter:
    """Domain-agnostic value formatter with type dispatch."""

    def get_format_rules(self) -> list[FormatRule]:
        """Override in domain project. Return format rules in priority order.
        First matching rule wins."""
        return []

    def format_row(self, row: dict) -> dict:
        """Format all values in a row using registered rules.
        Returns a new dict with formatted string values alongside originals."""
        ...

    def format_value(self, column: str, value: Any, row: dict) -> str | None:
        """Format a single value. Returns None if no rule matches (value unchanged)."""
        ...

    def format_rows(self, rows: list[dict]) -> list[dict]:
        """Format all rows. Convenience batch method."""
        ...
```

### Formatting Contract

- `format_row()` returns a new dict. For each formatted value, it adds a `{column}_formatted` key with the string representation. The original value is preserved under the original key. LLM clients get both — the formatted string for display and the raw value for computation.
- If no rule matches a value, it passes through unchanged with no `_formatted` key.
- Rules are evaluated in order. First match wins. This lets domain projects define specific rules before general fallbacks.
- `None` and missing values are never formatted — they pass through as-is.

### Framework-Provided Format Utilities

The framework ships a small library of common format functions that domain projects can reuse:

**File:** `src/brightsmith/mcp/format_utils.py`

```python
def format_large_number(value, prefix="", suffix="") -> str:
    """1234567890 → '1.2B', 1234567 → '1.2M', 1234 → '1,234'"""

def format_percentage(value, decimals=1) -> str:
    """0.253 → '25.3%', -0.124 → '-12.4%'"""

def format_decimal(value, decimals=2) -> str:
    """1234.5678 → '1,234.57'"""

def format_multiplier(value, decimals=1) -> str:
    """2.345 → '2.3x'"""

def format_date_range(start, end) -> str:
    """(2024-01-01, 2024-12-31) → 'Jan 2024 – Dec 2024'"""

def format_yoy_change(value, decimals=1) -> str:
    """0.078 → '+7.8%', -0.123 → '-12.3%'"""
```

These are **convenience functions, not base class methods**. Domain projects import and use them in their `FormatRule.format_fn`. A financial domain uses `format_large_number` with `prefix="$"`. A healthcare domain might never use it. The framework doesn't assume which formatters any domain needs.

## Component 2: BaseAnomalyChecker

**File:** `src/brightsmith/mcp/base_anomaly_checker.py`

### What It Does

Runs query-time rules against tool results and attaches human-readable flags. These aren't DQ rules (which validate data at rest) — they're interpretation aids that help LLMs caveat their responses.

### Why It Matters

Every dataset has edge cases that change how values should be interpreted. A company with negative equity makes debt-to-equity ratios meaningless. A hospital with a recent acquisition makes YoY comparisons misleading. A sensor with a known calibration issue makes readings unreliable during a specific window.

Without anomaly flags, the LLM serves the number without context. With them, the LLM says "Boeing's D/E ratio is -15.1x, but note that Boeing has negative stockholders' equity, which makes this ratio misleading."

### Design

```python
from brightsmith.mcp.base_anomaly_checker import BaseAnomalyChecker

class FinancialAnomalyChecker(BaseAnomalyChecker):
    def get_anomaly_rules(self) -> list[AnomalyRule]:
        return [
            AnomalyRule(
                rule_id="ANOM-001",
                description="Extreme year-over-year change",
                check=lambda row: abs(row.get("yoy_pct", 0)) > 2.0,
                flag="Extreme YoY change (>200%) — may indicate M&A, reclassification, or data issue",
                severity="warning",
            ),
            AnomalyRule(
                rule_id="ANOM-002",
                description="Negative equity",
                check=lambda row: (
                    row.get("metric") == "stockholders_equity"
                    and (row.get("val") or 0) < 0
                ),
                flag="Negative stockholders' equity — leverage ratios are misleading",
                severity="caveat",
            ),
        ]
```

### Base Class API

```python
@dataclass
class AnomalyFlag:
    """A flag attached to a data point by an anomaly rule."""
    rule_id: str
    severity: str      # "info" | "warning" | "caveat" | "error"
    message: str

@dataclass
class AnomalyRule:
    """A query-time anomaly detection rule."""
    rule_id: str
    description: str
    check: Callable[[dict], bool]  # (row) -> is_anomalous
    flag: str                       # Human-readable flag message
    severity: str = "warning"       # info | warning | caveat | error

class BaseAnomalyChecker:
    """Domain-agnostic query-time anomaly detection."""

    def get_anomaly_rules(self) -> list[AnomalyRule]:
        """Override in domain project. Return anomaly rules."""
        return []

    def check_row(self, row: dict) -> list[AnomalyFlag]:
        """Run all rules against a single row. Returns matching flags."""
        ...

    def check_rows(self, rows: list[dict]) -> list[dict]:
        """Run all rules against rows. Attaches '_anomaly_flags' key to each row."""
        ...
```

### Anomaly vs DQ Rules

These are complementary, not competing:

| Aspect | DQ Rules (@dq-rule-writer) | Anomaly Rules (BaseAnomalyChecker) |
|--------|---------------------------|-----------------------------------|
| When they run | Pipeline time (data at rest) | Query time (data in flight) |
| What they validate | Data correctness — "is this value valid?" | Data interpretation — "does this value need context?" |
| Failure mode | Block pipeline (P0) or flag (P1-P3) | Attach caveat to response — never block |
| Who writes them | @dq-rule-writer from EDA evidence | @mcp-engineer from domain knowledge + EDA |
| Where they live | `governance/dq-rules/` | In the domain MCP server code |
| Lifecycle | PROPOSED → APPROVED → ACTIVE | Part of MCP server implementation |

### Flag Attachment

Anomaly flags are attached to each row as a `_anomaly_flags` key:

```json
{
  "ticker": "BA",
  "metric": "debt_to_equity",
  "val": -15.1,
  "val_formatted": "-15.1x",
  "_anomaly_flags": [
    {
      "rule_id": "ANOM-002",
      "severity": "caveat",
      "message": "Negative stockholders' equity — leverage ratios are misleading"
    }
  ]
}
```

Rows with no anomalies have an empty `_anomaly_flags` list. The LLM client decides whether and how to surface flags.

## Component 3: BaseSystemPrompt

**File:** `src/brightsmith/mcp/base_system_prompt.py`

### What It Does

Assembles a system prompt from governance artifacts and domain-specific data sections. The prompt gives LLM clients the context they need to interpret tool results correctly — entity rosters, metric definitions, known anomalies, scope declarations, formatting conventions.

### Why It Matters

An MCP server exposes tools and resources. But the LLM client needs a system prompt that explains what the data is, what the tools do, and what caveats to watch for. Without it, the LLM hallucinates entity names, misinterprets metrics, and ignores edge cases.

The system prompt is the bridge between "here are your tools" and "here's how to use them well."

### Design

```python
from brightsmith.mcp.base_system_prompt import BaseSystemPrompt

class FinancialSystemPrompt(BaseSystemPrompt):
    def get_domain_sections(self) -> list[PromptSection]:
        return [
            PromptSection(
                name="company_roster",
                title="Companies in This Dataset",
                builder=self._build_roster,   # queries Iceberg, returns markdown table
                priority=1,
            ),
            PromptSection(
                name="metric_catalog",
                title="Available Metrics",
                builder=self._build_metric_catalog,
                priority=2,
            ),
            PromptSection(
                name="known_anomalies",
                title="Known Data Anomalies",
                builder=self._build_anomaly_summary,
                priority=3,
            ),
            PromptSection(
                name="fiscal_year_rules",
                title="Fiscal Year Alignment",
                builder=self._build_fiscal_rules,
                priority=4,
            ),
        ]
```

### Base Class API

```python
@dataclass
class PromptSection:
    """A section of the system prompt."""
    name: str                              # Identifier (for caching, ordering)
    title: str                             # Rendered as markdown heading
    builder: Callable[[], str]             # () -> markdown content
    priority: int = 10                     # Lower = earlier in prompt
    max_tokens: int | None = None          # Optional budget cap for this section
    cache_ttl_seconds: int = 0             # 0 = rebuild every time

class BaseSystemPrompt:
    """Domain-agnostic system prompt assembly from real data."""

    def __init__(
        self,
        server: BaseMCPServer,
        anomaly_checker: BaseAnomalyChecker | None = None,
    ):
        self.server = server       # For Iceberg queries
        self.anomaly_checker = anomaly_checker
        ...

    def get_domain_sections(self) -> list[PromptSection]:
        """Override in domain project. Return domain-specific prompt sections."""
        return []

    def build(self) -> str:
        """Assemble the full system prompt.

        Order: framework preamble → domain sections (by priority) → framework postscript.
        """
        ...

    def get_framework_sections(self) -> list[PromptSection]:
        """Framework-provided sections (auto-generated from governance artifacts).
        Domain projects generally don't override this."""
        ...
```

### Framework-Provided Sections

The framework automatically builds these sections from governance artifacts that every Brightsmith project has:

| Section | Source | Content |
|---------|--------|---------|
| **Scope Declaration** | Data contracts + table metadata | "This dataset contains N tables covering [domain summary]." |
| **Data Quality Summary** | DQ scorecards | "All P0 rules pass. 2 P1 warnings on table X." |
| **Business Term Reference** | Business glossary (abbreviated) | Key terms with definitions — not the full glossary, just terms referenced by tool schemas |
| **Response Guidelines** | Static template | "Always cite specific values. Flag anomalies. Note data freshness." |

These sections are always present. Domain sections are added alongside them, ordered by priority.

### Prompt as MCP Resource

The assembled system prompt is exposed as an MCP resource:

```
URI: brightsmith://system-prompt
Name: Recommended System Prompt
Description: Data-aware system prompt for LLM clients using this MCP server
```

MCP clients can fetch this resource and use it as their system prompt (or ignore it — the server doesn't mandate client behavior). This is the standard way for the intelligence layer to communicate context to LLM consumers.

### Token Budget

`BaseSystemPrompt.build()` accepts an optional `max_tokens` parameter. When set, sections are included in priority order until the budget is exhausted. Lower-priority sections are truncated or omitted. This prevents the prompt from growing unbounded as the dataset scales.

If no budget is set, all sections are included.

## Integration: The Response Pipeline

### How It Connects to BaseMCPServer

`BaseMCPServer` gains three optional constructor parameters and a new `enrich_response()` method:

```python
class BaseMCPServer:
    def __init__(
        self,
        warehouse_path,
        catalog_path,
        grounding_docs_path=None,
        server_name="brightsmith",
        formatter: BaseFormatter | None = None,          # NEW
        anomaly_checker: BaseAnomalyChecker | None = None,  # NEW
        system_prompt: BaseSystemPrompt | None = None,    # NEW
    ):
        ...

    def enrich_response(self, result: dict, table_name: str) -> dict:
        """Full response enrichment pipeline.

        1. Format values (if formatter configured)
        2. Flag anomalies (if anomaly checker configured)
        3. Attach governance metadata (always)

        Replaces attach_governance() as the standard tool response wrapper.
        """
        ...
```

### Pipeline Flow

```
Tool handler returns raw result
        │
        ▼
enrich_response(result, table_name)
        │
        ├─ 1. Formatter: format values → add _formatted keys
        │
        ├─ 2. Anomaly checker: flag edge cases → add _anomaly_flags
        │
        ├─ 3. Governance: attach contract, DQ, lineage metadata
        │
        ▼
Enriched response returned to MCP client
```

Each step is a no-op if the corresponding component isn't configured. A domain project that only uses formatters gets formatting + governance. One that uses nothing gets governance only (current behavior).

### Domain Project Wiring

```python
class MyDomainServer(BaseMCPServer):
    def __init__(self, warehouse_path, catalog_path, **kwargs):
        formatter = MyDomainFormatter()
        anomaly_checker = MyDomainAnomalyChecker()

        super().__init__(
            warehouse_path=warehouse_path,
            catalog_path=catalog_path,
            formatter=formatter,
            anomaly_checker=anomaly_checker,
            **kwargs,
        )

        self.system_prompt = MyDomainSystemPrompt(
            server=self,
            anomaly_checker=anomaly_checker,
        )

    def get_tools(self) -> list[ToolDef]:
        # Domain-specific tools
        ...

    def get_resources(self) -> list[ResourceDef]:
        # System prompt exposed as a resource
        return [
            ResourceDef(
                uri="brightsmith://system-prompt",
                name="Recommended System Prompt",
                description="Data-aware system prompt for this dataset",
                mime_type="text/markdown",
                handler=self.system_prompt.build,
            ),
        ]
```

## What Stays The Same

- `BaseMCPServer.get_tools()` and `get_resources()` — unchanged API
- `BaseMCPServer.query_iceberg()` and `query_iceberg_simple()` — unchanged
- Framework-provided tools (`query_table`, `list_tables`, `get_data_quality`, etc.) — unchanged
- Framework-provided resources (domain context, glossary, grounding docs) — unchanged
- MCP protocol, stdio transport, `python -m brightsmith.serve` — unchanged
- Eval set format and verification framework — unchanged
- Pipeline steps (`MCP_ZONE_STEPS`) — unchanged
- @mcp-engineer agent role — extended to cover formatters, anomaly rules, system prompt sections

## Success Criteria

- [ ] `BaseFormatter` with `FormatRule`, `format_row()`, `format_rows()`, `format_value()`
- [ ] `format_utils.py` with common format functions (large numbers, percentages, decimals, multipliers, date ranges, YoY changes)
- [ ] `BaseAnomalyChecker` with `AnomalyRule`, `AnomalyFlag`, `check_row()`, `check_rows()`
- [ ] `BaseSystemPrompt` with `PromptSection`, `get_framework_sections()`, `get_domain_sections()`, `build()`
- [ ] `BaseMCPServer.enrich_response()` chains formatter → anomaly checker → governance metadata
- [ ] `BaseMCPServer` constructor accepts optional `formatter`, `anomaly_checker`, `system_prompt`
- [ ] `attach_governance()` still works standalone (backward compatible)
- [ ] System prompt auto-exposed as MCP resource when configured
- [ ] Framework sections built from governance artifacts (contracts, DQ scorecards, glossary)
- [ ] All components are optional — MCP server works without any of them (current behavior preserved)
- [ ] Zero LLM SDK imports
- [ ] Tests for each component (formatter, anomaly checker, system prompt, enrichment pipeline)
- [ ] Existing `test_base_mcp_server.py` tests still pass
- [ ] @mcp-engineer agent definition updated to reference intelligence layer components

## Files to Create

| File | Purpose |
|------|---------|
| `src/brightsmith/mcp/base_formatter.py` | `BaseFormatter`, `FormatRule` |
| `src/brightsmith/mcp/format_utils.py` | Common format functions |
| `src/brightsmith/mcp/base_anomaly_checker.py` | `BaseAnomalyChecker`, `AnomalyRule`, `AnomalyFlag` |
| `src/brightsmith/mcp/base_system_prompt.py` | `BaseSystemPrompt`, `PromptSection` |
| `tests/mcp/test_base_formatter.py` | Formatter tests |
| `tests/mcp/test_format_utils.py` | Format utility tests |
| `tests/mcp/test_base_anomaly_checker.py` | Anomaly checker tests |
| `tests/mcp/test_base_system_prompt.py` | System prompt tests |
| `tests/mcp/test_enrich_response.py` | Enrichment pipeline integration tests |

## Files to Modify

| File | What Changes |
|------|-------------|
| `src/brightsmith/mcp/base_mcp_server.py` | Add `formatter`, `anomaly_checker`, `system_prompt` constructor params; add `enrich_response()` method; system prompt auto-resource |
| `src/brightsmith/mcp/__init__.py` | Export new classes |
| `.claude/agents/mcp-engineer.md` | Reference intelligence layer base classes and enrichment pipeline |
| `CLAUDE.md` | Update AI-Ready zone description to include intelligence layer components |
| `README.md` | Update architecture diagram and MCP zone description |

## Implementation Order

1. `BaseFormatter` + `format_utils.py` + tests (standalone, no dependencies)
2. `BaseAnomalyChecker` + tests (standalone, no dependencies)
3. `BaseSystemPrompt` + tests (depends on `BaseMCPServer` for Iceberg queries, `BaseAnomalyChecker` for anomaly summaries)
4. `BaseMCPServer` integration — `enrich_response()`, constructor params, system prompt resource
5. Integration tests for the full enrichment pipeline
6. Update `__init__.py` exports
7. Update @mcp-engineer agent definition
8. Update CLAUDE.md and README.md

## Tests

### BaseFormatter Tests
- `test_no_rules_passthrough` — empty formatter returns rows unchanged
- `test_single_rule_match` — matching rule formats value, adds `_formatted` key
- `test_first_match_wins` — multiple matching rules, first one applied
- `test_no_match_passthrough` — non-matching values unchanged, no `_formatted` key
- `test_none_values_skipped` — None values not formatted
- `test_format_rows_batch` — batch formatting works identically to individual
- `test_original_value_preserved` — raw value always kept alongside formatted string

### format_utils Tests
- `test_format_large_number_billions` — 1234567890 → "1.2B"
- `test_format_large_number_millions` — 1234567 → "1.2M"
- `test_format_large_number_thousands` — 1234 → "1,234"
- `test_format_large_number_negative` — -1234567890 → "-1.2B"
- `test_format_percentage` — 0.253 → "25.3%"
- `test_format_percentage_negative` — -0.124 → "-12.4%"
- `test_format_multiplier` — 2.345 → "2.3x"
- `test_format_yoy_change_positive` — 0.078 → "+7.8%"
- `test_format_yoy_change_negative` — -0.123 → "-12.3%"
- `test_format_date_range` — date pair → human-readable range
- `test_zero_and_edge_values` — 0, very small, very large values

### BaseAnomalyChecker Tests
- `test_no_rules_no_flags` — empty checker returns empty flags
- `test_matching_rule_creates_flag` — anomalous row gets flag attached
- `test_non_matching_no_flags` — clean row gets empty flags list
- `test_multiple_flags_per_row` — row can trigger multiple rules
- `test_check_rows_batch` — batch checking attaches `_anomaly_flags` to each row
- `test_severity_levels` — info, warning, caveat, error all work
- `test_rule_id_in_flag` — flag includes rule_id for traceability

### BaseSystemPrompt Tests
- `test_empty_prompt_has_framework_sections` — framework sections always present
- `test_domain_sections_ordered_by_priority` — lower priority number = earlier
- `test_build_returns_markdown` — output is valid markdown string
- `test_token_budget_truncates` — over-budget sections omitted
- `test_framework_sections_from_governance` — reads real governance files if present
- `test_missing_governance_graceful` — missing files don't break prompt assembly

### Enrichment Pipeline Tests
- `test_enrich_no_components` — governance only (backward compatible)
- `test_enrich_formatter_only` — format + governance
- `test_enrich_anomaly_only` — flag + governance
- `test_enrich_full_pipeline` — format + flag + governance
- `test_enrich_preserves_raw_data` — original values never lost

## Relationship to Other Specs

- **ai-ready-mcp-server.md**: This spec extends the MCP server design from that spec. That spec established `BaseMCPServer`; this one adds the intelligence layer on top.
- **data-contracts.md**: Governance metadata in `enrich_response()` reads contract info. System prompt includes DQ summary from scorecards.
- **adversarial-dq-hardening.md**: DQ rules (pipeline-time) and anomaly rules (query-time) are complementary. Anomaly checker doesn't replace DQ.
- **headless-pipeline-runner.md**: Intelligence layer has zero LLM imports — headless readiness preserved.

## Future Considerations

- **Caching** — `PromptSection.cache_ttl_seconds` is designed but not implemented in v1. System prompt sections that query Iceberg can be expensive; caching would help for high-traffic MCP servers.
- **Dynamic anomaly rule loading** — Currently anomaly rules are defined in code. Future: load from `governance/anomaly-rules/` YAML files, similar to DQ rules. This would let @dq-rule-writer or @mcp-engineer define anomaly rules as governance artifacts.
- **Token counting** — `max_tokens` on `PromptSection` and `build()` requires a token counter. v1 can use character-based approximation (4 chars ≈ 1 token); future versions can use a proper tokenizer.
- **Streaming responses** — `enrich_response()` currently operates on complete results. For large result sets, a streaming variant that formats/flags row-by-row would reduce memory pressure.
