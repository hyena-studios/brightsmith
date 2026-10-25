"""Rebuild the committed relocated-warehouse fixture.

    uv run python tests/fixtures/relocated_project/build_fixture.py

Writes ``data/`` beside this file: a one-table warehouse (``bronze.facts``, two
rows) whose catalog, metadata JSON and manifest avro all carry the absolute
prefix ``/srv/original-project`` — a project root that exists on no machine.
``relocate --check`` must exit 1 on it, so the CI step proves the detector runs
instead of printing "nothing to do" (2026-09-08 audit §7 P0).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType

import brightsmith.config as cfg
from brightsmith.infra import relocate
from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table, reset_catalog_cache

HERE = Path(__file__).resolve().parent
FOREIGN_ROOT = "/srv/original-project"
SCHEMA = Schema(NestedField(1, "id", StringType(), required=False))


def main() -> None:
    shutil.rmtree(HERE / "data", ignore_errors=True)
    warehouse = HERE / "data" / "bronze" / "iceberg_warehouse"
    catalog_db = HERE / "data" / "catalog" / "catalog.db"
    cfg.configure(project_root=HERE, project_name="brightsmith")
    table = get_or_create_table(get_catalog(warehouse, catalog_db), "bronze", "facts", SCHEMA)
    append_data(table, [{"id": "a"}, {"id": "b"}])
    reset_catalog_cache()

    plan = relocate.Plan.from_config()
    plan.project_root = FOREIGN_ROOT  # APPLY rewrites every layer to this root
    relocate.run(relocate.APPLY, plan)


if __name__ == "__main__":
    main()
