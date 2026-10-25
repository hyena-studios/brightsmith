# Session Logging Protocol

## Purpose
Every Claude Code session is logged for two reasons:
1. Open source transparency — anyone can see exactly how this project was built
2. Continuity — pick up where we left off between sessions

## Session Log Storage

Session logs are **Iceberg-authoritative**, consistent with the rest of governance:

1. **Primary (authoritative):** the governance `sessions` table. Write it via
   `log_session(...)` — the record is queryable with `get_sessions(...)` and shows
   up in the Brightforge UI alongside agent activity.
2. **Secondary (optional, human-readable):** a markdown file under `docs/sessions/`
   using the template below. This is a convenience export for reading in git — the
   table is the record of truth. If the two disagree, the table wins.

```python
from brightsmith.infra.governance_db import log_session

log_session(
    session_id="2026-07-01-1430",              # YYYY-MM-DD-HHMM, stable per session
    title="Short session title",
    summary="1-2 sentence summary of what this session accomplished",
    author="human:jeff",                        # or an agent id
    spec_name="the-spec",                        # optional
    agents_involved=["@staff-engineer"],          # optional
    artifacts=["governance/…", "silver.table"],   # optional
    content="<the full markdown session log body>",  # optional — the export text
)
```

`log_session` is strict by default: if the governance write fails it raises, so a
lost session record is loud, not silent. Pass `strict=False` for best-effort.

## At the START of Every Session

Create a new file: `docs/sessions/YYYY-MM-DD-HH-MM-session.md`

Write the following header immediately:

```markdown
# Session: [YYYY-MM-DD HH:MM]

## Prompt Provided
\`\`\`
[Paste the EXACT prompt you were given, verbatim, no edits]
\`\`\`

## Human Input Log

Every piece of human input during this session is recorded here in chronological order. This includes initial prompts, mid-session messages, AskUserQuestion responses, approval decisions, and any corrections or redirections.

| Timestamp | Type | Context | Input |
|-----------|------|---------|-------|
| HH:MM | prompt | Session start | [exact text] |
| HH:MM | message | [what was happening] | [exact text] |
| HH:MM | ask-response | [question asked] | [option selected + any free-text notes] |
| HH:MM | approval | [artifact being approved] | [decision + notes] |
| HH:MM | correction | [what was corrected] | [exact text] |
| HH:MM | redirect | [what changed direction] | [exact text] |

## Specs Referenced
- [List any spec files referenced in the prompt or during the session]

## Session Goal
[1-2 sentence summary of what this session is trying to accomplish]
```

## At the END of Every Session

Append the following to the same session log file:

```markdown
## Changes Made

### Files Created
| File | Purpose |
|------|---------|
| `path/to/file` | What it does |

### Files Modified
| File | What Changed |
|------|-------------|
| `path/to/file` | Summary of changes |

### Files Deleted
| File | Why |
|------|-----|
| `path/to/file` | Reason |

## Decisions Made
[List any judgment calls, trade-offs, or architectural decisions with rationale.]

## Problems Encountered
[Anything that didn't work the first time, workarounds, surprises in the data.]

## Current State
[What's working now that wasn't before this session]

## Next Steps
[What should the next session pick up on]

## Session Stats
- Duration: ~[X] minutes
- Files created: X
- Files modified: X
- DQ rules added: X (if applicable)
- Governance artifacts produced: [list] (if applicable)
```

## Rules
- The verbatim prompt capture is non-negotiable — copy it exactly as received, including typos
- Be honest in Problems Encountered — the failures are better content than the successes
- Decisions Made should capture the WHY, not just the WHAT
- If a session spans multiple specs, log all of them
- Don't sanitize or polish — raw is better for transparency
- Session logs are NEVER deleted, only appended to (the `sessions` table is append-only; the markdown export is only appended to)
- If you need to reference a previous session, query the `sessions` table (`get_sessions(...)`) or check `docs/sessions/` — the table is authoritative
- Every user message is logged in the Human Input Log — no exceptions, no paraphrasing
- AskUserQuestion responses are logged with BOTH the question that was asked AND the option/text selected
- Approval decisions are logged with the artifact path, the decision (APPROVED/CHANGES REQUESTED/etc.), and any notes
- If the user gives a vague or deferring answer ("handle it", "whatever you think", "idk"), log it EXACTLY as said — these are the most important entries because they explain why downstream assumptions were made
- If the user asks a follow-up question back to an agent, log both the question and the agent's response summary
- If the user corrects or redirects mid-pipeline ("wait, not that", "actually do X instead"), log it as type 'correction' or 'redirect'
- The Human Input Log is the AUTHORITATIVE record of human involvement. If an auditor asks "did a human approve this?", the answer is in the session log. If it's not logged, it didn't happen.
