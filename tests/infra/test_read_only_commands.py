"""Read-only commands must leave the catalog exactly as they found it.

During the 2026-09-08 audit a read-only ``governance_db status`` created a
``governance.agent_activity`` table in a consumer's committed catalog, with an
absolute path baked in. Every governance read went through a get-or-create
helper. This drives the real CLIs as subprocesses against a populated scratch
project and asserts the catalog database and every warehouse file are
byte-identical afterwards.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType

from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table, reset_catalog_cache

SCHEMA = Schema(NestedField(1, "id", StringType(), required=False))


def _cli(root: Path, module: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "BRIGHTSMITH_PROJECT_ROOT": str(root), "BRIGHTSMITH_PROJECT_NAME": "brightsmith"}
    return subprocess.run(
        [sys.executable, "-m", module, *args], cwd=root, env=env, capture_output=True, text=True, timeout=120
    )


def _fingerprint(root: Path) -> dict[str, str]:
    data = root / "data"
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(data.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def project(tmp_path):
    """A project with one data table and an initialised pipeline-state file."""
    root = tmp_path / "proj"
    root.mkdir()
    catalog = get_catalog(root / "data" / "bronze" / "iceberg_warehouse", root / "data" / "catalog" / "catalog.db")
    append_data(get_or_create_table(catalog, "bronze", "facts", SCHEMA), [{"id": "a"}])
    reset_catalog_cache()
    init = _cli(root, "brightsmith.infra.pipeline_gate", "init", "demo", "--zone", "bronze")
    assert init.returncode == 0, init.stdout + init.stderr
    return root


@pytest.mark.parametrize(
    "command",
    [
        ("brightsmith.infra.governance_db", "status"),
        ("brightsmith.infra.governance_db", "query", "agent_activity"),
        ("brightsmith.infra.pipeline_gate", "validate", "demo"),
        ("brightsmith.infra.pipeline_gate", "status", "demo"),
        ("brightsmith.infra.lineage", "verify", "--spec", "demo"),
        ("brightsmith.infra.lineage", "status"),
    ],
    ids=lambda c: " ".join(c[0].rsplit(".", 1)[-1:] + list(c[1:])),
)
def test_read_only_command_leaves_catalog_untouched(project, command):
    before = _fingerprint(project)
    proc = _cli(project, *command)
    assert "Traceback" not in proc.stderr, proc.stderr
    assert _fingerprint(project) == before, f"{' '.join(command)} changed the catalog:\n{proc.stdout}"


def test_governance_db_module_runs_its_cli(project):
    """`python -m brightsmith.infra.governance_db status` used to exit 0 having
    printed nothing: the façade module had no __main__ guard."""
    proc = _cli(project, "brightsmith.infra.governance_db", "status")
    assert proc.returncode == 0, proc.stderr
    assert "Governance Database Status" in proc.stdout
