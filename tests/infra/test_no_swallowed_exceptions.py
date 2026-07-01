"""Guard test for WP-2.1 — Success Criterion 5.

Encodes: *zero ``except Exception`` in enforcement paths without a narrowed
type or explicit UNKNOWN/error propagation.*

For a governance product, "swallow the error and report success" is the worst
default — a governance read/enforcement failure must never be indistinguishable
from "no data" or "success". This test parses the in-scope enforcement files
with ``ast`` and FAILS on any bare ``except Exception`` (or bare ``except:``)
handler that does NOT re-raise, unless its enclosing function is on the
explicit, comment-justified allowlist below.

Allowed forms for a broad handler:
  1. it (re-)raises (``raise`` somewhere in the handler body), OR
  2. it is on ``ALLOWLIST`` with a written justification.

Handlers that narrow to a specific exception type (e.g. ``except OSError``)
are not flagged at all — narrowing is the preferred fix.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Repo layout: tests/infra/this_file -> repo root is parents[2].
SRC = Path(__file__).resolve().parents[2] / "src" / "brightsmith"

# Files whose *entire* body is in scope for WP-2.1.
FULL_SCOPE_FILES = [
    SRC / "run.py",
    SRC / "infra" / "dq_runner.py",
    SRC / "infra" / "pipeline_gate.py",
    SRC / "bronze" / "base_ingestor.py",
    # Out of scope for fixing, but explicitly covered so its deliberately
    # fault-tolerant lineage wrapper stays documented on the allowlist.
    SRC / "infra" / "promote.py",
]

# queries.py houses _query_table (WP-2.3 moved it from the former product.py
# monolith). Restrict the check to that function in its canonical location.
PARTIAL_SCOPE_FILES = {
    SRC / "infra" / "governance" / "queries.py": {"_query_table"},
}

# Allowlist: (file_name, enclosing_function_name) -> justification.
# Every entry is a broad handler that is loud-by-design: it either converts the
# failure into an explicit FAILED status / blocking issue / errored-rule record,
# or is a by-design fault-tolerant lineage-emission wrapper (out of WP-2.1 scope).
ALLOWLIST: dict[tuple[str, str], str] = {
    # run.py — relocation preflight: re-raises WarehouseRelocationError; any
    # OTHER table-load error is deliberately deferred to per-zone execution,
    # which fails loudly (it cannot be swallowed into a pass).
    ("run.py", "_preflight_relocation"): "defers non-relocation load errors to loud per-zone execution",
    # run.py — zone-module + DQ execution handlers record zr.status='FAILED' and
    # return a PipelineResult. An arbitrary domain module / DQ infra error is a
    # real failure surfaced loudly, not a swallow (WP-1.2 enforcement gate).
    ("run.py", "run_pipeline"): "records FAILED status + returns; loud failure, not a swallow",
    # run.py — readiness diagnostic: a broken contract layer becomes a blocking
    # readiness issue ('not ready'), never a silent 'ready'.
    ("run.py", "check_headless_ready"): "converts contract-check failure into a blocking readiness issue",
    # dq_runner.py — per-rule executor: any rule SQL/engine error is recorded as
    # passed=False with the error captured. An errored rule is a FAILED rule
    # (decision D3), which the P0 gate blocks on — this is the loud path.
    ("dq_runner.py", "execute_sql_rule"): "errored rule recorded as passed=False with error (decision D3)",
    # pipeline_gate.py — warehouse validation: any catalog/table-read error
    # becomes a blocking validation issue, so the spec cannot be marked COMPLETE.
    ("pipeline_gate.py", "_validate_warehouse_population"): "table/catalog errors become blocking validation issues",
    # promote.py — by-design fault-tolerant lineage emission (explicitly OUT of
    # WP-2.1 scope; lineage is observability, never a data-correctness gate).
    ("promote.py", "promote"): "by-design fault-tolerant lineage emission (out of scope)",
    # base_ingestor.py — by-design fault-tolerant lineage emission (explicitly
    # OUT of WP-2.1 scope).
    ("base_ingestor.py", "_emit_lineage"): "by-design fault-tolerant lineage emission (out of scope)",
}


def _handler_reraises(handler: ast.ExceptHandler) -> bool:
    """True if the handler body contains a ``raise`` (not inside a nested def)."""
    for node in handler.body:
        for sub in ast.walk(node):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Don't descend into nested functions defined in the handler.
                break
            if isinstance(sub, ast.Raise):
                return True
    return False


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """True for ``except Exception`` and bare ``except:`` handlers."""
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def _enclosing_function(path_to_node: list[ast.AST]) -> str:
    for node in reversed(path_to_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


def _collect_broad_handlers(
    tree: ast.AST, restrict_to: set[str] | None
) -> list[tuple[str, int, bool]]:
    """Return (function_name, lineno, reraises) for each broad handler.

    If ``restrict_to`` is given, only handlers inside those functions count.
    """
    results: list[tuple[str, int, bool]] = []

    def visit(node: ast.AST, stack: list[ast.AST]) -> None:
        if isinstance(node, ast.ExceptHandler) and _is_broad(node):
            func = _enclosing_function(stack)
            if restrict_to is None or func in restrict_to:
                results.append((func, node.lineno, _handler_reraises(node)))
        for child in ast.iter_child_nodes(node):
            visit(child, stack + [node])

    visit(tree, [])
    return results


def _scan(path: Path, restrict_to: set[str] | None) -> list[str]:
    """Return a list of violation messages for one file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for func, lineno, reraises in _collect_broad_handlers(tree, restrict_to):
        if reraises:
            continue
        if (path.name, func) in ALLOWLIST:
            continue
        violations.append(
            f"{path.name}:{lineno} — `except Exception` in {func}() neither "
            f"re-raises nor is allowlisted (Success Criterion 5)."
        )
    return violations


def test_no_swallowed_exceptions_in_enforcement_paths():
    violations: list[str] = []
    for path in FULL_SCOPE_FILES:
        assert path.exists(), f"scoped file missing: {path}"
        violations.extend(_scan(path, restrict_to=None))
    for path, funcs in PARTIAL_SCOPE_FILES.items():
        assert path.exists(), f"scoped file missing: {path}"
        violations.extend(_scan(path, restrict_to=funcs))

    assert not violations, "Swallowed exceptions found:\n" + "\n".join(violations)


def test_query_table_raises_governance_read_error():
    """_query_table must propagate read failures as GovernanceReadError, not []."""
    product = SRC / "infra" / "governance" / "queries.py"
    tree = ast.parse(product.read_text(), filename=str(product))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_query_table"
    )
    handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
    assert handlers, "_query_table should still have an except handler"
    assert all(_handler_reraises(h) for h in handlers), (
        "_query_table must raise (GovernanceReadError) on read failure, not swallow"
    )


def test_allowlist_entries_are_real():
    """Every allowlist entry must correspond to an actual broad, non-reraising
    handler — keeps the allowlist from rotting as code changes."""
    seen: set[tuple[str, str]] = set()
    all_files = list(FULL_SCOPE_FILES) + list(PARTIAL_SCOPE_FILES)
    for path in all_files:
        restrict = PARTIAL_SCOPE_FILES.get(path)
        tree = ast.parse(path.read_text(), filename=str(path))
        for func, _lineno, reraises in _collect_broad_handlers(tree, restrict):
            if not reraises:
                seen.add((path.name, func))
    stale = set(ALLOWLIST) - seen
    assert not stale, f"Allowlist entries no longer match any handler: {stale}"
