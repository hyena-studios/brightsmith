#!/usr/bin/env bash
# Consumer-journey smoke test (W2 — docs/technical-audit-2026-07-02.md,
# Theme 1 / task 2.1: "the framework's own tests still never play the user").
#
# Proves the documented consumer journey (README "Quick Start > Option 2:
# Headless Pipeline") actually works end-to-end from a clean wheel install —
# run entirely from OUTSIDE this repo checkout. That is deliberate: it is the
# only way to catch the class of defect this audit found (H4a/H4b/H4c) where
# a path resolves correctly only inside a source checkout
# (e.g. `_TEMPLATES_DIR.parent.parent.parent`) and silently breaks for anyone
# who `pip install`s the package.
#
# Steps:
#   1. uv build --wheel                          (in this checkout)
#   2. scratch venv OUTSIDE the checkout, install the wheel into it
#   3. python -m brightsmith.setup init           (scaffold a project, outside the checkout)
#   4. verify the scaffold: DQ templates present + non-empty, warehouse dir exists
#   5. drop the fixture domain pack (tests/fixtures/consumer/) into the scaffold
#   6. python -m brightsmith.run --zone bronze    (from the scaffolded project root)
#      -> assert exit 0 and rows land in the Iceberg warehouse
#   7. instantiate BaseMCPServer against that warehouse; query_iceberg("SELECT
#      ...") returns real rows (not the SELECT-1-only test theater the audit
#      called out — C1)
#
# Usage: scripts/consumer_journey_smoke.sh
# Exits non-zero (via `set -e`) on the first failing step, printing what failed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/brightsmith-consumer-journey.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "== Brightsmith consumer-journey smoke test =="
echo "Repo checkout : $REPO_ROOT"
echo "Scratch dir   : $WORK_DIR (OUTSIDE the checkout)"

# ---------------------------------------------------------------------------
# 1. Build the wheel
# ---------------------------------------------------------------------------
echo
echo "[1/7] Building wheel (uv build --wheel)..."
(cd "$REPO_ROOT" && uv build --wheel)
WHEEL="$(ls -t "$REPO_ROOT"/dist/*.whl | head -1)"
echo "  built: $WHEEL"

# ---------------------------------------------------------------------------
# 2. Scratch venv (outside the checkout) + pip-install the wheel
# ---------------------------------------------------------------------------
echo
echo "[2/7] Creating scratch venv and installing the wheel..."
VENV_DIR="$WORK_DIR/venv"
uv venv "$VENV_DIR" >/dev/null
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
uv pip install "$WHEEL" >/dev/null
INSTALLED_AT="$(python -c 'import brightsmith, os; print(os.path.dirname(brightsmith.__file__))')"
echo "  installed brightsmith at: $INSTALLED_AT"
case "$INSTALLED_AT" in
  "$REPO_ROOT"*)
    echo "FAIL: installed package resolves inside the repo checkout ($INSTALLED_AT) — the venv is not isolated." >&2
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------
# 3. Scaffold a project (outside the checkout)
# ---------------------------------------------------------------------------
echo
echo "[3/7] Scaffolding a project (python -m brightsmith.setup init)..."
PROJECT_DIR="$WORK_DIR/scaffolded-project"
(cd "$WORK_DIR" && python -m brightsmith.setup init --name smoke-project --output "$PROJECT_DIR")

# ---------------------------------------------------------------------------
# 4. Verify the scaffold: DQ templates + warehouse path (H4a/H4b regression)
# ---------------------------------------------------------------------------
echo
echo "[4/7] Verifying scaffold contents..."
DQ_TEMPLATES_DIR="$PROJECT_DIR/governance/dq-rule-templates"
if [ ! -d "$DQ_TEMPLATES_DIR" ]; then
  echo "FAIL: DQ templates directory missing: $DQ_TEMPLATES_DIR" >&2
  exit 1
fi
TEMPLATE_FILES=("$DQ_TEMPLATES_DIR"/*.json)
if [ ! -e "${TEMPLATE_FILES[0]}" ]; then
  echo "FAIL: DQ templates directory is empty: $DQ_TEMPLATES_DIR" >&2
  exit 1
fi
for f in "${TEMPLATE_FILES[@]}"; do
  if [ ! -s "$f" ]; then
    echo "FAIL: DQ template is empty: $f" >&2
    exit 1
  fi
done
echo "  DQ templates: ${#TEMPLATE_FILES[@]} non-empty file(s) in $DQ_TEMPLATES_DIR"

WAREHOUSE_DIR="$PROJECT_DIR/data/bronze/iceberg_warehouse"
if [ ! -d "$WAREHOUSE_DIR" ]; then
  echo "FAIL: scaffolded warehouse directory missing: $WAREHOUSE_DIR (config.py default is data/bronze/..., H4b)" >&2
  exit 1
fi
echo "  warehouse dir present: $WAREHOUSE_DIR"

# ---------------------------------------------------------------------------
# 5. Drop the fixture domain pack into the scaffold
# ---------------------------------------------------------------------------
echo
echo "[5/7] Installing fixture domain pack (tests/fixtures/consumer/)..."
FIXTURE_DIR="$REPO_ROOT/tests/fixtures/consumer"
rm -rf "$PROJECT_DIR/domain"
cp -R "$FIXTURE_DIR/domain" "$PROJECT_DIR/domain"
mkdir -p "$PROJECT_DIR/src/raw"
cp "$FIXTURE_DIR/src/raw/stub_ingestor.py" "$PROJECT_DIR/src/raw/stub_ingestor.py"
echo "  fixture installed at: $PROJECT_DIR/domain, $PROJECT_DIR/src/raw/stub_ingestor.py"

# ---------------------------------------------------------------------------
# 6. Run the headless pipeline from the scaffolded project root
# ---------------------------------------------------------------------------
echo
echo "[6/7] Running 'python -m brightsmith.run --zone bronze'..."
set +e
RUN_OUTPUT="$(cd "$PROJECT_DIR" && python -m brightsmith.run --zone bronze --output json 2>&1)"
RUN_EXIT=$?
set -e
echo "$RUN_OUTPUT"
if [ "$RUN_EXIT" -ne 0 ]; then
  echo "FAIL: python -m brightsmith.run --zone bronze exited $RUN_EXIT" >&2
  exit 1
fi

python - "$PROJECT_DIR" <<'PYEOF'
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])

import brightsmith.config as config
config.configure(project_root=project_dir)

from brightsmith.infra.iceberg_setup import get_catalog, read_with_duckdb

catalog = get_catalog(config.WAREHOUSE_PATH, config.CATALOG_PATH)
table = catalog.load_table("bronze.stub_facts")
rows = read_with_duckdb(table)
assert len(rows) > 0, "expected rows in bronze.stub_facts after the pipeline run, found none"
print(f"  verified: {len(rows)} row(s) in bronze.stub_facts via read_with_duckdb")
PYEOF

# ---------------------------------------------------------------------------
# 7. Query the warehouse through BaseMCPServer.query_iceberg (C1 regression)
# ---------------------------------------------------------------------------
echo
echo "[7/7] Querying through BaseMCPServer.query_iceberg..."
python - "$PROJECT_DIR" <<'PYEOF'
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])

import brightsmith.config as config
config.configure(project_root=project_dir)

from brightsmith.mcp.base_mcp_server import BaseMCPServer

server = BaseMCPServer(
    warehouse_path=config.WAREHOUSE_PATH,
    catalog_path=config.CATALOG_PATH,
)
rows = server.query_iceberg("SELECT * FROM bronze_stub_facts")
assert isinstance(rows, list) and rows, f"expected real rows from query_iceberg, got: {rows!r}"
assert "error" not in rows[0], f"query_iceberg returned an error instead of rows: {rows[0]}"
print(f"  verified: query_iceberg returned {len(rows)} real row(s) from a live Iceberg table")

# query_iceberg_simple too — both documented base query paths (M1) must work.
simple_rows = server.query_iceberg_simple("bronze.stub_facts")
assert simple_rows and "error" not in simple_rows[0], f"query_iceberg_simple failed: {simple_rows!r}"
print(f"  verified: query_iceberg_simple returned {len(simple_rows)} real row(s)")
PYEOF

echo
echo "== Consumer-journey smoke test PASSED =="
