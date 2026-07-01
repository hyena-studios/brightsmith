"""Warehouse relocatability (WP-1.6 / audit A6).

Builds a tiny Iceberg warehouse under tmp root A using the config-derived layout,
moves it to root B with ``shutil.move``, then proves:

  * reading the moved warehouse raises ``WarehouseRelocationError`` naming the
    repair command (never silently-empty results);
  * ``relocate --check`` (CLI) exits non-zero;
  * ``relocate --apply`` rewrites all four metadata layers and reads return the
    ORIGINAL rows;
  * ``relocate --apply`` twice is a no-op (idempotent);
  * ``relocate --relative`` round-trips with CWD pinned to root B;
  * the avro codec is preserved across the rewrite;
  * a fresh / in-place warehouse does NOT raise (detection is not over-eager).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import fastavro
import pytest
from pyiceberg.schema import Schema
from pyiceberg.types import DoubleType, NestedField, StringType

import brightsmith.config as cfg
from brightsmith.infra import relocate
from brightsmith.infra.iceberg_setup import (
    WarehouseRelocationError,
    append_data,
    get_catalog,
    get_or_create_table,
    read_with_duckdb,
)

SCHEMA = Schema(
    NestedField(1, "company_id", StringType(), required=False),
    NestedField(2, "metric", StringType(), required=False),
    NestedField(3, "value", DoubleType(), required=False),
)

BATCH_1 = [
    {"company_id": "COMP_A", "metric": "revenue", "value": 1_000_000.0},
    {"company_id": "COMP_B", "metric": "assets", "value": 5_000_000.0},
]
BATCH_2 = [
    {"company_id": "COMP_C", "metric": "revenue", "value": 750_000.0},
]
ORIGINAL_IDS = sorted(r["company_id"] for r in BATCH_1 + BATCH_2)


def _point_config(monkeypatch, root: Path) -> None:
    """Repoint all warehouse-related config globals at ``root`` (config layout)."""
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    monkeypatch.setattr(cfg, "WAREHOUSE_PATH", root / "data" / "bronze" / "iceberg_warehouse")
    monkeypatch.setattr(cfg, "CATALOG_PATH", root / "data" / "catalog" / "catalog.db")
    monkeypatch.setattr(cfg, "GOVERNANCE_WAREHOUSE", root / "data" / "governance" / "iceberg_warehouse")


def _seed(root: Path) -> None:
    """Create a populated bronze.facts table under ``root`` with two snapshots."""
    warehouse = root / "data" / "bronze" / "iceberg_warehouse"
    catalog_db = root / "data" / "catalog" / "catalog.db"
    catalog = get_catalog(warehouse, catalog_db)
    table = get_or_create_table(catalog, "bronze", "facts", SCHEMA)
    append_data(table, BATCH_1)
    append_data(table, BATCH_2)


@pytest.fixture
def moved_warehouse(tmp_path, monkeypatch):
    """Seed at root A, move to root B, leave config pointed at B."""
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"

    _point_config(monkeypatch, root_a)
    _seed(root_a)

    # Sanity: in-place read works before the move.
    cat = get_catalog(cfg.WAREHOUSE_PATH, cfg.CATALOG_PATH)
    assert len(read_with_duckdb(cat.load_table("bronze.facts"))) == 3

    shutil.move(str(root_a), str(root_b))
    _point_config(monkeypatch, root_b)

    return {"root_a": root_a, "root_b": root_b}


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "BRIGHTSMITH_PROJECT_ROOT": str(root)}
    return subprocess.run(
        [sys.executable, "-m", "brightsmith.infra.relocate", *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
    )


# --- 1. moved warehouse raises loudly, naming the command ---


def test_moved_warehouse_read_raises_with_command(moved_warehouse):
    # get_catalog itself is fine; loading the (stale) table is the read that must
    # fail loudly rather than scan to silently-empty results.
    catalog = get_catalog(cfg.WAREHOUSE_PATH, cfg.CATALOG_PATH)
    with pytest.raises(WarehouseRelocationError) as exc:
        catalog.load_table("bronze.facts")
    msg = str(exc.value)
    assert "relocate --apply" in msg
    assert str(moved_warehouse["root_a"]) in msg  # names the stale baked prefix


# --- 2. relocate --check (CLI) exits non-zero on a moved warehouse ---


def test_check_cli_exits_nonzero(moved_warehouse):
    proc = _cli(moved_warehouse["root_b"], "--check")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "CHECK FAILED" in proc.stdout


# --- 3. --apply repairs all four layers; reads return the ORIGINAL rows ---


def test_apply_repairs_and_reads_original_rows(moved_warehouse):
    result = relocate.run(relocate.APPLY)
    # All four layers touched: sqlite rows + metadata.json + avro.
    assert result.sqlite_changed > 0
    assert result.json_changed > 0
    assert result.avro_changed > 0

    cat = get_catalog(cfg.WAREHOUSE_PATH, cfg.CATALOG_PATH)
    rows = read_with_duckdb(cat.load_table("bronze.facts"))
    assert sorted(r["company_id"] for r in rows) == ORIGINAL_IDS


def test_apply_via_cli_then_read(moved_warehouse):
    proc = _cli(moved_warehouse["root_b"], "--apply")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Read back in-process (config already points at B).
    cat = get_catalog(cfg.WAREHOUSE_PATH, cfg.CATALOG_PATH)
    rows = read_with_duckdb(cat.load_table("bronze.facts"))
    assert sorted(r["company_id"] for r in rows) == ORIGINAL_IDS
    # And --check is now clean.
    assert _cli(moved_warehouse["root_b"], "--check").returncode == 0


# --- 4. --apply twice is idempotent ---


def test_apply_twice_is_noop(moved_warehouse):
    first = relocate.run(relocate.APPLY)
    assert first.total_changed > 0
    second = relocate.run(relocate.APPLY)
    assert second.total_changed == 0


# --- 5. --relative round-trips with CWD pinned to root B ---


def test_relative_mode_roundtrips(moved_warehouse, monkeypatch):
    result = relocate.run(relocate.RELATIVE)
    assert result.total_changed > 0

    # Relative paths resolve against CWD — pin it to the new root.
    monkeypatch.chdir(moved_warehouse["root_b"])
    cat = get_catalog(cfg.WAREHOUSE_PATH, cfg.CATALOG_PATH)
    rows = read_with_duckdb(cat.load_table("bronze.facts"))
    assert sorted(r["company_id"] for r in rows) == ORIGINAL_IDS

    # No foreign-absolute paths remain.
    assert relocate.run(relocate.CHECK).total_changed == 0


# --- 6. avro codec is preserved across the rewrite ---


def test_avro_codec_preserved(moved_warehouse):
    warehouse = cfg.WAREHOUSE_PATH
    avro_files = sorted(Path(warehouse).rglob("*.avro"))
    assert avro_files, "expected manifest avro files in the warehouse"
    before = {}
    for ap in avro_files:
        with open(ap, "rb") as f:
            before[ap] = fastavro.reader(f).codec

    relocate.run(relocate.APPLY)

    for ap in avro_files:
        with open(ap, "rb") as f:
            assert fastavro.reader(f).codec == before[ap]


# --- 7. safety: fresh / in-place warehouse must NOT raise ---


def test_fresh_warehouse_does_not_raise(tmp_path, monkeypatch):
    root = tmp_path / "fresh"
    _point_config(monkeypatch, root)
    _seed(root)
    # In-place read: catalog rows point at files that exist → no raise.
    cat = get_catalog(cfg.WAREHOUSE_PATH, cfg.CATALOG_PATH)
    assert len(read_with_duckdb(cat.load_table("bronze.facts"))) == 3
    # check is clean and apply is a no-op for an in-place warehouse.
    assert relocate.run(relocate.CHECK).total_changed == 0
    assert relocate.run(relocate.APPLY).total_changed == 0


def test_empty_or_absent_warehouse_does_not_raise(tmp_path, monkeypatch):
    root = tmp_path / "empty"
    _point_config(monkeypatch, root)
    # No data/ dir at all: get_catalog creates an empty catalog without raising.
    cat = get_catalog(cfg.WAREHOUSE_PATH, cfg.CATALOG_PATH)
    assert cat is not None
    # And the CLI no-ops cleanly.
    proc = _cli(root, "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
