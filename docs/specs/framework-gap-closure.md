# Framework Spec: Closing the Domain-Agnostic Gap

## Context
Comparison of sec_edgair (domain-specific, 13 specs, 143 DQ rules, 54 business terms) vs sec_edgar_grist_2 (domain-agnostic via Grist framework, 4 specs, 37 DQ rules, 14 terms) revealed a ~35-40% gap in data product richness. The gap is NOT a framework limitation — the tools exist but were either skipped, under-utilized, or lack enforcement. This spec defines changes to close that gap.

## Root Causes

1. **@domain-context user interview was skippable.** User said "handle it" → all concept elicitation deferred → no concept normalization happened. The ConceptNormalizer ran in discovery mode (no mappings).
2. **@principal-data-architect was never invoked.** The agent exists but the pipeline orchestrator (`/grist:run`) doesn't call it at zone transitions.
3. **@insight-manager recommendations were advisory.** Tier 1 products (ratios, CAGR) were recommended but only the core dedup table was built.
4. **Several pipeline agents were skipped entirely.** @entity-resolver, @temporal-modeler, @pii-scanner, @adversarial-auditor never ran.
5. **No eval set or verification suite was produced.** sec_edgair had 88 verification checks + 200 eval cases; Grist had 3 golden values.

---

## Change 1: Rewrite @domain-context User Interview

**File:** `.claude/agents/domain-context.md`

**Problem:** The interview section says "present 5-10 targeted questions to the user" — but it just emits text. The user can dismiss it with one sentence and the agent moves on. There's no mechanism for the user to ask questions back, request exploration, or for the agent to make proposals when the user doesn't know.

**Changes:**

### 1a. Use AskUserQuestion tool, not text output

Replace the current "EDA-Informed User Interview" section (lines 146-164) with:

```markdown
## Interactive Domain Interview (MANDATORY)

After reading the EDA report but BEFORE synthesizing the domain context, conduct an interactive interview with the user using the `AskUserQuestion` tool. This is NOT optional — concept elicitation is the single most important step for downstream data product quality.

### Interview Protocol

The interview is a **multi-round conversation**, not a one-shot questionnaire. Use `AskUserQuestion` for EVERY question — never emit questions as plain text and hope the user responds.

### Round 1: User Domain Expertise Assessment

Before asking domain questions, determine what the user knows. Use AskUserQuestion:

**Question:** "How familiar are you with this data domain?"
**Options:**
- "Expert — I work with this data regularly" → Proceed with targeted concept elicitation questions
- "Familiar — I know the basics but not the details" → Mix of questions and proposals
- "New to this — I chose this data source but don't know the internals" → Agent-led exploration with proposals
- "Just exploring — show me what you find" → Full agent-led discovery mode

This answer determines the interview style for all subsequent questions.

### Round 2: Concept Elicitation (BLOCKING — cannot be skipped)

This is the most important question in the entire pipeline. The answer drives concept normalization, which determines whether the consumable zone produces queryable business metrics or raw classification codes.

**For Expert/Familiar users**, ask via AskUserQuestion:
> "The EDA found {N} distinct classification codes in this data (e.g., {top 5 examples with counts}). What are the 10-25 business concepts you actually care about? For example, in financial data these might be 'Revenue', 'Net Income', 'Total Assets'. In healthcare, 'Office Visit', 'Lab Test', 'Prescription'."

**For New/Exploring users**, the agent MUST:
1. Query the raw data to identify the top 50 most frequent classification codes
2. Use domain knowledge to group them into candidate business concepts
3. Present a PROPOSED concept list via AskUserQuestion:
   > "Based on the data and my knowledge of {identified domain}, here are the business concepts I recommend normalizing to. Review and adjust:"
   **Options:**
   - "Accept this list" → Use as-is
   - "I want to modify it" → User provides edits (via "Other" free text)
   - "Explore more — show me what other concepts exist" → Agent queries data for additional codes, groups them, presents another round
   - "I don't care about normalization" → Agent documents this as a RISK and proceeds, but still produces a best-effort concept map with a warning that downstream queryability will be limited

**Critical rule:** If the user selects "I don't care about normalization", the agent MUST still produce a proposed concept map in the domain context document under a section called "## Proposed Concept Normalization (Unconfirmed)". Downstream agents can use it with lower confidence. The @principal-data-architect will flag this gap at the zone transition review.

### Round 3: Follow-Up Exploration (user-driven)

After each AskUserQuestion response, check if the user's answer raises new questions. The user may also:
- **Ask the agent to explore something**: If the user says "what other revenue-related tags exist?" or "show me what's in the healthcare codes", the agent should query the data, summarize findings, and present results before asking the next question.
- **Ask clarifying questions back**: If the user says "what do you mean by temporal grain?", explain in plain language before re-asking.
- **Request proposals**: If the user says "just suggest something", the agent proposes based on domain knowledge + EDA evidence and asks for confirmation.

Use AskUserQuestion for each follow-up. The interview continues until:
1. Concept list is confirmed (or explicitly declined with risk documented), AND
2. At least 3 of the 5 question categories have been addressed (temporal, grain, semantics, edge cases, external context)

### Round 4: Remaining Interview Questions

For each remaining question category (temporal patterns, grain/uniqueness, domain semantics, edge cases, external context), ask ONE targeted question via AskUserQuestion. Each question MUST have:
- An option for "Use industry standard" (agent applies its domain knowledge)
- An option for "Tell me more first" (agent explores and comes back with findings)
- An option for the user to answer directly

### Handling "I Don't Know" Across All Questions

For ANY question where the user selects an equivalent of "I don't know" or "handle it":

1. The agent MUST still produce an answer — using domain knowledge, EDA evidence, and best judgment
2. Document the assumption in domain-context.md under "## Assumptions (User-Deferred)"
3. Flag confidence as MEDIUM or LOW for that section
4. Generate a mandatory DQ rule requirement for @dq-rule-writer
5. The @principal-data-architect will review all user-deferred assumptions at zone transitions
```

### 1b. Add Concept Normalization Output Section

Add to the domain context document template (after "Concept Mapping Guidance"):

```markdown
## Canonical Concept Map

This section is the PRIMARY INPUT for the ConceptNormalizer. It defines the target business concepts that raw classification codes should normalize to.

**Status:** CONFIRMED | PROPOSED (Unconfirmed) | NOT ATTEMPTED
**Source:** User interview | Agent-proposed | Domain knowledge default

### Target Business Concepts
| # | Business Concept | Plain English Name | Expected Source Codes | Financial Statement | Priority |
|---|-----------------|-------------------|----------------------|--------------------|-----------|
| 1 | [e.g., Revenue] | [e.g., Total Revenue] | [e.g., Revenues, RevenueFromContract*] | [e.g., Income Statement] | CORE / EXTENDED / OPTIONAL |

### Concept-to-Code Mapping Rules
[JSON-compatible mapping rules for ConceptNormalizer, organized by tier: exact → prefix → pattern → heuristic]

### Collision Resolution Rules
[When multiple source codes map to the same business concept for the same entity-period, which code wins? Priority order with rationale.]
```

---

## Change 2: Add @principal-data-architect to Pipeline

**File:** Grist framework pipeline orchestrator (the `/grist:run` skill at `skills/run/SKILL.md`)

**Problem:** @principal-data-architect exists as an agent definition but is never invoked by the pipeline. It should run at every zone transition BEFORE @insight-manager, and its findings should be blocking.

**Changes:**

### 2a. Insert architect review at zone transitions

Update the pipeline orchestration to add @principal-data-architect at these points:

**Raw Zone Pipeline** (after step 12 @staff-engineer, before proceeding to base zone):
> 12.5. @principal-data-architect — Zone transition review (raw → base). Reviews: domain context accuracy, concept normalization readiness, dimensional modeling recommendations, base zone architecture proposal. Output: `governance/reviews/raw-architecture-review.md`. **BLOCKING** — if the architect identifies missing concept normalization or proposes a dimensional model, those findings must be addressed in the base zone specs.

**Base Zone completion** (after @staff-engineer, before consumable zone):
> @principal-data-architect — Zone transition review (base → consumable). Reviews: base zone modeling decisions, normalization coverage, entity resolution completeness, data product design recommendations. **BLOCKING.**

**Consumable Zone completion** (after @staff-engineer, before AI-ready zone):
> @principal-data-architect — Zone transition review (consumable → AI-ready). Reviews: consumable grain correctness, cross-table consistency, golden dataset coverage, AI serving pattern recommendation, eval set requirements. **BLOCKING.**

### 2b. Architect must review concept normalization

Add to the @principal-data-architect agent definition under "What You Review > Domain Discovery":

```markdown
### Concept Normalization Gate (BLOCKING)

At the raw → base transition, verify:

- [ ] `governance/domain-context.md` contains a "Canonical Concept Map" section
- [ ] The concept map has status CONFIRMED or PROPOSED (not NOT ATTEMPTED)
- [ ] If PROPOSED: the map is reasonable for the identified domain (use your domain knowledge to validate)
- [ ] The number of target business concepts is appropriate (typically 15-50 for most domains — too few means over-simplification, too many means no normalization)
- [ ] Collision resolution rules exist for concepts with multiple source codes
- [ ] The base zone spec includes a concept normalization table/step that uses the ConceptNormalizer

If concept normalization is missing or inadequate, issue CHANGES REQUESTED. The base zone MUST include concept normalization — without it, the consumable zone will produce raw codes instead of queryable business metrics, and the AI-ready zone will require users to know internal classification schemes.
```

---

## Change 3: Make @insight-manager Tier 1 Products Mandatory

**File:** `.claude/agents/insight-manager.md`

**Problem:** Insight reports recommend data products in tiers but the pipeline treats them all as advisory. Tier 1 products (high value, high feasibility) should generate specs automatically.

**Changes:**

Add to the insight-manager agent definition:

```markdown
## Product Tier Enforcement

### Tier 1: MANDATORY
Tier 1 data products are **automatically converted to specs**. After the insight report is written:
1. For each Tier 1 product, draft a spec in `docs/specs/` with status PROPOSED
2. Present the list of auto-generated specs to the user via AskUserQuestion for confirmation
3. The user can remove products from the list but must acknowledge doing so (it's logged)
4. All confirmed Tier 1 specs are queued for pipeline execution

### Tier 2: PROPOSED
Tier 2 products are presented to the user via AskUserQuestion:
> "The insight report identified these additional data products. Which should we build?"
> [multiSelect: true, list each Tier 2 product as an option]

### Tier 3: DOCUMENTED
Tier 3 products are documented in the insight report for future consideration. No specs generated.

### Mandatory Tier 1 Products (all domains)
Regardless of domain, the following are ALWAYS Tier 1 if the data supports them:
- **Deduplicated metrics table** — one-row-per-entity-metric-period (if concept normalization produced business terms)
- **Computed ratios** — if the domain has standard ratio definitions (financial ratios, healthcare quality measures, etc.)
- **Period-over-period changes** — YoY/QoQ with growth rates and CAGR if 3+ years of data exist
```

---

## Change 4: Enforce Full Agent Pipeline — No Skipped Steps

**File:** `/grist:run` skill (`skills/run/SKILL.md`)

**Problem:** The pipeline definition lists all agents but the orchestrator (whoever runs `/grist:run`) can skip agents at discretion. Several agents were never invoked: @entity-resolver, @temporal-modeler, @pii-scanner, @adversarial-auditor.

**Changes:**

### 4a. Explicit skip-or-run decision for every agent

Update the pipeline orchestration instructions:

```markdown
## Agent Execution Rules

Every agent in the pipeline MUST be either **executed** or **explicitly skipped with documented justification**. Silent omission is not allowed.

For each agent in the pipeline:
1. **Check if the agent is applicable** to this spec (e.g., @pii-scanner is not needed if the domain context says "no PII expected")
2. If applicable: **execute the agent** and capture its output
3. If not applicable: **document the skip** in `governance/audit-trail/` with the reason
4. The skip reason must reference a specific finding (e.g., "domain-context.md PII section says 'No personal data expected' — skipping @pii-scanner")

### Agents That Are NEVER Skippable
These agents run on every spec, no exceptions:
- @governance-reviewer (pre and post)
- @staff-engineer (final gate)
- @data-analyst (EDA)
- @dq-rule-writer
- @dq-engineer (execution)
- @lineage-tracker
- @doc-generator

### Agents That Are Conditionally Skippable (with justification)
- @entity-resolver — skip only if domain-context.md says entity resolution is trivial (e.g., stable IDs, no name matching needed)
- @pii-scanner — skip only if domain-context.md PII section says no PII expected
- @temporal-modeler — skip only if no temporal data exists
- @adversarial-auditor — skip only if @chaos-monkey found no gaps in 5 cycles
- @cde-tagger — never skip (but may be brief if no new CDEs)

### Agents That Run at Zone Transitions Only
- @principal-data-architect — BLOCKING review at every zone transition
- @insight-manager — at base→consumable and consumable→AI-ready transitions
```

### 4b. Pipeline execution checklist

Add a machine-readable checklist that the orchestrator must complete:

```markdown
## Pipeline Completion Checklist

Before marking a spec COMPLETE, verify every row:

| Agent | Status | Output Location | Skip Reason (if skipped) |
|-------|--------|-----------------|-------------------------|
| @governance-reviewer (pre) | EXECUTED / SKIPPED | governance/reviews/ | |
| @data-steward | EXECUTED / SKIPPED | governance/business-glossary.json | |
| @semantic-modeler (conceptual) | EXECUTED / SKIPPED | governance/models/ | |
| @semantic-modeler (logical) | EXECUTED / SKIPPED | governance/models/ | |
| @data-analyst (EDA) | EXECUTED / SKIPPED | governance/eda/ | |
| @dq-rule-writer | EXECUTED / SKIPPED | governance/dq-rules/ | |
| @semantic-modeler (physical) | EXECUTED / SKIPPED | governance/models/ | |
| @primary-agent (implementation) | EXECUTED / SKIPPED | src/ | |
| @dq-engineer (execution) | EXECUTED / SKIPPED | governance/dq-results/ | |
| @chaos-monkey (5 cycles) | EXECUTED / SKIPPED | governance/chaos-manifests/ | |
| @entity-resolver | EXECUTED / SKIPPED | | |
| @pii-scanner | EXECUTED / SKIPPED | governance/pii-scans/ | |
| @temporal-modeler | EXECUTED / SKIPPED | | |
| @lineage-tracker | EXECUTED / SKIPPED | governance/lineage/ | |
| @cde-tagger | EXECUTED / SKIPPED | governance/data-contracts/*.yaml | |
| @doc-generator | EXECUTED / SKIPPED | governance/data-dictionary.json | |
| @adversarial-auditor | EXECUTED / SKIPPED | | |
| @governance-reviewer (post) | EXECUTED / SKIPPED | governance/reviews/ | |
| @staff-engineer | EXECUTED / SKIPPED | governance/reviews/ | |

This checklist is written to `governance/audit-trail/{spec}-pipeline-checklist.md` and verified by @governance-reviewer in the post-implementation review.
```

---

## Change 5: AI-Ready Zone Must Include Eval Set

**File:** `.claude/agents/insight-manager.md` (consumable→AI-ready insight section) and pipeline rules

**Problem:** sec_edgair produced 200+ mechanically verifiable Q&A eval cases. The Grist version produced zero. An AI agent without an eval set is untestable.

**Changes:**

Add to the Grist framework CLAUDE.md rules section:

```markdown
- AI-Ready zone specs MUST include an evaluation set (`data/ai_ready/eval/{spec}-eval.json`) with at least 50 mechanically verifiable Q&A cases before @staff-engineer review
- Eval cases must span at least 5 categories: point lookup, comparison, ranking, trend, and edge case
- Every eval case must include: question, expected_answer, source_table, source_filters, source_column — so answers can be verified programmatically against consumable tables
- The eval set is a DQ artifact — @dq-engineer validates that all expected answers match pipeline output
```

Add to @insight-manager's consumable→AI-ready transition output:

```markdown
### Mandatory: Evaluation Set Design

At the consumable-to-ai-ready transition, the insight report MUST include:

1. **Question categories** with example questions (at least 5 categories)
2. **Answer verification strategy** — how to mechanically check each answer against consumable tables
3. **Edge cases to test** — company-specific caveats, NULL handling, cross-tag queries
4. **Minimum case counts per category** — e.g., 15 lookup, 10 comparison, 8 ranking, 8 trend, 9 edge case = 50 minimum
```

---

## Change 6: ConceptNormalizer Integration in Base Zone Pipeline

**File:** Grist framework CLAUDE.md (Base zone pipeline section)

**Problem:** The ConceptNormalizer exists at `src/grist/base/concept_normalization/` but nothing in the pipeline tells base zone specs to use it. The concept map from @domain-context needs to flow into actual concept normalization tables.

**Changes:**

Add to the Base & Consumable Zone Pipeline section:

```markdown
### Concept Normalization Step (Base Zone, after @data-steward)

If `governance/domain-context.md` contains a "Canonical Concept Map" section with status CONFIRMED or PROPOSED:

1. @primary-agent generates concept mapping config files in `domain/concept-mappings/` from the domain context's concept map
2. @primary-agent creates a `base.concept_map` table using `ConceptNormalizer` that maps raw classification codes to canonical business concepts
3. The concept map table becomes a dimension that joins to `base.financial_facts` (or equivalent)
4. @dq-rule-writer writes coverage rules: what % of raw codes map to a canonical concept? (P1 rule, threshold from EDA)
5. Unmapped codes are preserved in the base table (no data loss) but flagged

This step is SKIPPABLE only if the @principal-data-architect explicitly approves skipping it at the zone transition review (e.g., the data has no classification codes to normalize).
```

---

## Change 7: Human Approval Documents + AskUserQuestion Approval Flow

**Files:** Framework CLAUDE.md (human approval gate protocol), `.claude/agents/doc-generator.md` (new responsibility)

**Problem:** When `REQUIRE_HUMAN_APPROVAL = True`, human gates exist for business terms, conceptual models, and logical models — but the pipeline just auto-approves artifacts with "approved" status. There's no structured document explaining what the human is approving, no link to review, and no formal approval mechanism via AskUserQuestion.

**Changes:**

### 7a. Human Approval Document Protocol

Add to framework CLAUDE.md under a new "## Human Approval Gates" section:

```markdown
## Human Approval Gates

When `REQUIRE_HUMAN_APPROVAL = True` (the default), certain pipeline steps require explicit human approval before proceeding. Every approval gate follows the same protocol:

### Protocol

1. **The producing agent** (e.g., @data-steward, @semantic-modeler, @dq-rule-writer) creates the artifact as usual
2. **@doc-generator is invoked** to produce a **Human Approval Document** — a plain-English markdown file that explains WHAT is being proposed, WHY, and what the human should look for
3. The approval document is saved to `governance/approvals/{spec}-{artifact-type}-approval.md`
4. The user is given the file path so they can review it (e.g., in Typora or their editor)
5. **AskUserQuestion is used** to collect the approval decision

### Human Approval Document Format

@doc-generator produces each approval document with this structure:

    # Approval Required: {Artifact Type}
    **Spec:** {spec name}
    **Produced by:** @{agent-name}
    **Date:** YYYY-MM-DD
    **Artifact:** {path to the artifact being approved}

    ## What You're Approving
    [Plain-English summary of what this artifact is and what it does. No jargon.
    A business user who has never seen this pipeline should understand this section.]

    ## What Changed (if updating an existing artifact)
    [Diff summary — what was added, modified, or removed]

    ## Key Decisions Made
    [Numbered list of the non-obvious choices the agent made, with rationale.
    These are the things the human should pay attention to.]

    ## What To Look For
    [Specific things the reviewer should verify. Varies by artifact type:]

    ### For Business Terms (@data-steward):
    - Are the definitions accurate for your domain?
    - Are any terms missing that your team uses?
    - Are project-specific terms correctly distinguished from external standards?
    - Do the is_cde and is_pii flags look right?

    ### For Conceptual Model (@semantic-modeler):
    - Do the entity types match how you think about this data?
    - Are the relationships correct?
    - Is anything missing?

    ### For Logical Model (@semantic-modeler):
    - Are the attributes complete?
    - Are nullable/required designations correct?
    - Does the grain make sense?
    - Are derived fields computed correctly?

    ### For DQ Rules (@dq-rule-writer):
    - Are the P0 (blocking) rules appropriate?
    - Are the thresholds realistic?
    - Are there edge cases the rules don't cover?

    ## Proposed Artifact
    [Embed or summarize the full artifact content inline so the reviewer
    doesn't have to open a separate file if they don't want to]

    ## Impact If Rejected
    [What happens if the human says no — which downstream steps are blocked,
    what would need to change]

### Collecting Approval via AskUserQuestion

After @doc-generator produces the approval document, use AskUserQuestion:

**Question:** "Review the {artifact type} approval document at `governance/approvals/{filename}`. What's your decision?"
**Options:**
- "Approved — looks good" → Mark artifact as APPROVED, proceed
- "Approved with notes" → User adds notes via free text, mark APPROVED, log notes in audit trail
- "Changes requested" → User specifies what to change (via free text), return artifact to producing agent for revision, re-run approval flow
- "Need more info — let me review the document first" → Pause pipeline, remind user of the file path, wait for them to come back

### When Multiple Artifacts Need Approval in Sequence

For Base/Consumable greenfield specs, approvals happen in order:
1. Business terms → approval document → AskUserQuestion
2. Conceptual model → approval document → AskUserQuestion
3. Logical model → approval document → AskUserQuestion

Each approval is independent. Rejection of an earlier artifact blocks later ones (e.g., rejecting business terms blocks conceptual model since it references those terms).

### Approval Audit Trail

Every approval decision is logged to `governance/audit-trail/{spec}-approvals.md`:

    | Artifact | Agent | Decision | Decided By | Date | Notes |
    |----------|-------|----------|-----------|------|-------|
    | Business Glossary (5 terms) | @data-steward | APPROVED | human:jeff | 2026-03-18 | "Looks right" |
    | Conceptual Model | @semantic-modeler | CHANGES REQUESTED | human:jeff | 2026-03-18 | "Missing entity X" |
    | Conceptual Model (rev 2) | @semantic-modeler | APPROVED | human:jeff | 2026-03-18 | — |

### When REQUIRE_HUMAN_APPROVAL = False

All approval documents are STILL produced (they're useful documentation regardless). But instead of AskUserQuestion, the pipeline:
1. Writes the approval document
2. Auto-marks the artifact as APPROVED
3. Logs "auto-approved (REQUIRE_HUMAN_APPROVAL=False)" in the audit trail
4. Proceeds without pausing
```

### 7b. Add approval document generation to @doc-generator

Add to `.claude/agents/doc-generator.md` responsibilities:

```markdown
### Human Approval Documents

When invoked for an approval gate, produce a plain-English approval document at `governance/approvals/{spec}-{artifact-type}-approval.md`. The document must:

1. **Be self-contained** — the reviewer should not need to open other files to understand what they're approving (embed the artifact content or a clear summary)
2. **Be written for a non-technical business user** — no raw JSON, no code, no schema definitions without explanation
3. **Highlight decisions, not boilerplate** — the "Key Decisions Made" section is the most important part. What did the agent choose that a human might disagree with?
4. **Include "What To Look For"** — artifact-type-specific review guidance so the human knows where to focus
5. **Be concise** — target 1-2 pages. If the artifact is large (e.g., 50 business terms), summarize with a table showing key items and flag only the ones that need attention

You receive context from the producing agent (the artifact content, the rationale, the spec reference). You transform it into reviewer-friendly prose. You do NOT make approval decisions — you present information clearly so the human can.
```

---

## Change 8: Complete Human Input Capture in Session Logs

**File:** Framework CLAUDE.md (Session Logging section)

**Problem:** Session logs currently capture the initial prompt verbatim and an end-of-session summary. But they do NOT capture:
- User answers to AskUserQuestion prompts (approval decisions, interview responses, concept selections)
- Mid-session user messages, corrections, and redirections
- Free-text notes attached to approvals
- Follow-up questions the user asks back during the domain interview
- Casual instructions that shaped the pipeline (e.g., "handle it however people handle it", "just do it", "skip that")

With Changes 1 and 7 introducing structured AskUserQuestion flows for interviews and approvals, this input is now critical governance data. An auditor or future session needs to know WHY a concept map was approved, WHAT the user said about temporal patterns, and WHEN they deferred a decision.

**Changes:**

### 8a. Add Human Input Log section to session log format

Update the session log template in framework CLAUDE.md. Add a new section between "Prompt Provided" and "Session Goal":

```markdown
## Human Input Log

Every piece of human input during this session is recorded here in chronological order. This includes initial prompts, mid-session messages, AskUserQuestion responses, approval decisions, and any corrections or redirections.

### Format

Each entry is timestamped and typed:

| Timestamp | Type | Context | Input |
|-----------|------|---------|-------|
| HH:MM | prompt | Session start | [exact text] |
| HH:MM | message | [what was happening] | [exact text] |
| HH:MM | ask-response | [question asked] | [option selected + any free-text notes] |
| HH:MM | approval | [artifact being approved] | [decision + notes] |
| HH:MM | correction | [what was corrected] | [exact text] |
| HH:MM | redirect | [what changed direction] | [exact text] |
```

### 8b. Add logging rules to Session Logging section

Add these rules to the existing "## Rules" list under Session Logging:

```markdown
- Every user message is logged in the Human Input Log — no exceptions, no paraphrasing
- AskUserQuestion responses are logged with BOTH the question that was asked AND the option/text selected
- Approval decisions are logged with the artifact path, the decision (APPROVED/CHANGES REQUESTED/etc.), and any notes
- If the user gives a vague or deferring answer ("handle it", "whatever you think", "idk"), log it EXACTLY as said — these are the most important entries because they explain why downstream assumptions were made
- If the user asks a follow-up question back to an agent, log both the question and the agent's response summary
- If the user corrects or redirects mid-pipeline ("wait, not that", "actually do X instead"), log it as type 'correction' or 'redirect'
- The Human Input Log is the AUTHORITATIVE record of human involvement. If an auditor asks "did a human approve this?", the answer is in the session log. If it's not logged, it didn't happen.
```

### 8c. Cross-reference with approval audit trail

Add to the Human Approval Gates section (Change 7):

```markdown
### Cross-Referencing

Every approval decision appears in TWO places:
1. `governance/audit-trail/{spec}-approvals.md` — the governance artifact (permanent, spec-scoped)
2. `docs/sessions/{session}-session.md` → Human Input Log — the chronological record (session-scoped)

Both must agree. If they don't, the session log is authoritative (it captures what actually happened in real-time).
```

### 8d. Domain interview answers are governance artifacts

Add to the @domain-context agent definition (Change 1):

```markdown
### Interview Response Logging

Every user response during the domain interview is:
1. Logged in the session's Human Input Log (with timestamp, question, and exact response)
2. Referenced in `governance/domain-context.md` under the relevant section (e.g., "Source: User interview response — 'we care about Revenue, Net Income, and EPS'")
3. If the response drove a major decision (e.g., concept list, temporal handling), the domain context document must include a **"User Said"** annotation so downstream agents and reviewers can trace the decision to human input

Example in domain-context.md:
> **User Said:** "I don't know the data — just suggest something" (session 2026-03-18-14-30)
> **Agent Action:** Proposed 25 canonical business concepts based on domain knowledge of SEC EDGAR XBRL. Status: PROPOSED (Unconfirmed).
```

---

## Change 9: Programmatic Pipeline Gate Enforcement

**File:** New module `src/grist/infra/pipeline_gate.py` + CLI entry point + integration into `skills/run/SKILL.md`

**Problem:** All pipeline enforcement (BLOCKING gates, MANDATORY steps, skip justification) is instruction-based — markdown text that Claude follows at its discretion. If Claude skips a step or forgets to invoke @principal-data-architect, nothing stops the pipeline. An auditor can't programmatically verify that every step ran. The pipeline needs a state machine with real Python checks that prevent progression when prerequisites aren't met.

**Changes:**

### 9a. Pipeline State File

Every spec gets a machine-readable state file at `governance/pipeline-state/{spec}-pipeline.json`. The state file tracks every pipeline step with its status, timestamps, outputs, and prerequisites.

```json
{
  "spec": "raw-sec-edgar-ingest",
  "zone": "raw",
  "mode": "greenfield",
  "started": "2026-03-18T14:30:00Z",
  "steps": {
    "governance-reviewer-pre": {
      "status": "COMPLETED",
      "agent": "@governance-reviewer",
      "completed_at": "2026-03-18T14:31:00Z",
      "output": "governance/reviews/raw-sec-edgar-ingest-pre-review.md"
    },
    "primary-agent": {
      "status": "COMPLETED",
      "agent": "@primary-agent",
      "completed_at": "2026-03-18T14:45:00Z",
      "output": "src/grist/raw/sec_edgar.py"
    },
    "data-analyst": {
      "status": "NOT_STARTED",
      "agent": "@data-analyst",
      "requires": ["primary-agent"]
    },
    "dq-rule-writer": {
      "status": "NOT_STARTED",
      "agent": "@dq-rule-writer",
      "requires": ["data-analyst"]
    },
    "principal-data-architect": {
      "status": "NOT_STARTED",
      "agent": "@principal-data-architect",
      "requires": ["staff-engineer"],
      "blocking": true
    }
  },
  "skipped_steps": {
    "pii-scanner": {
      "reason": "domain-context.md PII section: 'No personal data expected'",
      "evidence": "governance/domain-context.md",
      "skipped_at": "2026-03-18T15:00:00Z"
    }
  },
  "approvals": {
    "business-terms": {
      "status": "APPROVED",
      "decided_by": "human:jeff",
      "decided_at": "2026-03-18T15:10:00Z",
      "document": "governance/approvals/raw-sec-edgar-ingest-business-terms-approval.md",
      "notes": "Looks right"
    }
  }
}
```

### 9b. PipelineGate Python Module

New module at `src/grist/infra/pipeline_gate.py` with the following API:

```python
from grist.infra.pipeline_gate import PipelineGate

# Initialize or load state for a spec
gate = PipelineGate("raw-sec-edgar-ingest")

# --- Pre-step gate check ---
# Raises GateBlockedError if prerequisites aren't met
gate.check_prerequisites("data-analyst")

# --- Step completion registration ---
# Records that a step completed with its output artifact
gate.complete_step("data-analyst", output="governance/eda/raw-sec-edgar-eda.md")

# --- Skip registration (with mandatory justification) ---
# reason and evidence are required — cannot skip without receipts
gate.skip_step(
    "pii-scanner",
    reason="domain-context.md PII section: 'No personal data expected'",
    evidence="governance/domain-context.md"
)

# --- Approval registration ---
gate.record_approval(
    artifact="business-terms",
    decision="APPROVED",           # APPROVED | CHANGES_REQUESTED
    decided_by="human:jeff",
    notes="Looks right",
    document="governance/approvals/..."
)

# --- Zone transition gate ---
# Verifies ALL specs in the zone have passing pipeline state
# Verifies blocking reviews exist and are APPROVED
gate.check_zone_transition("raw", "base")

# --- Completion validation ---
# Returns (is_valid, list_of_issues)
valid, issues = gate.validate()
```

### 9c. Pipeline Step Definitions

The module includes a pipeline step registry that defines the canonical step sequence per zone, prerequisites, and skip rules:

```python
RAW_ZONE_STEPS = [
    Step("governance-reviewer-pre", agent="@governance-reviewer", skippable=False),
    Step("primary-agent", agent="@primary-agent", requires=["governance-reviewer-pre"], skippable=False),
    Step("data-analyst", agent="@data-analyst", requires=["primary-agent"], skippable=False),
    Step("domain-context", agent="@domain-context", requires=["data-analyst"], skippable=False),
    Step("dq-rule-writer", agent="@dq-rule-writer", requires=["data-analyst"], skippable=False),
    Step("dq-engineer", agent="@dq-engineer", requires=["dq-rule-writer"], skippable=False),
    Step("chaos-monkey", agent="@chaos-monkey", requires=["dq-engineer"], skippable=False),
    Step("entity-resolver", agent="@entity-resolver", requires=["primary-agent"],
         skippable=True, skip_condition="domain-context says entity resolution is trivial"),
    Step("pii-scanner", agent="@pii-scanner", requires=["primary-agent"],
         skippable=True, skip_condition="domain-context PII section says no PII expected"),
    Step("temporal-modeler", agent="@temporal-modeler", requires=["data-analyst"],
         skippable=True, skip_condition="no temporal data exists"),
    Step("lineage-tracker", agent="@lineage-tracker", requires=["primary-agent"], skippable=False),
    Step("cde-tagger", agent="@cde-tagger", requires=["primary-agent"], skippable=False),
    Step("doc-generator", agent="@doc-generator", requires=["cde-tagger"], skippable=False),
    Step("adversarial-auditor", agent="@adversarial-auditor", requires=["chaos-monkey"],
         skippable=True, skip_condition="chaos-monkey found no gaps in 5 cycles"),
    Step("governance-reviewer-post", agent="@governance-reviewer", requires=["doc-generator"], skippable=False),
    Step("staff-engineer", agent="@staff-engineer", requires=["governance-reviewer-post"], skippable=False),
]

ZONE_TRANSITION_STEPS = [
    Step("principal-data-architect", agent="@principal-data-architect",
         requires=["staff-engineer"], blocking=True, skippable=False),
    Step("insight-manager", agent="@insight-manager",
         requires=["principal-data-architect"],
         skippable=True, skip_condition="raw-to-base transition (insight-manager runs at base→consumable and consumable→ai-ready only)"),
]
```

Similar registries for `BASE_ZONE_STEPS` (greenfield and backfill variants) and `CONSUMABLE_ZONE_STEPS`.

### 9d. CLI Entry Point

```bash
# Validate a single spec's pipeline state
python -m grist.infra.pipeline_gate validate raw-sec-edgar-ingest

# Validate all specs
python -m grist.infra.pipeline_gate validate --all

# Check if a zone transition is clear
python -m grist.infra.pipeline_gate check-transition raw base

# Show pipeline status (human-readable)
python -m grist.infra.pipeline_gate status raw-sec-edgar-ingest

# Audit report — machine-readable JSON of all specs, all steps, all approvals
python -m grist.infra.pipeline_gate audit --format json
python -m grist.infra.pipeline_gate audit --format markdown
```

The `audit` command produces a report an auditor can review without running the pipeline — it reads the state files and governance artifacts, cross-references them, and flags any inconsistencies (e.g., approval in state file but no matching entry in audit trail, or output file referenced but not on disk).

### 9e. Integration with Pipeline Orchestrator

Update `skills/run/SKILL.md` to use the gate at three points:

**Before each agent step:**
```
Before invoking any agent, call `PipelineGate.check_prerequisites()` for that step.
If it raises GateBlockedError, STOP and report which prerequisites are missing.
Do NOT proceed by skipping the check — the gate is authoritative.
```

**After each agent step:**
```
After an agent completes, call `PipelineGate.complete_step()` with the output artifact path.
If the agent produced no output (error, empty result), do NOT register completion —
investigate the failure.
```

**Before marking spec COMPLETE:**
```
Run `python -m grist.infra.pipeline_gate validate {spec-name}`.
If validation fails, the spec CANNOT be marked complete.
@staff-engineer's final review MUST include a passing gate validation.
```

### 9f. Tamper Evidence

The state file includes SHA-256 hashes of output artifacts at completion time:

```json
"data-analyst": {
  "status": "COMPLETED",
  "completed_at": "2026-03-18T14:50:00Z",
  "output": "governance/eda/raw-sec-edgar-eda.md",
  "output_hash": "sha256:a3f2b8c..."
}
```

The `audit` command can verify that current file hashes match the recorded hashes, detecting post-completion modifications. Modified files aren't necessarily wrong (legitimate updates happen), but they're flagged for review.

---

## Summary of All Changes

| # | What | Where | Impact |
|---|------|-------|--------|
| 1 | Rewrite @domain-context interview to use AskUserQuestion, support naive users, make concept elicitation blocking | `.claude/agents/domain-context.md` | Closes concept normalization gap |
| 2 | Add @principal-data-architect to pipeline at zone transitions, blocking | `skills/run/SKILL.md` + `.claude/agents/principal-data-architect.md` | Catches architectural gaps before they compound |
| 3 | Make @insight-manager Tier 1 products mandatory specs | `.claude/agents/insight-manager.md` | Ensures ratios, CAGR, comparisons get built |
| 4 | Enforce full agent pipeline with skip-or-run tracking | `skills/run/SKILL.md` + framework CLAUDE.md | No more silently skipped agents |
| 5 | Mandate eval set for AI-Ready zone | Framework CLAUDE.md + `.claude/agents/insight-manager.md` | AI agents become testable |
| 6 | Wire ConceptNormalizer into base zone pipeline | Framework CLAUDE.md | Concept normalization actually happens |
| 7 | Human approval documents via @doc-generator + AskUserQuestion approval flow | Framework CLAUDE.md + `.claude/agents/doc-generator.md` | Approvals are structured, documented, and collected via tool — not silently bypassed |
| 8 | Complete human input capture in session logs | Framework CLAUDE.md (Session Logging) + `.claude/agents/domain-context.md` | Every prompt, answer, approval, correction, and deferral is on the record |
| 9 | Programmatic pipeline gate enforcement | New `src/grist/infra/pipeline_gate.py` + CLI + `skills/run/SKILL.md` | Real Python checks prevent step skipping — auditors get machine-verifiable compliance |

## Expected Outcome

With these changes, a domain-agnostic Grist pipeline should produce ~85-90% of sec_edgair's output quality:
- Concept normalization (3,285 → ~25 terms equivalent)
- Dimensional base zone (concept_map, entity dimension)
- Multiple consumable products (financials, ratios, period-over-period)
- Verification suite (50+ eval cases)
- Full agent coverage (no skipped steps)
- 80-100+ DQ rules (vs current 37)
- Programmatic pipeline compliance verification (auditor-ready)

The remaining 10-15% gap (sector benchmarks needing multi-sector data, amendment analysis tables, 466 unit tests, suspect value detection) would come from additional pipeline iterations or domain-specific extensions.
