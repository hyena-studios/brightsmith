# Framework Spec: AI-Ready Zone → MCP Server

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting, AI-Ready zone redesign)
**Primary Agent:** @primary-agent
**Created:** 2026-03-19

## Problem Statement

The AI-Ready zone currently produces a **tool-use chat agent** (`BaseChatAgent`) that bundles:
- Anthropic SDK dependency (conversation loop, tool routing)
- Tool definitions (query functions)
- System prompt assembly (grounding docs)
- Iceberg query execution (DuckDB)

This is the wrong abstraction. A chat agent is a **complete application** — it owns the conversation, the LLM calls, and the data access. If you want to use the data from Claude Desktop, you need to build a different thing. If you want to use it from Claude Code, another thing. If you want to use it from a custom app, yet another.

An MCP server is a **data service**. It exposes tools and resources over a standard protocol. Any MCP client — Claude Desktop, Claude Code, Cursor, custom apps — can connect and use the data without the framework needing to know or care about the client.

**Key insight:** The entire Grist pipeline (raw → base → consumable) is pure Python with zero LLM dependencies. The AI-Ready zone should be too. The MCP server serves governed data — the LLM lives on the client side.

## What Changes

| Aspect | Before (Chat Agent) | After (MCP Server) |
|--------|---------------------|-------------------|
| Deliverable | `BaseChatAgent` subclass | MCP server with tools + resources |
| LLM dependency | `anthropic` SDK required | None — pure Python |
| Client coupling | Tied to Anthropic API | Any MCP client (Claude Desktop, Code, etc.) |
| Protocol | Proprietary (Anthropic tool-use) | Standard (MCP over stdio/SSE) |
| Grounding docs | Concatenated into system prompt | Exposed as MCP resources |
| Eval set | Tests chat responses | Tests tool responses (same format, simpler) |
| Runtime | Needs API key + conversation loop | `python -m grist.serve` or MCP config |

## What Stays The Same

- The 4-zone architecture (Raw → Base → Consumable → AI-Ready)
- Tool functions (query_financials, compare_entities, etc.) — same logic, different wrapper
- Grounding documents (domain context for system prompts)
- Eval sets (50+ Q&A cases verifying tool correctness)
- DQ rules, contracts, golden datasets for consumable tables
- @insight-manager designs the tool surface at consumable→AI-ready transition
- @mcp-engineer implements the MCP server (role unchanged, elevated to primary AI-Ready agent)

## Success Criteria

- [ ] `BaseMCPServer` replaces `BaseChatAgent` as the AI-Ready zone framework base class
- [ ] MCP server exposes consumable tables as tools (query, compare, rank, trend)
- [ ] MCP server exposes grounding docs as resources
- [ ] MCP server exposes data quality metadata as resources
- [ ] `python -m grist.serve` starts the MCP server (stdio mode for Claude Desktop/Code)
- [ ] Zero `anthropic` imports in the entire framework (not just non-AI-Ready zones)
- [ ] Eval set format unchanged — tool input/output testing still works
- [ ] Domain projects extend `BaseMCPServer` the same way they extended `BaseChatAgent`
- [ ] Existing tests updated, no test count regression
- [ ] Claude Desktop and Claude Code can connect to a Grist MCP server

## Technical Design

### 1. `BaseMCPServer` — Framework Base Class

**File:** `src/grist/ai_ready/base_mcp_server.py` (replaces `base_chat_agent.py`)

```python
"""Base MCP server for AI-Ready zone — the standard deliverable.

Every Grist pipeline produces an MCP server as its AI-Ready zone output.
Domain projects extend BaseMCPServer with domain-specific tools and
resources. The framework handles MCP protocol, tool registration,
Iceberg query execution, and governance metadata attachment.

Usage:
    class MyDomainServer(BaseMCPServer):
        def register_tools(self) -> list[Tool]:
            return [
                Tool(
                    name="query_financials",
                    description="Query financial data by company and period",
                    input_schema={...},
                    handler=self._query_financials,
                ),
            ]

        def register_resources(self) -> list[Resource]:
            return [
                Resource(
                    uri="grist://domain-context",
                    name="Domain Context",
                    description="Domain knowledge for interpreting financial data",
                    handler=self._get_domain_context,
                ),
            ]
"""
```

The class provides:
- `register_tools()` — abstract, domain projects define query tools
- `register_resources()` — abstract, domain projects define context resources
- `query_iceberg(sql)` — inherited utility for querying consumable tables
- `load_grounding_docs()` — inherited utility for loading grounding documents
- `attach_governance(result)` — automatically attaches lineage, DQ score, provenance to tool responses
- `serve()` — starts the MCP server (stdio mode by default)

### 2. Tool Definition Pattern

Tools follow the same pattern as `BaseChatAgent` but with MCP schema:

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class Tool:
    """An MCP tool definition."""
    name: str
    description: str
    input_schema: dict          # JSON Schema for input parameters
    handler: callable           # (dict) -> dict — takes input, returns result

@dataclass
class Resource:
    """An MCP resource definition."""
    uri: str                    # e.g., "grist://domain-context"
    name: str
    description: str
    mime_type: str = "text/plain"
    handler: callable = None    # () -> str — returns resource content
```

### 3. Standard Tools (Framework-Provided)

Every MCP server gets these tools for free (domain projects add domain-specific ones):

| Tool | Description | Input |
|------|-------------|-------|
| `query_table` | Query any consumable table with filters | `{table, filters, columns, limit}` |
| `list_tables` | List available consumable tables with descriptions | `{}` |
| `get_data_quality` | Get DQ scorecard for a table | `{table}` |
| `get_lineage` | Get lineage for a table | `{table}` |
| `get_contract` | Get data contract for a table | `{table}` |

### 4. Standard Resources (Framework-Provided)

| Resource | URI | Content |
|----------|-----|---------|
| Domain Context | `grist://domain-context` | `governance/domain-context.md` |
| Business Glossary | `grist://business-glossary` | `governance/business-glossary.json` (as markdown) |
| Data Dictionary | `grist://data-dictionary` | `governance/data-dictionary.json` (as markdown) |
| Grounding Docs | `grist://grounding/{name}` | `data/ai_ready/grounding/*.md` |

Resources give the LLM client the context it needs to interpret tool results. The client decides how to use them (system prompt, RAG, reference) — the server just serves them.

### 5. MCP Server Entry Point

**File:** `src/grist/serve.py`

```bash
# Start MCP server (stdio mode — for Claude Desktop / Claude Code)
python -m grist.serve
```

The server uses stdio transport only (launched as a subprocess by MCP clients). It reads `domain/manifest.yaml` to discover which domain MCP server class to instantiate, or falls back to the base server with framework-provided tools only.

### 6. Claude Desktop / Claude Code Integration

Once the MCP server is built, users add it to their MCP client config:

**Claude Desktop (`claude_desktop_config.json`):**
```json
{
  "mcpServers": {
    "my-data-project": {
      "command": "uv",
      "args": ["--directory", "/path/to/project", "run", "python", "-m", "grist.serve"],
      "env": {
        "GRIST_PROJECT_ROOT": "/path/to/project"
      }
    }
  }
}
```

**Claude Code (`.claude/settings.json` or project-level):**
```json
{
  "mcpServers": {
    "my-data-project": {
      "command": "uv",
      "args": ["--directory", "/path/to/project", "run", "python", "-m", "grist.serve"]
    }
  }
}
```

### 7. Governance Metadata in Tool Responses

Every tool response automatically includes governance context:

```json
{
  "data": [...],
  "governance": {
    "table": "consumable.company_financials",
    "contract_version": "1.2.0",
    "contract_status": "active",
    "dq_score": "12/12 rules passed",
    "last_updated": "2026-03-19T06:00:00Z",
    "lineage": "raw.company_facts → base.financial_facts → consumable.company_financials",
    "golden_dataset_pass_rate": "100%"
  }
}
```

The LLM client can use this to calibrate confidence. If `dq_score` shows failures, the client knows to caveat its answer.

### 8. Eval Set — Same Format, Simpler Testing

Eval sets still use the same format (question, expected_answer, source_table, filters, column). But testing is simpler:

**Before (chat agent):** Send question to LLM → parse response → compare to expected
**After (MCP server):** Call tool directly with input → compare output to expected

No LLM in the eval loop. Tool testing is deterministic.

### 9. Files to Change

#### Replace
| Old File | New File | What Changes |
|----------|----------|-------------|
| `src/grist/ai_ready/base_chat_agent.py` | `src/grist/ai_ready/base_mcp_server.py` | Chat agent → MCP server base class |
| `tests/ai_ready/test_base_chat_agent.py` | `tests/ai_ready/test_base_mcp_server.py` | Tests for MCP server base class |

#### New
| File | Purpose |
|------|---------|
| `src/grist/serve.py` | MCP server entry point (`python -m grist.serve`) |

#### Update (references only)
| File | What Changes |
|------|-------------|
| `src/grist/ai_ready/__init__.py` | Export `BaseMCPServer` instead of `BaseChatAgent` |
| `CLAUDE.md` | Replace "tool-use chat agent" with "MCP server" throughout |
| `.claude/agents/insight-manager.md` | "chat agent design" → "MCP server design" |
| `.claude/agents/mcp-engineer.md` | Elevate to primary AI-Ready agent, reference `BaseMCPServer` |
| `.claude/agents/doc-generator.md` | Grounding docs serve as MCP resources, not system prompt chunks |
| `.claude/agents/principal-data-architect.md` | "AI-Ready serving pattern" references MCP |
| `.claude/agents/staff-engineer.md` | AI-Ready test minimums reference MCP tools |
| `README.md` | Update AI-Ready zone description |
| `docs/specs/infra-framework-hardening.md` | Historical reference (no functional change) |
| `src/grist/run.py` | Headless readiness: no `anthropic` imports anywhere (not just non-AI-Ready) |

### 10. What Happens to `BaseChatAgent`?

Deleted. If a domain project wants a chat agent, they can build one that connects to their MCP server as a client — but that's their concern, not the framework's. The framework provides the data service; the application layer is up to the consumer.

## MCP SDK Dependency

Add `mcp` to `pyproject.toml` dependencies:

```toml
"mcp[cli]>=1.0",
```

The `mcp` package provides the server framework (tool/resource registration, stdio/SSE transport). It's a lightweight dependency with no LLM coupling.

## Tests

- `tests/ai_ready/test_base_mcp_server.py`:
  - `test_tool_registration` — tools registered with correct schema
  - `test_resource_registration` — resources registered with correct URIs
  - `test_query_iceberg_returns_results` — DuckDB query works
  - `test_governance_metadata_attached` — responses include DQ/lineage/contract
  - `test_abstract_methods_enforced` — must implement register_tools/resources
  - `test_load_grounding_docs` — loads .md files from grounding directory
  - `test_list_tables_tool` — framework-provided tool works
  - `test_get_data_quality_tool` — framework-provided tool works

## Implementation Order

1. Create `base_mcp_server.py` with `BaseMCPServer`, `Tool`, `Resource` dataclasses
2. Implement framework-provided tools (query_table, list_tables, get_data_quality, etc.)
3. Implement framework-provided resources (domain context, glossary, dictionary, grounding docs)
4. Create `serve.py` entry point
5. Write tests
6. Delete `base_chat_agent.py` and update `__init__.py`
7. Update all agent definitions and CLAUDE.md references
8. Update README.md
9. Remove `anthropic` from framework dependencies (domain projects can still add it)

### 11. @principal-data-architect Interactive Proposals

Update the architect agent to present **evidence-backed architectural proposals** via `AskUserQuestion` at each zone transition. The architect has the EDA report, domain context, insight report, DQ scorecards, glossary, contracts, and all code — so proposals are informed, not blank-slate questions.

#### Protocol

At each zone transition, after reviewing all artifacts, the architect presents 2-4 key architectural decisions via `AskUserQuestion`. Each question includes:
- **The evidence** (what EDA/insight/DQ data informed the recommendation)
- **The architect's recommendation** (highlighted as default)
- **Alternative options** with trade-offs explained
- **"Do what you think is best"** — architect proceeds with expert judgment

#### Raw → Base Transition Questions

1. **Dimensional model design:**
   > "Based on the EDA ({N} entities, {M} metrics, {P} time periods), I recommend a {star schema / flat table / hybrid}. Here's why: {evidence}."
   > Options: "Star schema (fact + dimensions)" / "Flat denormalized" / "Do what you think is best"

2. **Normalization aggressiveness:**
   > "The concept map has {N} canonical concepts from {M} source codes. I recommend {aggressive/moderate/minimal} normalization because {evidence}."
   > Options: "Aggressive (15-25 core concepts)" / "Moderate (25-50)" / "Preserve all source codes" / "Do what you think is best"

3. **Entity resolution strategy:**
   > "Entity IDs are {stable/unstable}. I recommend {skipping resolution / building master entity table} because {evidence}."
   > Options: presented based on findings

#### Base → Consumable Transition Questions

1. **Data product serving pattern:**
   > "The insight report recommends {N} Tier 1 products. I recommend {wide pivoted / tall time series / both} because {evidence}."

2. **Derived metric strategy:**
   > "Ratios and computed metrics: precompute into tables (faster queries, stale if inputs change) or compute at query time in MCP tools (always fresh, slower)?"

#### Consumable → AI-Ready Transition Questions

1. **MCP tool design:**
   > "I recommend {domain-specific tools / generic query tools / both} because {evidence from insight report}."

2. **Resource strategy:**
   > "Grounding context: full domain context as one resource, or curated per-tool summaries?"

#### Handling "Do What You Think Is Best"

When the user defers:
1. Architect proceeds with the recommended option
2. Documents the decision in the architecture review under "## User-Deferred Decisions"
3. Flags confidence level (HIGH if evidence is strong, MEDIUM if trade-offs are close)
4. @staff-engineer reviews all user-deferred decisions at final gate

All responses logged in the session's Human Input Log per Change 8 of framework-gap-closure.

## Future: Authentication, Entitlements, and Row-Level Security

This spec ships stdio-only (local subprocess, OS-level access control). The following are **known future requirements** that will be addressed in a separate spec when there's a real multi-user or remote consumer:

- **Authentication** — required when the MCP server serves remote clients over SSE/HTTP. OAuth2 or API key auth at the transport layer.
- **Entitlements** — which users/roles can access which tools and tables. A policy layer between tool invocation and Iceberg query execution. Likely maps to data contract `consumers` section.
- **Row-Level Security (RLS)** — which rows a user can see within a table. Filter predicates injected based on the authenticated user's entitlements (e.g., "user X can only see entities in their portfolio"). Implemented as automatic WHERE clause injection in `query_iceberg()`.
- **Audit logging** — who queried what, when, with what entitlements. Extends the existing governance audit trail.

The architecture supports this: `query_iceberg()` is the single choke point for all data access. RLS filters, entitlement checks, and audit logging can all be injected there without changing tool definitions or domain code.

## Relationship to Other Specs

- **data-contracts.md**: MCP `get_contract` tool serves contract YAML to clients
- **framework-quality-parity.md (Change 7)**: Verification framework still validates tool outputs
- **headless-pipeline-runner.md**: Headless runner becomes fully LLM-free (no AI-Ready exception)
- **adversarial-dq-hardening.md**: DQ rules still protect consumable tables that MCP serves
