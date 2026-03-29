# Framework Spec: No Hardcoded Entity Data

**Status:** DRAFT
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @governance-reviewer
**Created:** 2026-03-28

## Problem Statement

During the sec-edgar field test, a silver zone transformer hardcoded a `_FISCAL_YEAR_END_MONTHS` dictionary mapping 5 company CIKs to their fiscal year end months:

```python
_FISCAL_YEAR_END_MONTHS = {
    "0000320193": 9,   # Apple
    "0000789019": 6,   # Microsoft
    "0001018724": 12,  # Amazon
    "0001652044": 12,  # Alphabet
    "0001326801": 12,  # Meta
}
```

The spec explicitly said to read fiscal year end from `entity_dim`. The hardcoded dict:
- Worked for exactly 5 companies and would silently produce wrong results for any new entity
- Required a code change to add a new company — violating Brightsmith's design principle that adding an entity is a config change, not a code change
- Bypassed the governance artifact chain (entity-registry.json → entity_dim → transformer)
- Was invisible to DQ rules because the output values were technically valid — just sourced from the wrong place

This is not an isolated incident. Any domain project could make the same mistake: hardcoding entity-specific lookup data as Python constants instead of reading from governance artifacts or source data.

## Success Criteria

- [ ] CLAUDE.md Rules section prohibits hardcoded entity data with specific examples of violation patterns
- [ ] CLAUDE.md Rules section defines the litmus test: "adding a new entity requires only config/registry update + pipeline re-run"
- [ ] @staff-engineer agent definition includes a check for hardcoded entity dicts/lists/if-elif chains, with REJECT disposition
- [ ] @adversarial-auditor agent definition includes a skepticism bullet about hardcoded entity data patterns
- [ ] All agents (via CLAUDE.md) understand that entity-specific values come from: governance artifacts (`governance/entity-registry.json`, `domain/sources/*.yaml`, `governance/business-glossary.json`) or runtime derivation from source data — never from Python literals

## Technical Design

This spec is purely documentation and policy — no Python code changes required.

### 1. CLAUDE.md Rules Section

Add three bullets to the `## Rules` section:

1. **The prohibition:** Never hardcode entity-specific data in Python source code. All entity-specific values must come from governance artifacts or be derived from source data at runtime.
2. **Violation patterns:** Python dicts keyed by CIK/ticker/entity name, if/elif chains branching on entity identifiers, list literals containing specific entity IDs.
3. **The litmus test:** If adding a new entity to entity-registry.json and re-running the pipeline does not produce correct results without code changes, the implementation violates this rule.

### 2. Staff Engineer Agent (`.claude/agents/staff-engineer.md`)

Add a "What You Check" bullet that instructs @staff-engineer to scan for hardcoded entity dicts/lists and if/elif chains on entity identifiers. Disposition: REJECT if adding a new entity would require a code change.

### 3. Adversarial Auditor Agent (`.claude/agents/adversarial-auditor.md`)

Add a "What You're Skeptical About" bullet that instructs @adversarial-auditor to search source code for Python dicts keyed by CIK numbers or ticker symbols, and flag them as governance violations. Frame the question: "What happens when entity #6 is added?"

### 4. Primary Agent Guidance

No `primary-agent.md` exists — it is an abstract role referenced in specs, not a concrete agent definition. The CLAUDE.md rules (read by all agents, including whoever acts as @primary-agent) provide sufficient coverage. If a primary-agent.md is created in the future, this rule should be added to its implementation guidelines.

## Governance Artifacts Affected

| Artifact | Change |
|----------|--------|
| `CLAUDE.md` | 3 new rule bullets |
| `.claude/agents/staff-engineer.md` | 1 new "What You Check" bullet |
| `.claude/agents/adversarial-auditor.md` | 1 new "What You're Skeptical About" bullet |

## Where Entity Data Should Live

| Data Type | Correct Location | Example |
|-----------|-----------------|---------|
| Fiscal year end months | `governance/entity-registry.json` or `entity_dim` table | Read at runtime via SQL join |
| CIK-to-company mappings | `domain/sources/*.yaml` or `entity_dim` table | Populated during bronze ingestion |
| Ticker symbols | `governance/entity-registry.json` | Read by transformer at runtime |
| Sector/industry mappings | `governance/business-glossary.json` or source data | Derived from source taxonomy |
| Entity lists (which companies to process) | `governance/entity-registry.json` | Pipeline iterates over registry |

## Tests

No code tests required — this is a policy spec. Compliance is verified by:
- @staff-engineer code review (mandatory final gate on every spec)
- @adversarial-auditor audit (checks for hardcoded patterns)
- @governance-reviewer pre-implementation review (checks that specs don't call for hardcoding)

## Relationship to Other Specs

This spec is independent of all existing specs. It adds a cross-cutting governance constraint that applies to every future spec in every zone.
