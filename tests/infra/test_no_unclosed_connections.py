"""Regression guard for the connection-leak fix (M0.2).

Before remediation, a full test run emitted 542 ``ResourceWarning: unclosed
database`` warnings — SqlCatalog engines and DuckDB connections opened per call
and never closed/disposed. This test exercises a representative governance-read
+ Iceberg-read + MCP-query flow and asserts that no ``unclosed database``
ResourceWarning escapes it, so a future un-closed connection regresses loudly.
"""

from __future__ import annotations

import gc
import warnings

import pytest
from pyiceberg.schema import Schema
from pyiceberg.types import IntegerType, NestedField, StringType

import brightsmith.config as config
from brightsmith.infra.iceberg_setup import (
    append_data,
    get_catalog,
    get_or_create_table,
    read_with_duckdb,
)

_SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="value", field_type=IntegerType(), required=False),
)


@pytest.fixture
def project(tmp_path):
    original = config.get_config()
    config.configure(project_root=tmp_path)
    config.GOVERNANCE_WAREHOUSE = tmp_path / "gov-wh"
    config.WAREHOUSE_PATH = tmp_path / "wh"
    config.CATALOG_PATH = tmp_path / "catalog.db"
    from brightsmith.infra.iceberg_setup import reset_catalog_cache
    reset_catalog_cache()
    try:
        yield tmp_path
    finally:
        config._CONFIG = original
        reset_catalog_cache()


def _unclosed_db_warnings(records):
    return [
        w for w in records
        if issubclass(w.category, ResourceWarning) and "unclosed database" in str(w.message).lower()
    ]


def test_no_unclosed_database_warnings_in_read_flow(project):
    """Governance reads + Iceberg reads + an MCP query leak no SQLite/DuckDB
    connections (no 'unclosed database' ResourceWarning)."""
    catalog = get_catalog(config.WAREHOUSE_PATH, config.CATALOG_PATH)
    table = get_or_create_table(catalog, "base", "widgets", _SCHEMA)
    append_data(table, [{"record_id": "a", "value": 1}, {"record_id": "b", "value": 2}])

    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")

        # 1. Repeated Iceberg reads (formerly leaked a DuckDB connection each).
        for _ in range(5):
            rows = read_with_duckdb(table)
            assert len(rows) == 2

        # 2. Governance read path (formerly rebuilt + leaked a SqlCatalog engine).
        from brightsmith.infra.governance.queries import get_current_specs
        get_current_specs()

        # 3. MCP query surface.
        from brightsmith.mcp.base_mcp_server import BaseMCPServer
        server = BaseMCPServer(
            warehouse_path=config.WAREHOUSE_PATH,
            catalog_path=config.CATALOG_PATH,
        )
        result = server.query_iceberg("SELECT 1 AS x")
        assert result == [{"x": 1}]

        # Force finalizers so any un-closed handle would surface its warning now.
        del server, table, catalog
        gc.collect()

    leaks = _unclosed_db_warnings(records)
    assert not leaks, "unclosed database connections leaked:\n" + "\n".join(str(w.message) for w in leaks)
