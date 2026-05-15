# Codex-Compatible Brightsmith Workflow

This document translates the existing Claude Code plugin workflow into a Codex-native operating model without requiring a plugin. The goal is parity, not simplification: the same Brightsmith agents, skills, pipeline ordering, governance artifacts, and human approval gates still exist, but orchestration is expressed in terms Codex can actually execute.

## Design Goals

- Preserve the Brightsmith pipeline contract defined in [CLAUDE.md](../../CLAUDE.md), [docs/workflows/bronze-pipeline.md](../../docs/workflows/bronze-pipeline.md), [docs/workflows/silver-gold-pipeline.md](../../docs/workflows/silver-gold-pipeline.md), [docs/workflows/mcp-pipeline.md](../../docs/workflows/mcp-pipeline.md), and [docs/workflows/human-approval-gates.md](../../docs/workflows/human-approval-gates.md).
- Reuse the repository's top-level [agents/](../../agents) and [skills/](../../skills) directories as the source of agent behavior.
- Replace Claude-only concepts such as `Agent(...)`, `subagent_type: "bs:..."`, hooks, and `AskUserQuestion` with Codex-native equivalents.
- Keep the same review gates, triple-write approval logging, and pipeline gate enforcement.

## What Changes Under Codex

### Orchestration Primitive

Claude plugin model:
- Skills dispatch named plugin agents via `Agent(subagent_type="bs:<agent-name>")`
- A hook rejects agent calls that omit `subagent_type`

Codex model:
- The main Codex session is the orchestrator
- When delegation is useful, Codex uses `spawn_agent` or `send_input`
- Agent identity lives in the prompt context, not in a plugin namespace
- The source of truth for each agent remains its markdown definition in [agents/](../../agents)

Recommended dispatch pattern:

1. Read the relevant file in `agents/<agent-name>.md`
2. Summarize the current spec, required inputs, expected outputs, and file ownership
3. Spawn a Codex sub-agent with those instructions in the task prompt
4. On completion, register the output with `pipeline_gate complete`

This is the Codex equivalent of `bs:<agent-name>`.

### Human Interaction Primitive

Claude plugin model:
- Structured approvals and interviews use `AskUserQuestion`

Codex model:
- Use direct user messages when human input is required
- Approval or interview prompts must still be explicit, enumerated, and logged in the session artifact
- If the runtime offers a structured input helper in the future, it can wrap the same questions, but the workflow must not depend on it

### Hook Enforcement

Claude plugin model:
- [hooks/hooks.json](../../hooks/hooks.json) enforces typed subagent usage

Codex model:
- No hook is required
- Enforcement moves into the orchestrator checklist:
  - every mandatory Brightsmith agent must either run or be explicitly skipped
  - every skip must cite evidence
  - every approval gate must either collect a human decision or auto-approve per `REQUIRE_HUMAN_APPROVAL`

## Codex Agent Catalog

The Codex version keeps the same Brightsmith specialist roles. The repository already mirrors almost all Claude agent specs in [agents/](../../agents); `cab-agent` is added there as well.

Core agents:
- `setup`
- `governance-reviewer`
- `staff-engineer`
- `primary-agent`
- `data-analyst`
- `domain-context`
- `dq-rule-writer`
- `dq-engineer`
- `chaos-monkey`
- `lineage-tracker`
- `cde-tagger`
- `doc-generator`

Conditional or domain-specific agents:
- `entity-resolver`
- `pii-scanner`
- `temporal-modeler`
- `adversarial-auditor`
- `data-steward`
- `semantic-modeler`
- `cab-agent`
- `principal-data-architect`
- `insight-manager`
- `policy-engineer`
- `bcbs239-auditor`
- `mcp-engineer`
- `content-strategist`
- `web-designer`

Codex execution guidance:
- Run mandatory blocking work locally when the main session needs the result immediately
- Use `spawn_agent` for bounded sidecar tasks or parallel read/review work
- Pass the relevant `agents/<name>.md` instructions into the spawned task instead of relying on a plugin registration
- Keep write ownership disjoint when multiple sub-agents edit files

## Codex Skill Mapping

The existing Brightsmith skills remain valid conceptually, but they should be treated as runbooks rather than Claude slash commands.

| Claude Skill | Codex Equivalent |
|---|---|
| `/bs:init` | Main Codex session follows [skills/init/SKILL.md](../../skills/init/SKILL.md) as a runbook and optionally delegates to `setup` |
| `/bs:mine` | Execute bronze runbook in [skills/mine/SKILL.md](../../skills/mine/SKILL.md) using Codex orchestration |
| `/bs:smelt` | Execute silver runbook in [skills/smelt/SKILL.md](../../skills/smelt/SKILL.md) using Codex orchestration |
| `/bs:cast` | Execute gold runbook in [skills/cast/SKILL.md](../../skills/cast/SKILL.md) using Codex orchestration |
| `/bs:serve` | Execute [skills/serve/SKILL.md](../../skills/serve/SKILL.md) directly from the main session |
| `/bs:run` | Use [skills/run/SKILL.md](../../skills/run/SKILL.md) as the master orchestration checklist |
| `/bs:assay` | Execute [skills/assay/SKILL.md](../../skills/assay/SKILL.md) directly |
| `/bs:stamp` | Execute [skills/stamp/SKILL.md](../../skills/stamp/SKILL.md) directly |
| `/bs:status` | Execute [skills/status/SKILL.md](../../skills/status/SKILL.md) directly |

Required translation rule:
- Anywhere a skill says `Agent(description: ..., subagent_type: "bs:<name>", prompt: ...)`, replace it with "read `agents/<name>.md`, then either execute locally or delegate via `spawn_agent` with the same contextual prompt."

## End-to-End Codex Pipeline

### Session Start

1. Create a session log per [docs/workflows/session-logging.md](../../docs/workflows/session-logging.md)
2. Record the exact user prompt
3. Read the relevant zone workflow documents on demand
4. If this is a new domain project, follow the `setup` runbook first

### Per-Step Execution Contract

For every Brightsmith step:

1. Run `python3 -m brightsmith.infra.pipeline_gate check <spec> <step>`
2. If `BLOCKED`, stop and surface the blocker
3. Execute the named agent locally or through a spawned Codex sub-agent
4. Write or update the required governance artifact
5. Run `python3 -m brightsmith.infra.pipeline_gate complete <spec> <step> --output <path>`
6. If the step is skipped, run `pipeline_gate skip` with a reason and evidence path

### Bronze

Use the same order as [docs/workflows/bronze-pipeline.md](../../docs/workflows/bronze-pipeline.md):

1. `governance-reviewer`
2. `primary-agent`
3. `data-analyst`
4. `domain-context`
5. `dq-rule-writer`
6. `dq-engineer`
7. `chaos-monkey`
8. Optional: `entity-resolver`, `pii-scanner`, `temporal-modeler`, `adversarial-auditor`
9. `lineage-tracker`
10. `cde-tagger`
11. `doc-generator`
12. `governance-reviewer`
13. `staff-engineer`

### Silver/Gold

Use the same greenfield/backfill branching and ordering from [docs/workflows/silver-gold-pipeline.md](../../docs/workflows/silver-gold-pipeline.md), including:

- business term, conceptual model, and logical model approval gates
- CAB review for changes to existing contracted tables
- chaos monkey hardening loop
- physical model before implementation in greenfield mode
- contract and golden dataset enforcement

### MCP

Use the same rules from [docs/workflows/mcp-pipeline.md](../../docs/workflows/mcp-pipeline.md):

- evaluation set with at least 50 mechanically verifiable cases
- correctness verification, not just structure checks
- headless readiness before serving
- contract and DQ gate enforcement between zones

## Human Review Gates Under Codex

The human gate behavior stays the same. Only the collection mechanism changes.

### Standard Approval Flow

1. Producing agent writes the artifact
2. `doc-generator` writes a human approval document to `governance/approvals/`
3. Codex tells the user exactly which file to review
4. Codex asks for a decision in plain text with the same discrete choices Claude used
5. Codex records the decision in:
   - `pipeline_gate approve`
   - `governance/audit-trail/{spec}-approvals.md`
   - the current session log's Human Input Log

Example approval prompt:

```text
Review governance/approvals/{spec}-{artifact}-approval.md and reply with one of:
1. Approved
2. Approved with notes: <notes>
3. Changes requested: <changes>
4. Need more info
```

### Auto-Approval Behavior

When `REQUIRE_HUMAN_APPROVAL=False`:

- approval documents are still generated
- Codex auto-approves the gate
- the auto-approval reason is recorded in pipeline state, audit trail, and session log

### Gates That Always Need Human Review

- CAB `MAJOR` schema changes
- Any approval explicitly required by policy even when global approval is disabled
- Any case where the user overrides an agent classification or recommendation

## Domain-Context Interview Under Codex

The `domain-context` agent currently assumes `AskUserQuestion` for its interview flow. Under Codex, keep the interview but present each question directly to the user in the chat.

Rules:
- Ask one targeted question at a time
- Offer bounded options when the agent definition expects them
- Log the exact human response in the session file
- If the user defers or says "handle it", log that exact text and continue with explicit risk notes

This preserves the workflow intent even without a dedicated question tool.

## Suggested Codex Operating Pattern

Recommended main-session responsibilities:
- read specs and workflow documents
- run `pipeline_gate` and CLI infrastructure commands
- decide whether a step should be local work or delegated work
- collect human approvals
- maintain the session log
- ensure every mandatory agent is accounted for

Recommended sub-agent usage:
- `explorer` for audits, evidence gathering, and codebase inspection
- `worker` for bounded implementation or document generation tasks with explicit file ownership
- `default` for general-purpose specialist tasks when the work is broader than a narrow code patch

## Minimal Implementation Plan

If you want to operationalize this in Codex without building a plugin, the smallest coherent setup is:

1. Keep [agents/](../../agents) as the specialist instruction library
2. Keep [skills/](../../skills) as human-readable runbooks
3. Use this document as the Claude-to-Codex translation layer
4. Optionally add thin wrapper prompts or scripts later, but do not move workflow authority out of the markdown specs and pipeline gate commands

## Known Gaps From The Current Repository

- The top-level skill docs still mention Claude-only `Agent(...)`, `bs:` namespaces, and hook behavior
- Several agent docs still mention `AskUserQuestion` literally; under Codex that should be read as "ask the user directly and log the answer"
- `cab-agent` existed only in `.claude/agents/` and has been mirrored into [agents/cab-agent.md](../../agents/cab-agent.md)

Those are documentation and orchestration gaps, not workflow gaps. The underlying Brightsmith governance model ports cleanly to Codex.
