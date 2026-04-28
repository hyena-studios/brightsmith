# Framework Spec: Dual-Runtime Workflow Compatibility

**Status:** DRAFT
**Zone:** Infrastructure (cross-cutting orchestration)
**Primary Agent:** @primary-agent
**Created:** 2026-04-07

## Problem Statement

Brightsmith currently has one real workflow and two representations of it:

1. The **governance and pipeline truth** lives in framework docs, agent definitions, and Python CLIs such as `pipeline_gate`, `dq_runner`, `contract`, and `cab`
2. The **orchestration surface** is still strongly biased toward Claude Code plugin primitives such as `Agent(...)`, `subagent_type: "bs:..."`, `AskUserQuestion`, and plugin hooks

That creates three problems:

1. **Runtime lock-in.** The workflow is portable in principle, but the operational docs read as if Claude Code is the only supported runtime.
2. **Split documentation.** We now have a Codex compatibility proposal, but the core skills and workflow docs still embed Claude-specific instructions.
3. **Risk of workflow drift.** If Claude and Codex each get their own separate workflow docs, one will eventually stop matching the actual Brightsmith gate logic.

The fix is not to fork the workflow. The fix is to define a **runtime-agnostic Brightsmith workflow model** and then provide thin adapters for Claude Code and Codex. The core workflow must stay single-source and invariant across runtimes.

## Goals

- Define one canonical Brightsmith workflow that is compatible with both Claude Code and Codex
- Preserve all existing Brightsmith concepts:
  - agent roster
  - skills/runbooks
  - pipeline ordering by zone
  - human approval gates
  - session logging
  - pipeline gate enforcement
  - DQ, contracts, CAB, lineage, and governance artifacts
- Move Claude-specific and Codex-specific invocation details into explicit adapter documentation
- Make it possible to run Brightsmith end-to-end in either runtime without changing the business process

## Non-Goals

- Do not change the underlying Brightsmith pipeline logic
- Do not change agent responsibilities or reorder the pipeline unless already required by existing specs
- Do not remove Claude Code support
- Do not introduce a new plugin system
- Do not implement the migration in this spec

## Core Principle

Brightsmith must distinguish between:

- **Workflow semantics**: what steps exist, in what order, with what gates, outputs, and skip rules
- **Runtime mechanics**: how a given AI runtime delegates work, asks the human for approval, and structures sub-agents

Workflow semantics are canonical and runtime-independent.
Runtime mechanics are adapters.

## Scope

This spec covers:

- [CLAUDE.md](/Users/jcernauske/code/bright/brightsmith/CLAUDE.md)
- [agents/](/Users/jcernauske/code/bright/brightsmith/agents)
- [skills/](/Users/jcernauske/code/bright/brightsmith/skills)
- [docs/workflows/](/Users/jcernauske/code/bright/brightsmith/docs/workflows)
- Claude-specific mirror content under `.claude/`
- Human approval and session logging instructions where they currently reference Claude-only tools

This spec does not require changes to:

- `src/brightsmith/infra/pipeline_gate.py`
- `src/brightsmith/infra/dq_runner.py`
- `src/brightsmith/infra/contract.py`
- `src/brightsmith/infra/cab.py`
- `src/brightsmith/run.py`

unless a later implementation uncovers a real enforcement gap.

## Success Criteria

- [ ] A single canonical workflow document defines Brightsmith semantics independently of runtime
- [ ] Claude Code and Codex each have a runtime adapter document describing how to execute the same workflow
- [ ] `agents/` is the canonical agent library for runtime-neutral role definitions
- [ ] `.claude/agents/` is either generated from `agents/` or explicitly documented as a runtime adapter mirror
- [ ] `skills/*.md` no longer require Claude-only syntax in their primary instructions
- [ ] Any Claude-specific examples are isolated to the Claude adapter documentation
- [ ] Any Codex-specific examples are isolated to the Codex adapter documentation
- [ ] Human approval gates are defined in runtime-neutral terms and mapped cleanly to both runtimes
- [ ] Domain-context interview flow is defined in runtime-neutral terms and mapped cleanly to both runtimes
- [ ] Session logging remains authoritative regardless of runtime
- [ ] The current Brightsmith agent roster remains fully represented
- [ ] No mandatory gate, review stage, or governance artifact is lost in the migration

## Current State

### What Is Already Portable

- Pipeline sequencing in:
  - [docs/workflows/bronze-pipeline.md](/Users/jcernauske/code/bright/brightsmith/docs/workflows/bronze-pipeline.md)
  - [docs/workflows/silver-gold-pipeline.md](/Users/jcernauske/code/bright/brightsmith/docs/workflows/silver-gold-pipeline.md)
  - [docs/workflows/mcp-pipeline.md](/Users/jcernauske/code/bright/brightsmith/docs/workflows/mcp-pipeline.md)
- Approval semantics in [docs/workflows/human-approval-gates.md](/Users/jcernauske/code/bright/brightsmith/docs/workflows/human-approval-gates.md)
- Session logging semantics in [docs/workflows/session-logging.md](/Users/jcernauske/code/bright/brightsmith/docs/workflows/session-logging.md)
- Agent behavior in [agents/](/Users/jcernauske/code/bright/brightsmith/agents)
- Enforcement CLIs in `src/brightsmith/infra/`

### What Is Still Claude-Specific

- `skills/*.md` using `Agent(...)`, `subagent_type`, and `bs:` dispatch as primary instructions
- references to `AskUserQuestion` as if it were the only valid human-input mechanism
- `.claude/agents/` as a parallel agent tree
- hook assumptions from [hooks/hooks.json](/Users/jcernauske/code/bright/brightsmith/hooks/hooks.json)

## Proposed Design

### 1. Introduce a Canonical Runtime-Neutral Workflow Layer

Add a canonical workflow doc set that defines Brightsmith in terms of:

- named agents
- ordered steps
- mandatory vs skippable stages
- required artifacts
- review gates
- approval decision points
- session logging obligations
- CLI enforcement points

This layer must never mention:

- `Agent(...)`
- `subagent_type`
- `bs:`
- `spawn_agent`
- `AskUserQuestion`
- runtime-specific hooks

It should describe semantics like:

> "Dispatch the `dq-engineer` role using the current runtime's delegation mechanism."

not:

> "Call `Agent(subagent_type=\"bs:dq-engineer\")`."

### 2. Define Runtime Adapter Documents

Add two adapter documents:

- `docs/workflows/runtime-adapters/claude-code.md`
- `docs/workflows/runtime-adapters/codex.md`

These documents translate the canonical workflow into each runtime's mechanics.

#### Claude Code Adapter

Owns:
- `Agent(...)`
- `subagent_type: "bs:<agent>"`
- `AskUserQuestion`
- plugin hook expectations
- any `.claude/`-specific integration notes

#### Codex Adapter

Owns:
- when to execute locally vs delegate
- `spawn_agent`, `send_input`, or equivalent Codex-native delegation
- direct user prompts for approvals/interviews
- any Codex-specific collaboration expectations

### 3. Make `agents/` Canonical

`agents/` becomes the canonical human-readable role library.

Options for `.claude/agents/`:

1. **Generated mirror**
   - preferred if Claude runtime still needs a dedicated path
2. **Documented runtime mirror**
   - acceptable if generation is not worth it yet

Rules:
- no semantic drift between `agents/` and `.claude/agents/`
- if both trees remain, ownership and sync policy must be explicit

### 4. Convert Skills Into Runtime-Neutral Runbooks

Current skills should describe:

- what to read
- what commands to run
- which role to dispatch
- which gates to check
- which outputs to record

They should not make Claude-only invocation syntax the normative path.

Example transformation:

Current:

```text
Dispatch: Agent(description: "...", subagent_type: "bs:dq-engineer", prompt: "...")
```

Target:

```text
Dispatch the `dq-engineer` role using the current runtime adapter. Provide the spec name, zone, expected outputs, and relevant artifact paths.
```

Runtime-specific examples then move to the adapter docs.

### 5. Generalize Human Input Semantics

Approval and interview docs should define:

- the question to be asked
- the expected answer shape
- the valid decisions/options
- the required audit trail writes

They should not require one specific tool name.

Target language:

> "Collect a structured human decision using the current runtime's supported human-input mechanism."

Claude adapter:
- `AskUserQuestion`

Codex adapter:
- direct plain-text prompt in chat, preserving the same decision options

### 6. Keep Enforcement in Python and Governance Artifacts

The actual guarantees must remain in:

- pipeline gate state
- audit trail
- approval docs
- session logs
- DQ results
- contracts
- CAB decisions

This prevents runtime adapters from becoming the source of truth.

## Detailed Changes

### Change 1: Create Canonical Workflow Document

Add a new top-level workflow document that defines Brightsmith semantics without reference to Claude or Codex.

The document must include:

- agent roster
- step ordering by zone
- mandatory vs skippable steps
- gate checks
- approval stages
- artifact requirements
- session logging obligations

### Change 2: Add Runtime Adapter Docs

Create:

- `docs/workflows/runtime-adapters/claude-code.md`
- `docs/workflows/runtime-adapters/codex.md`

Each adapter must map:

- role dispatch
- human approval collection
- interview collection
- optional parallelism
- runtime-specific caveats

### Change 3: Refactor Skills To Be Runtime-Neutral

Update:

- [skills/init/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/init/SKILL.md)
- [skills/run/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/run/SKILL.md)
- [skills/mine/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/mine/SKILL.md)
- [skills/smelt/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/smelt/SKILL.md)
- [skills/cast/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/cast/SKILL.md)
- [skills/assay/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/assay/SKILL.md)
- [skills/stamp/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/stamp/SKILL.md)
- [skills/serve/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/serve/SKILL.md)
- [skills/status/SKILL.md](/Users/jcernauske/code/bright/brightsmith/skills/status/SKILL.md)

Required outcome:
- primary instructions are runtime-neutral
- runtime-specific examples live in adapter docs or subordinate examples sections clearly labeled by runtime

### Change 4: Normalize Agent Ownership

Document `agents/` as canonical and define one of:

- a generation flow for `.claude/agents/`
- a strict mirror policy

If a generation flow is chosen, the spec should define the source of truth and acceptable transformation rules.

### Change 5: Rewrite Human Approval Docs in Runtime-Neutral Terms

Update:

- [docs/workflows/human-approval-gates.md](/Users/jcernauske/code/bright/brightsmith/docs/workflows/human-approval-gates.md)
- [docs/workflows/session-logging.md](/Users/jcernauske/code/bright/brightsmith/docs/workflows/session-logging.md)
- agent docs that refer directly to `AskUserQuestion`

Required outcome:
- questions and approval options are preserved
- runtime-specific tool names are adapter concerns, not workflow concerns

### Change 6: Codify the Domain-Context Interview Adapter Pattern

The `domain-context` interview is the highest-risk place for runtime drift because it is interactive and multi-round.

The spec must ensure:

- the interview remains mandatory
- the question structure remains explicit
- human responses remain logged verbatim
- both runtimes support iterative follow-up and proposal/clarification loops

## Acceptance Tests

The implementation is complete when the following are true:

1. A reader can understand the Brightsmith workflow without knowing whether Claude Code or Codex is being used.
2. A Claude Code user can still execute the workflow with no loss of functionality.
3. A Codex user can execute the same workflow with no missing gates or implicit Claude-only assumptions.
4. The same spec, same pipeline state, and same governance artifacts are valid in both runtimes.
5. A diff between `agents/` and `.claude/agents/` shows either no semantic divergence or an intentional, documented adapter transformation only.

## Risks

### Risk 1: Duplicate Truth

If runtime-neutral docs and runtime adapters both restate the workflow in full, they will drift.

Mitigation:
- canonical docs define semantics once
- adapters only describe translation mechanics

### Risk 2: Weakening of Human Gates

If `AskUserQuestion` is removed from the core docs without a strong replacement contract, approvals could become vague free-form chat.

Mitigation:
- preserve exact decision shapes and logging requirements
- only abstract the mechanism, not the structure

### Risk 3: Agent Drift Between `agents/` and `.claude/agents/`

Mitigation:
- make ownership explicit
- prefer generation or at minimum a documented sync check

### Risk 4: Over-Abstracting Runtime Behavior

If the neutral layer becomes too vague, it stops being executable.

Mitigation:
- neutral docs still specify exact steps, artifacts, and decisions
- only invocation syntax moves to adapters

## Open Questions

1. Should `.claude/agents/` remain as committed files, or should they be generated from `agents/`?
2. Should runtime-specific examples stay inline in skill docs under labeled subsections, or move entirely into adapter docs?
3. Should the existing Codex compatibility proposal be absorbed into the new canonical/adapter structure or retained as a transitional design record?

## Implementation Plan

This spec does not implement the migration. When implementation begins, recommended order:

1. Create canonical workflow doc
2. Create Claude and Codex adapter docs
3. Refactor skills to runtime-neutral wording
4. Refactor human-input docs to runtime-neutral wording
5. Resolve `agents/` vs `.claude/agents/` ownership
6. Run a doc consistency pass across all referenced files

## Deliverables

- New canonical workflow doc
- New Claude Code adapter doc
- New Codex adapter doc
- Refactored skills
- Refactored human approval/session logging docs
- Ownership policy for `agents/` and `.claude/agents/`

## Out of Scope For This Spec

- Converting Brightsmith into a standalone runtime-agnostic package with first-class adapters in code
- Changing Python CLI behavior
- Reworking pipeline gate state formats
- Introducing a third runtime beyond Claude Code and Codex
