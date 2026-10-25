"""Shared pytest fixtures for the Brightsmith test suite."""

from __future__ import annotations

from pathlib import Path

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


@pytest.fixture(autouse=True)
def _isolate_warehouse(tmp_path_factory, monkeypatch):
    """Point the warehouse, catalog and governance warehouse at a scratch
    directory for every test.

    Without this, any test that reached a default path (lineage emission,
    run_rules' governance writes, governance reads) wrote into the repo's own
    ``data/`` catalog: the 2026-10-24 governance review counted 497 new files
    from one suite run and ~1,900 accumulated test rows. Tests that configure
    their own paths (monkeypatch, gov_env) still override these.
    """
    import brightsmith.config as cfg

    scratch = tmp_path_factory.mktemp("isolated-data")
    monkeypatch.setattr(cfg, "WAREHOUSE_PATH", scratch / "bronze" / "iceberg_warehouse")
    monkeypatch.setattr(cfg, "GOVERNANCE_WAREHOUSE", scratch / "governance" / "iceberg_warehouse")
    monkeypatch.setattr(cfg, "CATALOG_PATH", scratch / "catalog" / "catalog.db")


_REPO_DATA = Path(__file__).resolve().parents[1] / "data"


def _repo_data_fingerprint() -> dict[str, tuple[int, int]]:
    if not _REPO_DATA.is_dir():
        return {}
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in _REPO_DATA.rglob("*") if p.is_file()}


def pytest_sessionstart(session):
    session.config._repo_data_before = _repo_data_fingerprint()


def pytest_sessionfinish(session, exitstatus):
    """Fail the run if any test wrote into the repo's own data/ directory."""
    before = getattr(session.config, "_repo_data_before", None)
    if before is None:
        return
    after = _repo_data_fingerprint()
    changed = sorted(set(after) - set(before)) + sorted(k for k in before if after.get(k) != before[k])
    if changed:
        print(f"\nTests wrote into the repo's data/ directory ({len(changed)} file(s)), e.g.:")
        for path in changed[:10]:
            print(f"  {path}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
