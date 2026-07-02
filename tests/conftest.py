"""Shared pytest fixtures for the Brightsmith test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    """Drop the process-wide SqlCatalog cache around every test.

    ``iceberg_setup.get_catalog`` caches catalogs per resolved
    (warehouse, catalog, project_name) triple to avoid leaking a SQLAlchemy
    engine per call. Tests reconfigure ``brightsmith.config`` (and create fresh
    tmp warehouses) between cases, so we clear the cache before and after each
    test to guarantee no catalog bound to a previous configuration is reused.
    """
    from brightsmith.infra.iceberg_setup import reset_catalog_cache

    reset_catalog_cache()
    yield
    reset_catalog_cache()
