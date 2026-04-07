# Human Approval Gates

When `REQUIRE_HUMAN_APPROVAL = True` (the default), certain pipeline steps require explicit human approval before proceeding. Every approval gate follows the same protocol.

## Protocol

1. **The producing agent** (e.g., @data-steward, @semantic-modeler, @dq-rule-writer) creates the artifact as usual
2. **@doc-generator is invoked** to produce a **Human Approval Document** — a plain-English markdown file that explains WHAT is being proposed, WHY, and what the human should look for
3. The approval document is saved to `governance/approvals/{spec}-{artifact-type}-approval.md`
4. The user is given the file path so they can review it (e.g., in Typora or their editor)
5. **AskUserQuestion is used** to collect the approval decision

## Collecting Approval via AskUserQuestion

After @doc-generator produces the approval document, use AskUserQuestion:

**Question:** "Review the {artifact type} approval document at `governance/approvals/{filename}`. What's your decision?"
**Options:**
- "Approved — looks good" → Mark artifact as APPROVED, proceed
- "Approved with notes" → User adds notes via free text, mark APPROVED, log notes in audit trail
- "Changes requested" → User specifies what to change (via free text), return artifact to producing agent for revision, re-run approval flow
- "Need more info — let me review the document first" → Pause pipeline, remind user of the file path, wait for them to come back

## When Multiple Artifacts Need Approval in Sequence

For Silver/Gold greenfield specs, approvals happen in order:
1. Business terms → approval document → AskUserQuestion
2. Conceptual model → approval document → AskUserQuestion
3. Logical model → approval document → AskUserQuestion

Each approval is independent. Rejection of an earlier artifact blocks later ones (e.g., rejecting business terms blocks conceptual model since it references those terms).

## Recording Approvals

Every approval decision is recorded in TWO places:

1. **Pipeline gate state file** via:
   ```bash
   python3 -m brightsmith.infra.pipeline_gate approve {spec} {artifact} --decision APPROVED --by human:{name} --notes "..." --document governance/approvals/{filename}
   ```

2. **Audit trail** at `governance/audit-trail/{spec}-approvals.md`:

| Artifact | Agent | Decision | Decided By | Date | Notes |
|----------|-------|----------|-----------|------|-------|
| Business Glossary (5 terms) | @data-steward | APPROVED | human:jeff | 2026-03-18 | "Looks right" |

## When REQUIRE_HUMAN_APPROVAL = False

All approval documents are STILL produced (they're useful documentation regardless). But instead of AskUserQuestion, the pipeline:
1. Writes the approval document
2. Auto-marks the artifact as APPROVED via pipeline gate
3. Logs "auto-approved (REQUIRE_HUMAN_APPROVAL=False)" in the audit trail
4. Proceeds without pausing

## Cross-Referencing

Every approval decision appears in THREE places:
1. `governance/pipeline-state/{spec}-pipeline.json` — the programmatic state (machine-readable)
2. `governance/audit-trail/{spec}-approvals.md` — the governance artifact (permanent, spec-scoped)
3. `docs/sessions/{session}-session.md` → Human Input Log — the chronological record (session-scoped)

All three must agree. If they don't, the session log is authoritative (it captures what actually happened in real-time).
