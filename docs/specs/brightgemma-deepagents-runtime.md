# Framework Spec: Brightgemma Deep Agents Runtime

**Status:** DRAFT  
**Zone:** Infrastructure (cross-cutting agent workflow)  
**Primary Agent:** @staff-engineer  
**Created:** 2026-04-28  
**Target Workspace:** `~/code/bright/brightgemma`

## Problem Statement

Brightsmith's real workflow is larger than a data pipeline. It is an agentic development process with named specialist roles, zone-specific sequencing, governance artifacts, data quality gates, human approvals, session logs, and final review.

Today that workflow is still operationally tied to Claude Code:

- `CLAUDE.md` acts as the master runtime contract
- `skills/*.md` use Claude-specific dispatch language such as `Agent(...)`, `subagent_type`, and `bs:<agent>`
- human approvals and interviews assume `AskUserQuestion`
- `.claude/agents/` mirrors `agents/`
- Claude hooks enforce agent dispatch shape

The model can now be supplied independently through Gemma running locally via Ollama or remotely via OpenRouter. The missing piece is the tooling harness that replaces Claude Code while preserving the full Brightsmith workflow.

Brightgemma will be that harness: a Deep Agents SDK / LangGraph based runtime that executes Brightsmith's existing agent workflow with Gemma-compatible model backends and a thin custom chat UI. A later phase will replace the thin UI with a full workflow control plane.

## Goals

- Create a new sibling project at `~/code/bright/brightgemma`
- Replace Claude Code as the Brightsmith agent workflow harness
- Use Deep Agents SDK on LangGraph for planning, subagents, tool calls, persistence, streaming, and human-in-the-loop interrupts
- Support Gemma model execution through:
  - Ollama for local/private/dev runs
  - OpenRouter for hosted/full-workflow runs
- Treat Brightsmith workflow semantics as canonical:
  - agent roles
  - pipeline gate state
  - human approval gates
  - session logging
  - governance artifacts
  - zone sequencing
  - DQ, lineage, contract, golden dataset, and verification checks
- Build a thin custom web chat client for Phase 1
- Reuse design elements from Brightforge/Brightgem only:
  - Warmcraft tokens
  - app shell layout
  - right-side chat panel
  - agent timeline cards
  - approval card patterns
- Keep future full UI work separate from the runtime migration

## Non-Goals

- Do not rewrite Brightsmith's data infrastructure
- Do not redesign the Bronze/Silver/Gold/MCP workflow
- Do not copy Brightforge or Brightgem runtime architecture
- Do not build the final polished workflow UI in Phase 1
- Do not require Claude Code, OpenCode, OpenClaw, or a Claude plugin
- Do not make Gemma-specific assumptions inside Brightsmith's canonical workflow semantics

## Chosen Harness

Use **Deep Agents SDK on LangGraph** as the replacement tooling harness.

Rationale:

- Deep Agents provides a programmable agent harness rather than only an interactive coding assistant
- subagents map directly to Brightsmith roles in `agents/*.md`
- LangGraph provides durable execution, streaming, checkpointing, and pause/resume
- human-in-the-loop interrupts map cleanly to approval gates and domain interviews
- model access is provider-agnostic and can route to Ollama or OpenRouter
- the same runtime can power CLI, thin chat UI, and future full web UI

## System Architecture

```text
~/code/bright/brightgemma
  frontend thin chat client
        │
        ▼
  FastAPI app / SSE event stream
        │
        ▼
  Brightgemma agent runtime
        │
        ├── Deep Agents supervisor
        ├── LangGraph durable execution
        ├── Brightsmith role subagents from agents/*.md
        ├── Brightsmith tools and gate wrappers
        ├── approval/interview interrupts
        └── model backends
              ├── Ollama
              └── OpenRouter
```

Brightsmith remains the source of truth for pipeline infrastructure and governance logic. Brightgemma is the agent workflow runtime and UI shell.

## Repository Layout

Create a new sibling repository:

```text
~/code/bright/brightgemma/
  pyproject.toml
  README.md
  backend/
    app/
      main.py
      routes/
        chat.py
        runs.py
        approvals.py
        artifacts.py
        health.py
      runtime/
        orchestrator.py
        graph.py
        roles.py
        tools.py
        approvals.py
        backends.py
        events.py
        session_log.py
        config.py
      storage/
        models.py
        repository.py
  frontend/
    package.json
    index.html
    src/
      App.tsx
      main.tsx
      styles/
        warmcraft-tokens.css
        globals.css
      components/
        shell/
        chat/
        runs/
        approvals/
      hooks/
      lib/
      types/
  tests/
    backend/
    frontend/
```

## Dependency Direction

Brightgemma may depend on Brightsmith as a local package during development:

```toml
dependencies = [
  "brightsmith @ file:///Users/jcernauske/code/bright/brightsmith",
  "deepagents",
  "langgraph",
  "langchain",
  "fastapi",
  "uvicorn",
  "pydantic",
  "httpx",
]
```

If Deep Agents requires provider-specific packages for Ollama or OpenRouter, add them in Brightgemma only. Do not add LLM runtime dependencies to Brightsmith's core package unless Brightsmith itself grows a provider-neutral agent runtime later.

## Model Backends

Brightgemma must expose one runtime configuration surface:

```env
BRIGHTGEMMA_BACKEND=ollama
BRIGHTGEMMA_MODEL=gemma4:31b
BRIGHTGEMMA_OLLAMA_BASE_URL=http://127.0.0.1:11434

# or

BRIGHTGEMMA_BACKEND=openrouter
BRIGHTGEMMA_MODEL=google/gemma-4-31b
OPENROUTER_API_KEY=...
```

The workflow must not hardcode model IDs. Model names are configuration.

### Ollama

Use for local, private, and development runs.

Requirements:

- configurable Ollama base URL
- configurable model name
- low default parallelism
- explicit timeout handling
- structured-output retry policy
- clear error when Ollama is unavailable or the model is missing

### OpenRouter

Use for hosted and full-workflow runs.

Requirements:

- API key from `OPENROUTER_API_KEY`
- configurable model name
- rate limit and retry handling
- timeout handling
- structured-output retry policy
- request metadata identifying Brightgemma where supported

## Runtime Design

### 1. Supervisor

The supervisor is the main Deep Agent. It coordinates workflow execution but does not own Brightsmith semantics directly. Its job is to:

1. inspect the requested spec and zone
2. check pipeline gate state
3. dispatch the next required role
4. stream run events
5. pause for human input when needed
6. validate expected artifacts
7. complete, skip, or block steps through `pipeline_gate`

### 2. Role Registry

Brightgemma loads role definitions from Brightsmith's canonical `agents/` directory:

```text
/Users/jcernauske/code/bright/brightsmith/agents/<role>.md
```

Each role becomes a Deep Agents subagent spec with:

- stable role name
- role markdown as system prompt
- allowed tools
- optional response schema
- optional per-role model override
- optional per-role filesystem permissions

`.claude/agents/` must not be used as a source of truth.

### 3. Tool Registry

Brightgemma wraps existing Brightsmith behavior as tools:

| Tool | Purpose |
|---|---|
| `pipeline_gate_check` | Run `python -m brightsmith.infra.pipeline_gate check` |
| `pipeline_gate_complete` | Register step completion |
| `pipeline_gate_skip` | Register skip with reason/evidence |
| `pipeline_gate_approve` | Register approval decision |
| `read_artifact` | Read approved artifact paths |
| `write_artifact` | Write governance artifacts under allowed directories |
| `run_dq` | Execute DQ checks |
| `run_contract_verify` | Verify contracts |
| `run_lineage_verify` | Verify lineage |
| `run_golden_verify` | Verify golden datasets |
| `run_tests` | Run focused test commands |
| `shell` | Restricted shell execution |

The model proposes tool calls. The runtime executes them. Gate completion only counts when the tool succeeds and the expected artifact exists.

### 4. Human Input

Map Brightsmith approvals and interviews to LangGraph interrupts.

Approval interrupt payload:

```json
{
  "type": "approval",
  "spec": "spec-name",
  "artifact": "business-terms",
  "artifact_path": "governance/approvals/spec-business-terms-approval.md",
  "allowed_decisions": [
    "approved",
    "approved_with_notes",
    "changes_requested",
    "need_more_info"
  ]
}
```

On resume, Brightgemma must:

1. write the human response to the run event log
2. write the human response to the session log
3. update Brightsmith audit trail artifacts
4. call `pipeline_gate approve` when applicable
5. resume the graph from the checkpoint

### 5. Session Logging

Brightgemma must preserve Brightsmith's session logging guarantees:

- exact initial user prompt
- all human input verbatim
- approvals and decisions
- specs referenced
- files changed
- model/backend used
- run events
- failures and retries

Phase 1 may store runtime metadata in SQLite, but Brightsmith-compatible session logs must still be written under `docs/sessions/` or an equivalent configured project path.

## Phase 1: Runtime Plus Thin Chat

### Scope

Build the minimum viable Brightgemma runtime and web client.

Backend:

- FastAPI app
- Deep Agents supervisor
- role registry loading `agents/*.md`
- Ollama backend
- OpenRouter backend
- basic Brightsmith tool registry
- LangGraph checkpointing
- SSE event streaming
- approval interrupt plumbing
- run/session storage

Frontend:

- Warmcraft token layer copied from Brightforge
- compact app shell
- thin chat surface
- run status panel
- active step/agent timeline
- approval prompt cards
- backend/model selectors

CLI:

```bash
brightgemma run <spec> --zone bronze --backend ollama --model gemma4:31b
brightgemma chat --backend openrouter --model google/gemma-4-31b
brightgemma status <run-id>
```

### Thin UI Shape

```text
Top bar:
  Brightgemma | project | backend/model | health

Left nav:
  Chat
  Runs
  Specs
  Artifacts
  Settings

Main:
  chat stream OR run timeline

Right rail:
  active spec
  active zone
  current agent
  pending approval
  artifact links
```

Chat is an operational control surface, not the final product UX.

### Design References

Use only design elements from the old repos:

- Warmcraft tokens:
  - `brightforge/frontend/src/styles/warmcraft-tokens.css`
- app shell pattern:
  - `brightforge/frontend/src/components/shell/AppShell.tsx`
- chat panel pattern:
  - `brightforge/frontend/src/components/chat/ChatPanel.tsx`
- pipeline runner simplicity:
  - `brightgem/frontend/src/components/pipeline/PipelineRunner.tsx`
- agent timeline cards:
  - `brightgem/frontend/src/components/pipeline/AgentTimeline.tsx`
  - `brightgem/frontend/src/components/pipeline/AgentStepCard.tsx`
- approval card pattern:
  - `brightforge/frontend/src/components/pipeline/ApprovalGateCard.tsx`

Do not copy Brightforge/Brightgem backend architecture or runtime assumptions.

## Phase 2: Workflow Parity And Hardening

Goal: make Brightgemma trustworthy enough to replace Claude Code for real Brightsmith specs.

Add:

- deterministic graph definitions for Bronze, Silver, Gold, and MCP workflows
- branch logic for greenfield/backfill flows
- explicit role-to-tool permission maps
- structured response schemas for each role
- artifact existence checks before gate completion
- retry policy for malformed tool calls and invalid structured outputs
- checkpoint resume after crash or approval wait
- run cancellation
- run replay
- full event log
- regression tests for gate behavior
- drift check proving `agents/` is canonical
- docs converting Claude-specific workflow instructions into runtime-neutral runbooks

Phase 2 must prove at least:

- one Bronze spec can run end to end
- one Silver/Gold flow can reach approval gates
- human approvals pause and resume correctly
- `pipeline_gate` remains the authoritative state machine
- no role can silently mark itself complete without expected artifacts

## Phase 3: Full Workflow Control Plane

Goal: replace the thin chat client with a real Brightsmith operational UI.

Views:

- spec dashboard
- zone board
- run timeline
- agent activity stream
- pipeline gate board
- approval inbox
- artifact browser
- artifact diff/review
- governance/audit trail viewer
- DQ scorecard viewer
- lineage viewer
- data contract viewer
- model/backend settings
- chat side panel for steering

At this stage, chat becomes a side panel. The primary UX becomes:

```text
Spec -> Zone -> Step -> Agent -> Artifact -> Approval -> Gate
```

## Phase 4: Deployment And Multi-User

Add only after the runtime is stable:

- auth
- workspace/project separation
- secrets management
- run queue
- remote workers
- OpenRouter/Ollama backend pools
- audit policies
- hosted deployment option
- role-based permissions

## Acceptance Criteria

### Phase 1

- [ ] `~/code/bright/brightgemma` exists as a standalone project
- [ ] backend starts with FastAPI
- [ ] frontend starts with Vite/React
- [ ] Warmcraft tokens are applied
- [ ] user can select Ollama or OpenRouter backend
- [ ] user can select model by config, not hardcoded list only
- [ ] Deep Agents supervisor can load Brightsmith role definitions from `agents/`
- [ ] at least two role subagents can be dispatched
- [ ] `pipeline_gate check` can be called through a tool
- [ ] `pipeline_gate complete` can be called through a tool
- [ ] thin chat can stream events from a run
- [ ] an approval interrupt appears in the UI and can be resumed
- [ ] session log records model/backend, user prompt, and human decisions

### Phase 2

- [ ] Bronze graph is implemented
- [ ] Silver/Gold graph is implemented
- [ ] MCP graph is implemented
- [ ] role-specific tool permissions are enforced
- [ ] artifact checks block invalid completion
- [ ] malformed structured outputs trigger retry or failure
- [ ] interrupted runs resume from checkpoint
- [ ] gate state remains authoritative in Brightsmith
- [ ] tests cover successful run, blocked gate, approval pause/resume, failed tool call, and cancellation

### Phase 3

- [ ] full run dashboard exists
- [ ] approval inbox exists
- [ ] artifact browser exists
- [ ] pipeline gate board exists
- [ ] run event log is inspectable
- [ ] chat is integrated as a side panel, not the whole app

## Risks

### Risk 1: Treating The Model As The Harness

Gemma is the reasoning engine, not the workflow engine. Brightgemma must enforce permissions, gates, and artifact checks outside the model.

Mitigation:

- keep LangGraph and Brightsmith tools authoritative
- never trust a model assertion that a step is complete
- validate every completion through `pipeline_gate` and file checks

### Risk 2: Claude Workflow Assumptions Leak In

Current docs still contain Claude-specific instructions.

Mitigation:

- load role semantics from `agents/`
- treat `CLAUDE.md`, `.claude/`, and `skills/*.md` as migration inputs, not canonical runtime APIs
- continue the provider-agnostic workflow migration

### Risk 3: Ollama Tool Calling Is Less Reliable

Local models may produce inconsistent tool calls or structured output.

Mitigation:

- use strict schemas
- implement retries
- keep OpenRouter as the recommended backend for full workflow parity testing
- start with low parallelism for Ollama

### Risk 4: UI Work Distracts From Runtime Correctness

The final UI can consume a lot of effort before the harness is proven.

Mitigation:

- Phase 1 UI is deliberately thin
- no full workflow console until Bronze/Silver/Gold/MCP runtime parity is demonstrable

## Implementation Order

1. Scaffold `~/code/bright/brightgemma`
2. Add backend package and model backend config
3. Add Deep Agents supervisor with one simple tool
4. Load Brightsmith `agents/*.md` as role specs
5. Wrap `pipeline_gate check` and `pipeline_gate complete`
6. Run one narrow two-step workflow against a test spec
7. Add FastAPI streaming endpoint
8. Add thin React chat/run UI using Warmcraft tokens
9. Add approval interrupt and resume path
10. Expand to Bronze workflow
11. Add tests and checkpoint resume
12. Expand to Silver/Gold/MCP
13. Begin full UI phase only after runtime parity is credible

## Open Questions

1. Should Brightgemma keep its own SQLite run database, or reuse/extend Brightsmith's governance database for run metadata?
2. Should Brightgemma vendor a snapshot of Warmcraft tokens, or depend on a future shared `warmcraft` package?
3. Should Brightsmith eventually absorb `agent_runtime`, or should Brightgemma remain a separate application permanently?
4. What is the first real spec to use as the Phase 1 workflow-parity target?

