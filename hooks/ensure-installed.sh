#!/bin/bash
# SessionStart hook: makes sure the `brightsmith` package is importable by the
# active Python before agents start relying on it.
#
# Replaces the previous inline command (audit finding M8, docs/technical-
# audit-2026-07-02.md): `pip show brightsmith > /dev/null 2>&1 || pip install
# -e ${CLAUDE_PLUGIN_ROOT}` was silent, environment-blind (no uv/venv
# awareness), and swallowed every install failure. This script:
#   1. checks importability directly (not `pip show`, which can be stale/wrong
#      for an editable install or a different Python than the active one)
#   2. prefers `uv pip install -e` when uv is on PATH AND a venv is active,
#      else falls back to the active Python's own pip module
#   3. on ANY failure — including PEP 668 "externally-managed-environment"
#      errors — prints one instructive message with the exact command to run
#      manually, and always exits 0 (a hook must never brick the session)
#   4. the happy path (already installed, or install succeeds) prints at
#      most one short line

set -u

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
  # No plugin root to install from — nothing this hook can do. Stay silent;
  # this should only happen outside the plugin's own runtime.
  exit 0
fi

PYTHON_BIN="python3"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || PYTHON_BIN="python"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "brightsmith: no python3/python found on PATH — install Python 3.11+ and re-run: $PYTHON_BIN -m pip install -e ${PLUGIN_ROOT}"
  exit 0
fi

# Already importable by the active Python — nothing to do. Silent happy path.
if "$PYTHON_BIN" -c "import brightsmith" >/dev/null 2>&1; then
  exit 0
fi

# Prefer uv when it's available AND a venv is active (VIRTUAL_ENV set) — this
# matches the project's own dependency-management convention (CLAUDE.md: "uv
# for dependency management") and avoids uv's own PEP 668 refusal against a
# bare system Python. Otherwise fall back to the active Python's pip module
# (not a bare `pip` on PATH, which could resolve to a different Python than
# the one we just checked importability against).
if command -v uv >/dev/null 2>&1 && [ -n "${VIRTUAL_ENV:-}" ]; then
  INSTALL_CMD="uv pip install -e ${PLUGIN_ROOT}"
else
  INSTALL_CMD="$PYTHON_BIN -m pip install -e ${PLUGIN_ROOT}"
fi

INSTALL_LOG="$(mktemp 2>/dev/null || echo "/tmp/brightsmith-install-$$.log")"

# shellcheck disable=SC2086
if $INSTALL_CMD >"$INSTALL_LOG" 2>&1; then
  echo "brightsmith installed (${INSTALL_CMD})."
  rm -f "$INSTALL_LOG" 2>/dev/null
  exit 0
fi

# Install failed. One clear, instructive message — never a silent swallow —
# naming the exact command to run by hand. Flag PEP 668 specifically since
# that's the single most common failure mode on a system/managed Python.
MSG="brightsmith is not installed and the automatic install failed. Run manually: ${INSTALL_CMD}"
if grep -qi "externally-managed-environment" "$INSTALL_LOG" 2>/dev/null; then
  MSG="${MSG}
(your Python is externally managed — PEP 668. Create a venv first, e.g. \`uv venv && source .venv/bin/activate\`, then re-run the command above.)"
fi
echo "$MSG"
rm -f "$INSTALL_LOG" 2>/dev/null

# A hook must not brick the session — always exit 0 regardless of install
# outcome; the instructive message above is the loud-failure signal instead.
exit 0
