# DeepAgents Integration Guide

How to adapt the Grist framework to work with a LangChain DeepAgents-style AI agent system, wrapping each pipeline stage as an agent tool so the agent orchestrates the Raw -> Base -> Consumable -> AI-Ready flow.

---

## 1. Dependencies

Add to `pyproject.toml`:

```toml
dependencies = [
    # ... existing deps ...
    "langchain-core>=0.3",
    "langchain>=0.3",
    "langgraph>=0.2",           # if your fork uses LangGraph
    "langchain-anthropic>=0.3", # or langchain-openai, whichever LLM you use
]
```

---

## 2. Agent Tools — Wrapping Pipeline Stages

Create `src/agent/tools.py`. Each tool is a thin wrapper around an existing Grist capability:

```python
from langchain_core.tools import tool
from grist.domain_loader import load_manifest
from grist.infra.iceberg_setup import create_table, append_to_table, read_table
from grist.infra.dq_runner import DQRunner
from grist.infra.glossary_loader import load_glossary
from grist.base.concept_normalization.normalize import ConceptNormalizer
from grist.infra.lineage import emit_lineage_event


@tool
def ingest_source(source_name: str) -> str:
    """Fetch and ingest a data source into the raw zone.
    Loads manifest, finds the named source, runs fetch -> flatten -> dedup -> Iceberg append."""
    manifest = load_manifest()
    source = next(s for s in manifest.sources if s.name == source_name)
    # Instantiate your domain-specific ingestor here
    # ingestor = MyIngestor(source)
    # ingestor.run()
    return f"Ingested {source_name} into raw zone"


@tool
def run_dq_check(table_name: str, namespace: str = "raw") -> str:
    """Run data quality checks on a table. Returns pass/fail summary."""
    runner = DQRunner()
    results = runner.run(namespace=namespace, table_name=table_name)
    failures = [r for r in results if not r["passed"]]
    if failures:
        return f"DQ FAILED: {len(failures)} rules failed: {failures}"
    return f"DQ PASSED: all {len(results)} rules passed"


@tool
def normalize_concepts(source_name: str) -> str:
    """Run concept normalization on a source. Returns unmapped concepts if any."""
    normalizer = ConceptNormalizer(source_name)
    unmapped = normalizer.get_unmapped_concepts()
    if unmapped:
        return f"Found {len(unmapped)} unmapped concepts: {unmapped}"
    return "All concepts mapped"


@tool
def check_glossary(term: str) -> str:
    """Search the composed glossary for a business term."""
    glossary = load_glossary()
    match = glossary.find_matching_term(term)
    if match:
        return f"Found: {match}"
    return f"No glossary match for '{term}'"


@tool
def read_table_sample(namespace: str, table_name: str, limit: int = 10) -> str:
    """Read a sample from an Iceberg table for inspection."""
    df = read_table(namespace, table_name)
    return df.head(limit).to_string()
```

---

## 3. Pipeline Agent

Create `src/agent/pipeline_agent.py`:

```python
from langchain_anthropic import ChatAnthropic  # or your LLM
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent

from grist.agent.tools import (
    ingest_source,
    run_dq_check,
    normalize_concepts,
    check_glossary,
    read_table_sample,
)

SYSTEM_PROMPT = """You are a data pipeline agent operating the Grist framework.
Your job is to orchestrate data through the pipeline: Raw -> Base -> Consumable -> AI-Ready.

For each source:
1. Ingest into raw zone
2. Run DQ checks — if P0 fails, stop and report
3. Normalize concepts — if unmapped concepts exist, flag for review
4. Verify glossary alignment
5. Report status

Always run DQ checks before promoting data to the next zone.
If REQUIRE_HUMAN_APPROVAL is enabled, flag items that need review rather than auto-promoting.
"""


def create_pipeline_agent():
    llm = ChatAnthropic(model="claude-sonnet-4-6-20250514")
    tools = [
        ingest_source,
        run_dq_check,
        normalize_concepts,
        check_glossary,
        read_table_sample,
    ]
    return create_react_agent(llm, tools, prompt=SystemMessage(content=SYSTEM_PROMPT))


def run_agent(user_input: str):
    agent = create_pipeline_agent()
    result = agent.invoke({"messages": [("user", user_input)]})
    return result["messages"][-1].content


def main():
    import sys
    query = " ".join(sys.argv[1:]) or "Ingest all sources from the manifest and run DQ checks"
    print(run_agent(query))
```

---

## 4. Human-in-the-Loop (Approval Gates)

Grist already has `src/infra/staging.py` for approval gates. In a DeepAgents-style system, you add an **interrupt node** in LangGraph that mirrors `REQUIRE_HUMAN_APPROVAL`:

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent


def create_pipeline_agent_with_approval():
    llm = ChatAnthropic(model="claude-sonnet-4-6-20250514")
    tools = [
        ingest_source,
        run_dq_check,
        normalize_concepts,
        check_glossary,
        read_table_sample,
    ]

    # interrupt_before stops execution before these tools run, requiring human approval
    return create_react_agent(
        llm,
        tools,
        interrupt_before=["ingest_source", "normalize_concepts"],
        checkpointer=MemorySaver(),
    )
```

This maps the `REQUIRE_HUMAN_APPROVAL` flag to the agent orchestration level. The underlying `staging.py` logic remains unchanged.

---

## 5. Entry Point

Add a CLI entry in `pyproject.toml`:

```toml
[project.scripts]
grist-agent = "src.agent.pipeline_agent:main"
```

Then run:

```bash
uv run grist-agent "Ingest sec_filings and run DQ checks"
```

---

## 6. Concept Mapping — Grist to Agent Patterns

| Grist Concept | Agent Pattern |
|---|---|
| `BaseIngestor.run()` | `@tool ingest_source` |
| `DQRunner.run()` | `@tool run_dq_check` — agent stops on P0 failure |
| `ConceptNormalizer` | `@tool normalize_concepts` — agent flags unmapped |
| `staging.py` approval gates | LangGraph `interrupt_before` |
| `lineage.py` events | Emit inside each tool wrapper for audit trail |
| `domain/manifest.yaml` | Agent reads this to discover available sources |
| `glossary_loader` | `@tool check_glossary` — agent can look up terms |

---

## 7. Adapting for Non-Standard Forks

If your DeepAgents fork diverges from upstream LangChain, the pattern stays the same:

- **Tools** = thin wrappers around existing Grist functions using `@tool`
- **Agent prompt** = describes the pipeline stages and rules (DQ gating, approval flow)
- **Human-in-the-loop** = map to your fork's interrupt/approval mechanism

The main thing to adapt is the agent constructor syntax (`create_react_agent` vs whatever your fork provides). The `@tool` definitions are standard LangChain and should work in any fork.

---

## 8. Lineage Integration

Wrap each tool with lineage emission so agent actions are auditable:

```python
from grist.infra.lineage import emit_lineage_event

@tool
def ingest_source(source_name: str) -> str:
    """Fetch and ingest a data source into the raw zone."""
    emit_lineage_event("START", job_name=f"ingest_{source_name}")
    try:
        manifest = load_manifest()
        source = next(s for s in manifest.sources if s.name == source_name)
        # ... ingestor logic ...
        emit_lineage_event("COMPLETE", job_name=f"ingest_{source_name}", row_count=row_count)
        return f"Ingested {source_name} into raw zone"
    except Exception as e:
        emit_lineage_event("FAIL", job_name=f"ingest_{source_name}", error=str(e))
        raise
```

This ensures every agent action produces an OpenLineage-compatible audit trail in the `governance.lineage_events` Iceberg table.
