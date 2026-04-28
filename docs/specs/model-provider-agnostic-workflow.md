# Framework Spec: Model- and Provider-Agnostic Workflow

**Status:** DRAFT
**Zone:** Infrastructure (cross-cutting orchestration)
**Primary Agent:** @staff-engineer
**Created:** 2026-04-07

## Problem Statement

Brightsmith's execution model is currently framed as a Claude Code plugin workflow, even though most of the real workflow semantics already live outside the plugin:

- the plugin manifest at `.claude-plugin/plugin.json` is minimal and does not encode the pipeline
- the real gate logic lives in Python CLIs such as `pipeline_gate`, `cab`, `contract`, and `dq_runner`
- the real workflow ordering lives in `CLAUDE.md`, `docs/workflows/`, `skills/`, and agent instructions

The current issue is not that Brightsmith needs a better plugin. The issue is that the orchestration contract is still written in Claude-specific terms:

- `Agent(...)`
- `subagent_type: "bs:<agent>"`
- `AskUserQuestion`
- Claude hook enforcement in `hooks/hooks.json`
- a duplicated Claude-only agent tree under `.claude/agents/`

That makes the workflow appear provider-bound even though the business process is not. It also creates semantic drift risk because the repository now contains:

1. canonical-looking runtime-neutral material under `agents/`
2. Claude-specific mirror material under `.claude/agents/`
3. workflow docs and skills that still treat the Claude plugin as the primary execution surface

The target state is a single Brightsmith workflow that is model- and provider-agnostic and can be executed by either OpenAI Codex or Claude Code without changing the governance process, pipeline stages, or required artifacts.

## Goals

- Define one canonical Brightsmith workflow independent of model vendor, provider, or plugin system
- Support both OpenAI Codex and Claude Code as runtime adapters over the same workflow
- Preserve existing Brightsmith semantics:
  - agent roles
  - skill/runbook intent
  - zone sequencing
  - human approval gates
  - session logging
  - pipeline gate enforcement
  - governance artifact production
- Remove Claude-plugin syntax from canonical workflow instructions
- Make plugin packaging optional rather than foundational
- Establish one canonical source for agent definitions and one canonical source for workflow semantics

## Non-Goals

- Do not redesign the Brightsmith pipeline
- Do not remove Claude Code support
- Do not require a new plugin system for Codex or Claude
- Do not change the `pipeline_gate` state machine unless implementation reveals a real enforcement gap
- Do not change zone ordering, approval requirements, or mandatory artifacts in this spec

## Current State

### What Is Actually Canonical Today

- `src/brightsmith/infra/*.py` enforces gates and governance checks
- `docs/workflows/*.md` describes the intended pipeline shape
- `agents/*.md` already contains a runtime-neutral copy of most agent roles

### What Is Still Claude-Bound

- `CLAUDE.md` is written as the primary operating contract
- `skills/*.md` repeatedly mandate `Agent(...)`, `subagent_type`, and the `bs:` namespace
- `docs/workflows/human-approval-gates.md` assumes `AskUserQuestion`
- `hooks/hooks.json` enforces Claude plugin dispatch shape
- `.claude/agents/` duplicates `agents/`, and `diff -qr agents .claude/agents` currently reports drift across many files

### Root Cause

Brightsmith mixed two different concerns into the same documents:

- workflow semantics: what must happen
- runtime mechanics: how a given AI client performs delegation and user interaction

Those need to be separated.

## Design Principle

Brightsmith must define:

1. a canonical workflow layer that is runtime-neutral
2. thin runtime adapters for Claude Code and Codex
3. optional packaging/integration layers such as a Claude plugin

The workflow cannot depend on any specific tool name, plugin API, or model provider primitive.

## Proposed Architecture

### 1. Canonical Workflow Layer

Create or refactor the core workflow docs so they describe only:

- ordered steps
- required inputs
- expected outputs
- skippable vs mandatory stages
- gate checks
- approval decisions
- audit trail obligations

The canonical layer must not mention:

- `Agent(...)`
- `subagent_type`
- `bs:`
- `AskUserQuestion`
- `spawn_agent`
- Claude hooks
- plugin installation behavior

Canonical phrasing should use abstractions such as:

- "Dispatch the `dq-engineer` role using the active runtime adapter."
- "Collect a structured human decision with one of the allowed approval outcomes."
- "Register completion with `pipeline_gate complete`."

### 2. Runtime Adapter Layer

Add adapter docs for each supported runtime:

- `docs/workflows/runtime-adapters/claude-code.md`
- `docs/workflows/runtime-adapters/openai-codex.md`

These adapters own the runtime-specific translation:

#### Claude Code Adapter

- maps role dispatch to `Agent(...)`
- maps agent identity to `subagent_type: "bs:<agent>"`
- maps human interaction to `AskUserQuestion` when available
- documents `.claude/agents/` and any hook behavior
- treats the Claude plugin as optional packaging around the adapter, not the workflow itself

#### OpenAI Codex Adapter

- maps role dispatch to local execution, `spawn_agent`, or `send_input`
- maps human interaction to direct user messages or structured input helpers if available
- documents how to pass role instructions from `agents/<name>.md`
- does not require plugin packaging

### 3. Optional Packaging Layer

Packaging becomes an implementation detail:

- `.claude-plugin/plugin.json` may remain as a Claude distribution artifact
- no equivalent packaging is required for Codex
- the workflow must remain runnable from repo docs plus built-in runtime capabilities

This is the key inversion: the plugin becomes a consumer of the workflow, not the owner of the workflow.

## Canonical Repository Structure

### Source of Truth

- `docs/workflows/` becomes the canonical workflow library
- `agents/` becomes the canonical role library
- `skills/` becomes runtime-neutral runbooks

### Runtime-Specific Material

- `.claude/agents/` becomes either:
  - a generated mirror of `agents/`, preferred
  - or a documented adapter-owned mirror with an explicit sync rule
- `.claude-plugin/` becomes optional packaging only
- `hooks/` becomes Claude-adapter-specific, not workflow-global

### New Documentation Layout

- `docs/workflows/canonical/`
  - `overview.md`
  - `bronze.md`
  - `silver-gold.md`
  - `mcp.md`
  - `human-input.md`
  - `session-logging.md`
- `docs/workflows/runtime-adapters/`
  - `claude-code.md`
  - `openai-codex.md`

If maintaining the current `docs/workflows/` filenames is lower risk, the same split may be done in-place, but the semantic distinction must still be explicit.

## Runtime-Neutral Execution Contract

Every Brightsmith workflow step must follow this contract regardless of runtime:

1. Read the relevant spec and required upstream artifacts
2. Check gate state with `python3 -m brightsmith.infra.pipeline_gate check`
3. Dispatch the named role through the active runtime adapter
4. Produce the required governance artifact(s)
5. Register completion with `pipeline_gate complete`
6. If skipped, register the skip with reason and evidence via `pipeline_gate skip`
7. Preserve all human input and decisions in session logging and audit trail artifacts

This contract is already mostly enforced by the Python layer and should remain the invariant shared by Codex and Claude.

## Role Dispatch Model

### Canonical Definition

Roles are identified by stable names matching files in `agents/`:

- `governance-reviewer`
- `staff-engineer`
- `data-analyst`
- `domain-context`
- `dq-rule-writer`
- `dq-engineer`
- `chaos-monkey`
- `lineage-tracker`
- `cde-tagger`
- `doc-generator`
- and the rest of the existing roster

### Adapter Translation

- Claude Code resolves a role name to `bs:<role>` and dispatches it via plugin/subagent primitives
- Codex resolves a role name to `agents/<role>.md` and either executes locally or delegates with built-in agent tools

The canonical workflow should refer only to role names, never runtime-specific namespace syntax.

## Human Input Model

### Canonical Definition

Human interaction must be specified in terms of:

- trigger condition
- exact question intent
- allowed answer shape
- required logging
- effect on gate state

Two distinct interaction types exist:

- approval decisions
- discovery/interview questions

### Adapter Translation

- Claude Code may use `AskUserQuestion`
- Codex may use direct chat prompts or structured input helpers

The workflow definition must not require a particular UI primitive.

## Skill Conversion

The existing `skills/*.md` files should be converted from Claude plugin commands into runtime-neutral runbooks.

### Current Pattern

Current skills repeatedly define the orchestrator like this:

- use Bash plus `Agent`
- never implement directly
- always call `subagent_type: "bs:<agent>"`

### Target Pattern

Each skill should instead define:

- the purpose of the runbook
- the required inputs
- the workflow steps and gate commands
- the named roles that must be dispatched
- the expected outputs
- runtime notes pointing to the adapter docs

Example transformation:

Current:

```text
Dispatch: Agent(description: "...", subagent_type: "bs:dq-engineer", prompt: "...")
```

Target:

```text
Dispatch the `dq-engineer` role using the active runtime adapter. Pass the spec name, zone, relevant artifact paths, and expected output path.
```

This keeps the workflow stable while allowing each runtime to decide how delegation actually happens.

## Agent Library Canonicalization

`agents/` must become canonical.

### Required Rules

- all role semantics are authored first in `agents/`
- `.claude/agents/` cannot diverge silently
- any Claude-only additions must be clearly marked as adapter notes, not role semantics

### Recommended Enforcement

Implement one of:

1. a generator that derives `.claude/agents/` from `agents/`
2. a CI/test check that fails on semantic drift
3. removal of `.claude/agents/` if Claude can consume `agents/` directly

The current diff between the two trees is a concrete risk and should be treated as migration debt.

## CLAUDE.md Replacement Strategy

`CLAUDE.md` currently acts like both:

- repo operating manual
- Claude-specific runtime contract

That should be split.

### Target State

- a runtime-neutral repo operations doc, ideally `AGENTS.md` or `docs/workflows/canonical/overview.md`
- a Claude-specific adapter doc that explains how Claude executes the same workflow

If `CLAUDE.md` must remain for Claude compatibility, it should become a thin adapter entrypoint that points to canonical docs instead of carrying the full workflow contract itself.

## Hook Strategy

`hooks/hooks.json` currently enforces Claude-specific dispatch behavior.

That enforcement should be reclassified as adapter-local behavior:

- valid for Claude
- irrelevant for Codex
- not part of the canonical workflow contract

Any workflow requirement currently enforced only by a Claude hook should be moved into one of:

- canonical documentation
- a Python enforcement CLI
- a repo test

This prevents runtime-specific hooks from being the only thing preserving workflow correctness.

## Migration Plan

### Phase 1: Define Canonical Docs

- create the canonical workflow docs
- move runtime-specific language out of core workflow documents
- define stable adapter responsibilities for Claude and Codex

### Phase 2: Canonicalize Roles

- declare `agents/` authoritative
- choose generated mirror, validated mirror, or direct reuse for `.claude/agents/`
- add a sync check to prevent future drift

### Phase 3: Convert Skills

- rewrite `skills/init`, `skills/run`, `skills/mine`, `skills/smelt`, `skills/cast`, `skills/assay`, `skills/stamp`, `skills/serve`, and `skills/status`
- remove normative Claude plugin syntax from skill bodies
- add short runtime notes linking to adapter docs

### Phase 4: Normalize Human Input

- rewrite approval and interview docs in runtime-neutral terms
- isolate `AskUserQuestion` examples to the Claude adapter
- define Codex equivalents for approvals and discovery interviews

### Phase 5: Demote Plugin Packaging

- keep `.claude-plugin/plugin.json` only as optional Claude packaging
- remove any implication that plugin installation is required for Brightsmith itself
- ensure repo docs explain how to run the workflow without plugin packaging

## Acceptance Criteria

- [ ] One canonical workflow description exists without Claude- or Codex-specific primitives
- [ ] Claude Code and OpenAI Codex each have an adapter doc for the same workflow
- [ ] `agents/` is the canonical role library
- [ ] drift between `agents/` and `.claude/agents/` is prevented by generation or test enforcement
- [ ] `skills/*.md` no longer require `Agent(...)`, `subagent_type`, or `bs:` as normative instructions
- [ ] approval and interview flows are specified independently of `AskUserQuestion`
- [ ] plugin packaging is optional and documented as such
- [ ] no mandatory gate, approval, or governance artifact is lost
- [ ] the same spec can be executed end-to-end under either Claude Code or Codex

## Risks

### Risk 1: Semantic Drift During Migration

If runtime-specific examples remain mixed into core docs, the repo will continue to fork operationally.

Mitigation:

- make the canonical vs adapter split explicit
- add drift checks for agent mirrors and skill wording where practical

### Risk 2: Human Input Becomes Underspecified

If `AskUserQuestion` is removed without replacing it with an interaction contract, approvals may degrade into vague chat.

Mitigation:

- define allowed options, audit requirements, and gate side effects in canonical docs
- leave UI details to adapters only

### Risk 3: Codex and Claude Behave Differently on Delegation

The runtimes have different delegation primitives and collaboration models.

Mitigation:

- standardize required inputs and expected outputs for each role
- keep gate registration and artifact requirements in the invariant layer

## Implementation Notes

This spec intentionally does not require a plugin abstraction that works in both runtimes. The compatible unit is the workflow, not the packaging.

Claude Code may continue to use a plugin.
OpenAI Codex may run directly from repo instructions and built-in delegation tools.
Both are valid as long as they execute the same Brightsmith workflow contract.

## Recommended Next Changes

1. Promote this spec over `docs/specs/dual-runtime-workflow.md` or merge the two into one canonical migration spec
2. Rewrite `docs/workflows/human-approval-gates.md` into runtime-neutral language
3. Rewrite `skills/run/SKILL.md` first, because it currently encodes the master Claude-specific dispatch contract
4. Add a test or script that fails if `agents/` and `.claude/agents/` drift
5. Reduce `CLAUDE.md` to a Claude adapter entrypoint instead of a workflow source of truth
