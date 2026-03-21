# Spec: infra-framework-hardening

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-17

## Problem Statement

The Grist framework was field-tested by building `sec_edgar_grist` — a full SEC EDGAR XBRL pipeline for 20 public companies. A critical period disambiguation bug corrupted 17% of consumable-zone revenue data: 47% of Apple's historical revenue was wrong, 48% of derived Q4 values were impossible negatives. All 24 agents — including @staff-engineer's final review — approved the code that produced this corruption.

Four systemic framework gaps enabled this:
1. **No temporal period classification utility** — domain builders had to invent ad-hoc period logic that failed
2. **No DQ rule patterns for consumable grain uniqueness** — 192 rules existed but none caught "multiple values per entity-metric-period"
3. **No data correctness verification** — reviews checked code quality and artifact completeness, never actual output values
4. **No user interview during domain discovery** — @domain-context synthesized domain knowledge purely from automated EDA, never asking the user. Anyone who's worked with SEC EDGAR XBRL data would have immediately said "use date-span filtering for period disambiguation." The user chose this data source — they likely know something about it.

Additionally, the chaos monkey (adversarial DQ testing) is raw-zone-only, not in the standard pipeline, and lacks After-Action Reports. Insight report recommendations were never verified as implemented. The first domain project was built in the framework repo instead of a separate project.

**Source reports:**
- `~/code/sec_edgar_grist/docs/grist-framework-improvement-report.md`
- `~/code/sec_edgar_grist/docs/comparison-grist-vs-sec-edgair.md`
- `~/code/sec_edgar_grist/docs/data-product-quality-audit.md`

## Success Criteria

- [ ] PeriodDisambiguator utility exists and classifies date spans as annual/quarterly/monthly/point-in-time
- [ ] PeriodDisambiguator correctly selects the annual value from the Apple FY2010 scenario (11 rows, $9.1B-$65.2B)
- [ ] DQ rule templates exist for consumable zone with 4 mandatory patterns
- [ ] @dq-rule-writer agent references consumable templates before writing consumable rules
- [ ] Chaos monkey is schema-agnostic (introspects any Iceberg table schema)
- [ ] Chaos monkey hardening loop appears in all three CLAUDE.md pipeline variants
- [ ] Chaos monkey produces After-Action Reports
- [ ] DQ runner supports `--shadow` flag for shadow table execution
- [ ] @governance-reviewer verifies insight report recommendations have validating DQ rules
- [ ] @insight-manager output includes verification criteria per recommendation
- [ ] @staff-engineer has mandatory data correctness spot-check in review protocol
- [ ] Golden dataset template and directory convention exist
- [ ] Integration test harness validates pipeline output against golden datasets
- [ ] @domain-context agent conducts EDA-informed user interview before synthesizing domain knowledge
- [ ] Unanswered interview questions are flagged as risks in domain-context.md with corresponding DQ rule requirements
- [ ] @insight-manager no longer runs at raw-to-base transition; only runs at base-to-consumable and consumable-to-ai-ready
- [ ] BaseChatAgent exists with Anthropic SDK setup, tool registration, conversation loop, and Iceberg query helper
- [ ] AI-Ready zone always produces: tool-use chat agent + grounding docs (for system prompt) + eval set (for validation)
- [ ] @setup scaffolds AI-Ready zone with chat agent skeleton extending BaseChatAgent
- [ ] @setup agent refuses to scaffold a domain project inside the framework repo
- [ ] CLAUDE.md rules enforce framework/domain separation
- [ ] All new code has tests; `python -m pytest tests/` passes

## Technical Design

### 1. Period Disambiguator (`src/grist/infra/period_disambiguator.py`)

A domain-agnostic utility for classifying temporal periods by date-span length. Any dataset with start/end dates needs this — financial filings, healthcare encounters, IoT sensor windows, subscription billing periods.

**Classes:**

```python
@dataclass
class PeriodThresholds:
    """Configurable thresholds for period classification.
    Domain projects override these for their specific temporal patterns."""
    annual_min_days: int = 300      # >300 days = annual
    quarterly_min_days: int = 60    # 60-120 days = quarterly
    quarterly_max_days: int = 120
    monthly_min_days: int = 25      # 25-35 days = monthly
    monthly_max_days: int = 35

@dataclass
class PeriodClassification:
    period_type: str        # "annual", "quarterly", "monthly", "point_in_time", "other"
    duration_days: int
    confidence: float       # 1.0 for clear matches, lower for boundary cases

class PeriodDisambiguator:
    def __init__(self, thresholds: PeriodThresholds | None = None): ...
    def classify(self, start_date: date, end_date: date) -> PeriodClassification: ...
    def classify_batch(self, records: list[dict], start_col: str, end_col: str) -> list[PeriodClassification]: ...
    def select_primary(
        self,
        facts: list[dict],
        entity_col: str,
        metric_col: str,
        period_col: str,
        start_col: str,
        end_col: str,
        target_type: str = "annual"
    ) -> list[dict]: ...
    def as_duckdb_case(self, start_col: str, end_col: str) -> str: ...
```

**`classify(start_date, end_date)`** — Returns `PeriodClassification` based on span length. Point-in-time when start == end or end is None.

**`select_primary(facts, ..., target_type="annual")`** — Given multiple facts for the same (entity, metric, fiscal_year) grain, filters to only those whose date span matches `target_type`. This is the core function that fixes the period disambiguation bug.

**`as_duckdb_case(start_col, end_col)`** — Generates a SQL CASE expression for use in DuckDB queries:
```sql
CASE
    WHEN end_col - start_col > 300 THEN 'annual'
    WHEN end_col - start_col BETWEEN 60 AND 120 THEN 'quarterly'
    WHEN end_col - start_col BETWEEN 25 AND 35 THEN 'monthly'
    WHEN end_col = start_col OR start_col IS NULL THEN 'point_in_time'
    ELSE 'other'
END
```

### 2. DQ Rule Templates (`governance/dq-rule-templates/consumable-patterns.json`)

JSON template catalog — not executable code, but a checklist that @dq-rule-writer must reference for every consumable spec.

**4 mandatory patterns:**

| Pattern ID | Dimension | Priority | Description |
|-----------|-----------|----------|-------------|
| CONS-GRAIN-UNIQUE | Uniqueness | P0 | One value per entity-metric-period at declared grain |
| CONS-IMPOSSIBLE-VALUE | Validity | P0 | Values that violate domain constraints (negative revenue, >100% margin) |
| CONS-CROSS-TABLE | Consistency | P1 | Related tables agree on shared dimensions |
| CONS-GOLDEN-DATASET | Accuracy | P0 | Known-correct values match pipeline output |

Each pattern includes: `sql_template` (with `{table}`, `{grain_columns}` placeholders), `threshold`, `when` (applicability criteria).

CONS-GRAIN-UNIQUE and CONS-GOLDEN-DATASET are mandatory for every consumable spec. Skipping requires human override documented in audit trail.

### 3. Chaos Monkey Schema-Agnostic Redesign (`src/grist/infra/chaos_monkey/`)

Complete rewrite of the chaos monkey as a framework utility that works with any Iceberg table schema.

**Package structure:**
```
src/grist/infra/chaos_monkey/
    __init__.py
    injector.py       # SchemaIntrospector + ChaosInjector
    safety.py         # Three-layer kill switch
    manifest.py       # JSON manifest writer
    reconciler.py     # After-Action Report generator
    __main__.py       # CLI entry point
```

**`SchemaIntrospector`** — Reads PyIceberg `table.schema()`, maps each field to corruption strategies:

| Iceberg Type | Corruption Strategies |
|-------------|----------------------|
| StringType | null, empty string, unicode garbage, truncation |
| DoubleType/FloatType | null, negative, NaN, extreme value, zero |
| IntegerType/LongType | null, negative, overflow, zero |
| DateType/TimestampType | null, future date, epoch, far past |
| BooleanType | null |

**`ChaosInjector.inject(table_ref, shadow_namespace, rate, seed)`**:
1. Copies table data to shadow namespace via Iceberg
2. Selects `rate%` of rows randomly (seeded for reproducibility)
3. For each selected row, picks 1-3 columns and applies type-appropriate corruption
4. Records every corruption in `ChaosManifest`
5. Safety: validates `CHAOS_MONKEY_ENABLED=True`, `GRIST_ENV=dev`, shadow namespace prefix

**`ChaosReconciler.reconcile(manifest, dq_results)`**:
1. Reads the chaos manifest (what was injected)
2. Reads DQ results from shadow execution (what was caught)
3. Cross-references: for each injection, was there a DQ rule that flagged it?
4. Produces After-Action Report:
   - Caught corruptions (rule ID, dimension, injection details)
   - Missed corruptions (gap analysis — what DQ dimension is under-covered?)
   - DQ coverage score: caught / total injections
5. Output: `governance/chaos-manifests/{spec}-after-action-{timestamp}.md`

**DQ Runner `--shadow` flag** — Small addition to `src/grist/infra/dq_runner.py`:
- `_register_iceberg_views()` gains a `shadow_namespace` parameter
- When set, table references `namespace.table` resolve to `shadow_namespace.table` instead
- CLI: `python -m grist.infra.dq_runner run --spec {spec} --shadow`

### 4. Insight Report Verification (Agent Definition Changes)

**`.claude/agents/governance-reviewer.md`** — Add to Post-Implementation Checklist:
```
- [ ] Insight Traceability: If an Insight Report exists for this zone transition,
      verify each recommendation relevant to this spec has (a) a corresponding
      implementation and (b) a DQ rule validating it. Missing validation = CHANGES REQUESTED.
```

**`.claude/agents/insight-manager.md`** — Add to each recommendation in output format:
```
Verification Criteria: [What DQ rule confirms this was implemented?
What would failure look like in the data?]
```

### 5. Staff Engineer Data Correctness (`Agent Definition Change`)

**`.claude/agents/staff-engineer.md`** — Add after "What You Check":
```
### Data Correctness Spot-Check (MANDATORY — Base and Consumable zones)

1. Identify 3-5 output values independently verifiable from public/authoritative sources
2. Query actual Iceberg tables and compare to reference values
3. Document results:
   | Entity | Metric | Period | Pipeline Value | Reference Value | Source | Match? |
4. ANY wrong value beyond tolerance (<1% for financials, exact for counts): REJECT
5. If no reference data exists, require @data-analyst to produce a golden dataset first
```

### 6. Golden Dataset Convention

**Template:** `governance/dq-rule-templates/golden-dataset-template.json`
```json
{
  "spec": "spec-name",
  "records": [
    {
      "entity": "Apple Inc.",
      "metric": "Revenue",
      "period": "FY2024",
      "expected_value": 391035000000,
      "source": "Apple 10-K FY2024",
      "tolerance_pct": 0.01
    }
  ]
}
```

**Path convention:** `governance/golden-datasets/{spec}-golden.json`

**Config addition:** `GOLDEN_DATASETS_DIR` and `DQ_TEMPLATES_DIR` in `src/grist/config.py`

### 7. Integration Test Harness (`src/grist/infra/integration_test_harness.py`)

```python
class PipelineTestHarness:
    def load_golden_dataset(self, path: Path) -> list[GoldenRecord]: ...
    def validate(self, golden_records: list[GoldenRecord]) -> ValidationResult: ...

@dataclass
class GoldenRecord:
    entity: str
    metric: str
    period: str
    expected_value: float
    source: str
    tolerance_pct: float

@dataclass
class ValidationResult:
    matches: list[GoldenRecord]
    mismatches: list[tuple[GoldenRecord, float]]  # (record, actual_value)
    missing: list[GoldenRecord]                    # not found in table
```

Queries Iceberg tables via the existing `iceberg_setup.read_with_duckdb()` utility. Integrates with pytest as a fixture factory.

### 8. EDA-Informed User Interview (`@domain-context` Agent Enhancement)

Currently @domain-context runs fully autonomously — it reads the EDA report, synthesizes domain knowledge, and produces `governance/domain-context.md` with zero user input. This throws away the most valuable signal available: the person who chose this data source probably knows something about it.

**Enhancement to `.claude/agents/domain-context.md`:**

Add a new step between reading EDA and synthesizing: **interview the user** with targeted questions derived from what the EDA actually found.

**How it works:**

1. @domain-context reads the EDA report (existing step)
2. **NEW: Generate 5-10 targeted questions** based on EDA findings. These are not generic — they're specific to patterns found in the data. Examples:
   - "EDA found 412 rows per filing with overlapping date ranges (start_date/end_date). How should we distinguish the primary period from comparatives?"
   - "We see 3,289 distinct concepts but most are sparse. Which metrics matter most for your use case?"
   - "Filings contain both 10-K (annual) and 10-Q (quarterly) forms. Should consumable tables normalize to annual periods, quarterly, or both?"
   - "Some entities have different fiscal year-ends (September, June, January, December). Should we normalize to calendar year or preserve fiscal years?"
3. **NEW: Present questions to the user.** User answers what they know, skips what they don't.
4. **NEW: For unanswered questions, flag as risks** in `domain-context.md`:

```markdown
## Unresolved Domain Questions (Flagged as Risks)

| # | Question | EDA Evidence | Risk | DQ Rule Required |
|---|----------|-------------|------|-----------------|
| 1 | How to disambiguate overlapping periods? | 412 rows/filing with date spans 0d-365d | Period selection may pick wrong value | CONS-GRAIN-UNIQUE on (entity, metric, period) |
| 2 | Which metrics matter most? | 3,289 concepts, 82% unmapped | Pipeline may over/under-index on wrong metrics | Golden dataset validation against known values |
```

Each unresolved question generates a **mandatory DQ rule requirement** — @dq-rule-writer must write a rule that would catch the failure mode if the assumption turns out wrong. This connects directly to the DQ rule templates in Section 2.

5. Synthesize domain-context.md (existing step), now enriched with user answers and explicit risk flags

**Question generation strategy:**

@domain-context generates questions in these categories:
- **Temporal patterns** — period disambiguation, fiscal calendars, amendment handling
- **Grain/uniqueness** — what constitutes one record, how to dedup
- **Domain semantics** — what fields mean, which values matter
- **Known edge cases** — things the user has encountered before
- **External context** — regulations, standards, data quirks the user knows about

Questions are phrased to be answerable by someone with moderate domain familiarity. Not "explain XBRL" but "we found overlapping date ranges — how should we handle them?"

### 9. Drop Raw-to-Base Insight Report + Chat-Backward Design (Pipeline Timing Fix)

The raw-to-base transition is mechanical — normalize flat data into dimensional tables. There's not enough signal in raw data for meaningful data product recommendations. In sec_edgair (the predecessor project), the first insight report was base-to-consumable, not raw-to-base.

Additionally, @insight-manager must know that the pipeline always produces a **tool-use chat agent** as the AI-Ready deliverable. This adds a second lens to its recommendations — alongside "what data products are valuable?", it also asks "what does the chat agent need?"

These overlap but aren't identical. A sector benchmarks table might be valuable for analysts even if the chat agent doesn't use it. And the chat agent might need simple lookups that don't warrant dedicated consumable tables.

**What changes:**

@insight-manager runs only at:
- **Base-to-Consumable** — Recommend valuable data products AND identify what the chat agent needs (questions users will ask, tools to answer them, consumable tables those tools query)
- **Consumable-to-AI-Ready** — Recommend AI-ready artifacts AND design the chat agent (tools to register, grounding context, eval questions)

@insight-manager does NOT run at Raw-to-Base. Domain discovery at that stage is handled by @data-analyst EDA and @domain-context user interview.

**Modified: `CLAUDE.md`**

Update the "Zone Transition: @insight-manager" section:
```
@insight-manager runs at these transitions:
- After Base Zone complete -> Inform Consumable Zone specs
- After Consumable Zone complete -> Inform AI-Ready Zone specs

@insight-manager does NOT run after Raw Zone. Raw-to-Base is mechanical.

The end goal is always a tool-use chat agent (BaseChatAgent). Insight reports
design BACKWARD from the chat experience:
- Base-to-Consumable: What questions will users ask? -> What tools answer them?
  -> What consumable tables do those tools need?
- Consumable-to-AI-Ready: What tools to register? What grounding context?
  What eval questions validate correctness?
```

**Modified: `.claude/agents/insight-manager.md`**

Update "Your Role in the Pipeline" to remove raw-to-base and add chat agent awareness:
```
You run at zone transitions:
1. After Base Zone complete -> Inform Consumable Zone specs
2. After Consumable Zone complete -> Inform AI-Ready Zone specs

The pipeline always produces a tool-use chat agent (BaseChatAgent) as the
AI-Ready deliverable. Your insight reports serve two purposes:

1. Recommend valuable DATA PRODUCTS — tables, views, and aggregations worth
   building on their own merits (for analysts, dashboards, exports, direct SQL)
2. Recommend what the CHAT AGENT needs — questions users will ask, tools to
   answer them, consumable tables those tools query, grounding context

These overlap but aren't identical. Some data products are valuable even if
the chat agent doesn't use them. Some chat tools need simple lookups that
don't warrant their own consumable table.
```

Update the output format to ADD a chat agent section alongside the existing "Data Products — Ranked":

```markdown
## Data Products — Ranked
[existing Tier 1/2/3 format stays]

## Chat Agent Design
### Top Questions Users Will Ask
| # | Question Pattern | Example | Tool Needed | Consumable Table |
|---|-----------------|---------|-------------|-----------------|

### Tools Required
| Tool Name | Description | Input | SQL Pattern | Tables Queried |
|-----------|-------------|-------|-------------|---------------|

### Grounding Context for System Prompt
| Topic | What the LLM Needs to Know | Source |
|-------|---------------------------|--------|
```

### 10. AI-Ready Zone: Tool-Use Chat Agent as Standard (`src/grist/ai_ready/base_chat_agent.py`)

The AI-Ready zone always produces a **tool-use chat agent** that queries live Iceberg data — not static documents or embeddings. This mirrors how `BaseIngestor` standardizes the Raw zone: the framework provides the chassis, domain projects provide the tools.

**Three AI-Ready artifacts, always:**
1. **Tool-use chat agent** — the primary deliverable. Queries live Iceberg tables via DuckDB.
2. **Grounding docs** — domain context that feeds into the chat agent's system prompt. Produced by @content-strategist from domain-context.md + consumable data.
3. **Eval set** — question-answer pairs that validate the chat agent gives correct answers. Produced by @data-analyst from golden datasets + consumable tables.

**New file: `src/grist/ai_ready/base_chat_agent.py`**

```python
class BaseChatAgent:
    """Framework base class for domain-specific tool-use chat agents.

    Domain projects extend this with domain-specific tools and system prompt.
    The framework handles: Anthropic SDK setup, tool registration, conversation
    loop, Iceberg query execution, response formatting.
    """

    def __init__(self, project_name: str, model: str = "claude-sonnet-4-5-20250514"):
        self.client = anthropic.Anthropic()
        self.model = model
        self.project_name = project_name
        self.tools: list[dict] = []
        self._system_prompt: str = ""

    # --- Framework provides ---

    def query_iceberg(self, sql: str) -> list[dict]:
        """Execute SQL against Iceberg tables via DuckDB. Returns rows as dicts."""
        ...

    def register_tool(self, name: str, description: str, input_schema: dict,
                      handler: Callable) -> None:
        """Register a tool the chat agent can call."""
        ...

    def load_grounding_docs(self, path: Path) -> str:
        """Load grounding docs directory into system prompt context."""
        ...

    def chat(self, user_message: str) -> str:
        """Single-turn: send message, handle tool calls, return final response."""
        ...

    def conversation_loop(self) -> None:
        """Interactive REPL: read user input, chat, print response, repeat."""
        ...

    # --- Domain projects override ---

    def get_system_prompt(self) -> str:
        """Override to provide domain-specific system prompt.
        Should incorporate grounding docs for domain context."""
        raise NotImplementedError

    def register_tools(self) -> None:
        """Override to register domain-specific tools via self.register_tool()."""
        raise NotImplementedError
```

**Key design decisions:**

- `query_iceberg()` reuses `iceberg_setup.read_with_duckdb()` — same Arrow bridge pattern as the rest of the framework
- Tool handlers receive parsed arguments and return results. The framework handles the Anthropic tool_use/tool_result message flow.
- `load_grounding_docs()` reads the markdown files from `data/ai_ready/grounding/` and concatenates them into system prompt context. This is how domain-discovered knowledge (from @domain-context -> @content-strategist -> grounding docs) feeds into the chat agent without hardcoding.
- `anthropic` becomes a framework dependency in `pyproject.toml`

**New file: `tests/ai_ready/test_base_chat_agent.py`**

Tests:
- Tool registration and dispatch
- `query_iceberg()` returns correct results for known SQL
- `chat()` handles tool_use responses correctly (mock Anthropic client)
- `load_grounding_docs()` concatenates markdown files
- Domain subclass can override `get_system_prompt()` and `register_tools()`

**Modified: `.claude/agents/setup.md`**

Scaffold AI-Ready zone in domain projects:
```
src/ai_ready/
    chat_agent.py          # Extends BaseChatAgent with domain tools
    __main__.py            # CLI entry point for chat
```

**Modified: `CLAUDE.md`**

Update AI-Ready zone description:
```
The AI-Ready zone always produces three artifacts:
1. Tool-use chat agent (extends BaseChatAgent) — primary deliverable
2. Grounding docs — domain context for system prompt
3. Eval set — validates chat agent correctness

The chat agent queries live Iceberg tables via DuckDB. Domain projects
implement tools and system prompt; the framework handles SDK setup,
tool dispatch, and conversation management.
```

### 11. Restructure Grist as a Claude Code Plugin

The framework repo becomes a Claude Code plugin — the idiomatic distribution mechanism. Users install via `/plugin install grist` and never check out the framework repo. Domain projects are always separate directories.

**Repo restructure:**

```
grist/                                    # Same repo, new structure
├── .claude-plugin/
│   └── plugin.json                      # Plugin manifest
├── skills/
│   └── grist/
│       └── SKILL.md                     # /grist slash command (init, run, status)
├── agents/                              # Moved from .claude/agents/ (plugin convention)
│   ├── setup.md
│   ├── staff-engineer.md
│   ├── chaos-monkey.md
│   ├── domain-context.md
│   ├── governance-reviewer.md
│   ├── insight-manager.md
│   └── ... (18 more agents)
├── hooks/
│   └── hooks.json                       # SessionStart: pip install grist if missing
├── src/grist/                           # Python package (unchanged)
│   ├── infra/
│   ├── raw/
│   ├── ai_ready/
│   ├── base/
│   ├── _templates/                      # CLAUDE.md template, governance scaffolding
│   │   ├── CLAUDE.md.template
│   │   └── project-structure.yaml       # What dirs/files to scaffold
│   └── config.py
├── pyproject.toml                       # Python package metadata
├── marketplace.json                     # Plugin marketplace entry
└── README.md
```

**Key structural changes:**
- `.claude/agents/*.md` -> `agents/*.md` (top-level, plugin convention)
- New `.claude-plugin/plugin.json` — plugin manifest with name, version, description
- New `skills/grist/SKILL.md` — the `/grist` slash command
- New `hooks/hooks.json` — SessionStart hook that runs `pip install grist` if not already installed
- New `src/grist/_templates/` — CLAUDE.md template and scaffolding definitions
- New `marketplace.json` — for plugin discovery and installation

**`.claude-plugin/plugin.json`:**
```json
{
  "name": "grist",
  "description": "Domain-agnostic AI agent data pipeline framework",
  "version": "0.2.0"
}
```

**`skills/grist/SKILL.md`** — The main entry point:
```markdown
---
name: grist
description: Grist data pipeline framework — scaffold projects, run pipelines, check status
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# /grist — Grist Pipeline Framework

## Commands
- `/grist init` — Scaffold a new domain project in the current directory
- `/grist run <spec>` — Run the pipeline for a spec
- `/grist status` — Show pipeline status for the current project

## Init
When the user runs `/grist init`:
1. Verify current directory is NOT the grist framework repo
2. Run `python -m grist.setup init` to scaffold the project structure
3. This creates: CLAUDE.md, pyproject.toml, governance/, domain/, docs/specs/, src/, tests/
4. Launch @setup agent to interview the user about their data source
```

**`hooks/hooks.json`** — Auto-install pip package:
```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "pip install grist 2>/dev/null || pip install -e ${CLAUDE_PLUGIN_DIR} 2>/dev/null",
        "timeout": 60
      }]
    }]
  }
}
```

**User workflow:**
```bash
# One-time: install the plugin
claude /plugin install grist

# Per project: scaffold and build
mkdir my-data-project && cd my-data-project
claude /grist init        # scaffolds project, interviews user about data
# ... agents run the pipeline ...
```

**What `/grist init` scaffolds in the domain project:**
```
my-data-project/
├── .claude/
│   └── settings.json                # Project-level Claude settings
├── src/
│   └── raw/                         # Domain ingestor goes here
├── domain/
│   ├── manifest.yaml                # Data source config
│   └── sources/                     # Source definitions
├── governance/
│   ├── dq-rules/
│   ├── dq-results/
│   ├── dq-scorecards/
│   ├── dq-rule-templates/           # Copied from framework
│   ├── golden-datasets/
│   ├── models/
│   ├── reviews/
│   ├── audit-trail/
│   ├── lineage/
│   ├── eda/
│   └── insights/
├── docs/
│   ├── specs/
│   └── sessions/
├── tests/
├── CLAUDE.md                        # Generated from template
├── pyproject.toml                   # With grist as dependency
└── README.md
```

**Framework/domain separation is now automatic** — the framework is a plugin, domain projects are separate directories. There's no way to accidentally build a domain project inside the framework repo because users never check it out.

## Files Created

| File | Purpose |
|------|---------|
| `src/grist/infra/period_disambiguator.py` | Temporal period classification utility |
| `tests/infra/test_period_disambiguator.py` | Period disambiguator tests |
| `governance/dq-rule-templates/consumable-patterns.json` | Consumable DQ rule template catalog |
| `governance/dq-rule-templates/golden-dataset-template.json` | Golden dataset format template |
| `src/grist/infra/chaos_monkey/__init__.py` | Package init |
| `src/grist/infra/chaos_monkey/injector.py` | Schema-agnostic corruption injector |
| `src/grist/infra/chaos_monkey/safety.py` | Three-layer kill switch |
| `src/grist/infra/chaos_monkey/manifest.py` | Injection manifest writer |
| `src/grist/infra/chaos_monkey/reconciler.py` | After-Action Report generator |
| `src/grist/infra/chaos_monkey/__main__.py` | CLI entry point |
| `tests/infra/test_chaos_monkey.py` | Chaos monkey tests |
| `src/grist/infra/integration_test_harness.py` | Golden dataset validation harness |
| `tests/integration/test_harness.py` | Integration test harness tests |
| `src/grist/ai_ready/base_chat_agent.py` | Framework base class for tool-use chat agents |
| `tests/ai_ready/test_base_chat_agent.py` | Chat agent base class tests |
| `.claude-plugin/plugin.json` | Plugin manifest (name, version, description) |
| `skills/grist/SKILL.md` | /grist slash command (init, run, status) |
| `hooks/hooks.json` | SessionStart hook for auto pip-install |
| `marketplace.json` | Plugin marketplace entry for distribution |
| `src/grist/_templates/CLAUDE.md.template` | CLAUDE.md template for domain projects |
| `src/grist/_templates/project-structure.yaml` | Scaffolding definition for domain projects |

## Files Modified

| File | What Changes |
|------|-------------|
| `src/grist/config.py` | Add `DQ_TEMPLATES_DIR`, `GOLDEN_DATASETS_DIR` paths + rebuild in `configure()` |
| `src/grist/infra/dq_runner.py` | Add `--shadow` flag to `run_rules()` and `_register_iceberg_views()` |
| `pyproject.toml` | Add `anthropic` as framework dependency, include `_templates/` as package data |
| **Agent definitions (moved from `.claude/agents/` to `agents/`):** | |
| `agents/dq-rule-writer.md` | Add consumable template checklist section |
| `agents/chaos-monkey.md` | Schema-agnostic redesign, add After-Action Reports, remove raw-zone-only limitation |
| `agents/governance-reviewer.md` | Add insight traceability to post-implementation checklist |
| `agents/insight-manager.md` | Add verification criteria, remove raw-to-base, add chat agent awareness |
| `agents/staff-engineer.md` | Add mandatory data correctness spot-check section |
| `agents/domain-context.md` | Add EDA-informed user interview step, risk flagging for unanswered questions |
| `agents/setup.md` | Rewrite for plugin context — scaffolds via `python -m grist.setup init` |

## Files Moved

| From | To | Why |
|------|----|-----|
| `.claude/agents/*.md` (all 24) | `agents/*.md` | Plugin convention: agents at top-level, not `.claude/agents/` |
| `CLAUDE.md` | `src/grist/_templates/CLAUDE.md.template` | Becomes a template generated per domain project, not a static file in the framework |

## Files Removed

| File | Why |
|------|-----|
| `.claude/agents/` (directory) | Replaced by top-level `agents/` (plugin convention) |
| `governance/` (empty scaffold dirs) | Only exist in domain projects, not the framework plugin |
| `domain/` (example files) | Only exist in domain projects, scaffolded by `/grist init` |

## Agent Workflow

This is an infrastructure spec — no zone-specific pipeline applies. Implementation order:

1. **Period Disambiguator** — Root cause fix (code + tests)
2. **DQ Rule Templates** — JSON templates (moved to `src/grist/_templates/` as package data)
3. **Config additions** — New paths in `config.py`
4. **Chaos Monkey redesign** — Schema-agnostic rewrite (code + tests)
5. **DQ Runner shadow flag** — Enable chaos monkey DQ execution
6. **BaseChatAgent** — AI-Ready tool-use chat base class (code + tests)
7. **Agent definition updates** — All 24 agents updated and moved to `agents/`
8. **Integration test harness** — Golden dataset validation (code + tests)
9. **Plugin structure** — `.claude-plugin/`, `skills/`, `hooks/`, `marketplace.json`
10. **Templates** — CLAUDE.md template, project scaffolding definition
11. **Setup module** — `python -m grist.setup init` scaffolding logic

Steps 4-5 are coupled. Step 6 is independent. Steps 7-10 can run in parallel once earlier steps establish what agent changes are needed.

## DQ Rules

Not applicable (infrastructure spec). The DQ rule templates produced by this spec will be consumed by future domain specs.

## Governance Artifacts

- [ ] Spec review: `governance/reviews/infra-framework-hardening-pre-review.md`
- [ ] Post-implementation review: `governance/reviews/infra-framework-hardening-post-review.md`
- [ ] Staff engineer review: `governance/reviews/infra-framework-hardening-staff-review.md`
- [ ] Audit trail: `governance/audit-trail/infra-framework-hardening.json`
- [ ] Session log: `docs/sessions/YYYY-MM-DD-HH-MM-session.md`

## Verification

1. **Unit tests pass:** `python -m pytest tests/` — all new and existing tests green
2. **Period disambiguator regression test:** Apple FY2010 scenario — 11 rows with spans from 91d to 365d — `select_primary()` returns only the 365d row with value $65.2B
3. **Chaos monkey introspection:** Point `SchemaIntrospector` at any Iceberg table, verify it reads all columns and types correctly
4. **Chaos monkey safety:** Verify injection is blocked when `GRIST_ENV != dev` or `CHAOS_MONKEY_ENABLED != True`
5. **DQ runner shadow mode:** `python -m grist.infra.dq_runner run --spec test --shadow` resolves tables from shadow namespace
6. **DQ templates loadable:** Verify JSON parses and all 4 patterns have required fields
7. **Golden dataset validation:** Integration test harness correctly identifies matches and mismatches against known Iceberg data
8. **Agent definitions coherent:** Read each modified agent definition end-to-end, verify no contradictions
9. **CLAUDE.md pipeline complete:** Chaos monkey step appears in all three pipeline variants (Raw, Base Greenfield, Base Backfill)
10. **Plugin structure valid:** `.claude-plugin/plugin.json` parses correctly, `skills/grist/SKILL.md` has valid frontmatter, `hooks/hooks.json` is valid JSON
11. **`/grist init` works:** Creates a complete domain project in an empty directory with CLAUDE.md, governance dirs, pyproject.toml
12. **`/grist init` refuses in framework dir:** Running from the grist plugin repo itself produces a refusal
11. **Domain context interview:** @domain-context agent definition includes interview step with question generation from EDA findings
12. **Risk flagging:** Unanswered interview questions appear as risks in domain-context.md template with mandatory DQ rule requirements
13. **BaseChatAgent:** Tool registration, query_iceberg(), chat() with mocked Anthropic client, grounding doc loading all work correctly
14. **Domain subclass:** A test subclass extending BaseChatAgent can override get_system_prompt() and register_tools() and handle a conversation turn
