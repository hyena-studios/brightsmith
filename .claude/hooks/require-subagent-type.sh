#!/bin/bash
# Rejects Agent tool calls that don't specify subagent_type.
# Without subagent_type, agents run as generic (no colored label, no persona loaded).

INPUT=$(cat)

SUBAGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // empty')

if [ -z "$SUBAGENT_TYPE" ]; then
  jq -n '{
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "deny",
      "permissionDecisionReason": "BLOCKED: Agent tool call is missing subagent_type. You MUST set subagent_type to the agent name (e.g., \"setup\", \"data-analyst\", \"dq-engineer\", \"chaos-monkey\", \"staff-engineer\"). The agent name goes in subagent_type, NOT in description. Fix this and retry."
    }
  }'
  exit 0
fi

exit 0
