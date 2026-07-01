"""Tests for brightsmith.config — the WP-2.2 config refactor (audit finding A3).

The central guarantee: ``config.configure()`` takes effect even for modules that
bound config values *before* configure() was called (the import-time-binding bug).
"""

from __future__ import annotations

import warnings

import pytest


@pytest.fixture
def restore_config():
    """Snapshot and restore the global config around a test."""
    import brightsmith.config as cfg

    saved = cfg.get_config()
    yield cfg
    # Restore by reconfiguring to the saved root, then patching the rest back.
    cfg.configure(
        project_root=saved.project_root,
        project_name=saved.project_name,
        require_human_approval=saved.require_human_approval,
    )


def test_configure_takes_effect_after_import(restore_config, tmp_path):
    """The core A3 regression test.

    Import the consumers FIRST, THEN configure(), and prove the new root is used.
    """
    # Import order matters: these modules historically bound paths at import time.
    import brightsmith.infra.dq_runner as dq_runner
    import brightsmith.infra.iceberg_setup as iceberg_setup  # noqa: F401
    import brightsmith.infra.lineage as lineage  # noqa: F401

    cfg = restore_config
    cfg.configure(project_root=tmp_path)
    resolved = tmp_path.resolve()

    # dq_runner resolves rules dir from the *new* root, not the import-time root.
    assert cfg.DQ_RULES_DIR == resolved / "governance" / "dq-rules"
    assert dq_runner._cfg("DQ_RULES_DIR") == resolved / "governance" / "dq-rules"

    # load_rules() actually globs the new directory (empty -> []), proving the
    # call-time read. Create a rule file under the new root and load it.
    rules_dir = resolved / "governance" / "dq-rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "demo.json").write_text(
        '{"spec": "demo", "tables": ["raw.t"], "rules": [{"rule_id": "r1"}]}'
    )
    loaded = dq_runner.load_rules()
    assert [r["rule_id"] for r in loaded] == ["r1"]

    # lineage + iceberg_setup resolve catalog/warehouse under the new root.
    assert lineage.config.CATALOG_PATH == resolved / "data" / "catalog" / "catalog.db"
    assert (
        lineage.config.GOVERNANCE_WAREHOUSE
        == resolved / "data" / "governance" / "iceberg_warehouse"
    )
    assert iceberg_setup.config.WAREHOUSE_PATH == resolved / "data" / "bronze" / "iceberg_warehouse"


def test_get_config_returns_frozen_snapshot(restore_config, tmp_path):
    """get_config() returns an immutable, internally-consistent snapshot."""
    import dataclasses

    cfg = restore_config
    snap = cfg.configure(project_root=tmp_path)
    resolved = tmp_path.resolve()

    assert snap is cfg.get_config()
    assert snap.project_root == resolved
    assert snap.dq_rules_dir == resolved / "governance" / "dq-rules"
    assert snap.catalog_path == resolved / "data" / "catalog" / "catalog.db"

    # Frozen: cannot mutate in place.
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.project_root = tmp_path / "other"


def test_legacy_module_names_resolve_live(restore_config, tmp_path):
    """Legacy UPPER_CASE module names track configure()."""
    cfg = restore_config
    cfg.configure(project_root=tmp_path)
    resolved = tmp_path.resolve()

    assert cfg.PROJECT_ROOT == resolved
    assert cfg.DQ_RESULTS_DIR == resolved / "governance" / "dq-results"
    assert cfg.WAREHOUSE_PATH == resolved / "data" / "bronze" / "iceberg_warehouse"


def test_direct_attribute_assignment_updates_live_config(restore_config, tmp_path):
    """Assigning a legacy name (config.DQ_RULES_DIR = x) updates the live snapshot.

    This is the pattern the governance-db test suite uses, and downstream modules
    must observe it.
    """
    import brightsmith.infra.dq_runner as dq_runner

    cfg = restore_config
    custom = tmp_path / "custom-rules"
    cfg.DQ_RULES_DIR = custom

    assert cfg.get_config().dq_rules_dir == custom
    assert cfg.DQ_RULES_DIR == custom
    assert dq_runner.config.DQ_RULES_DIR == custom


def test_monkeypatch_does_not_leak_stale_value(restore_config, tmp_path, monkeypatch):
    """monkeypatch.setattr on config + undo must not shadow the live config.

    Because managed names never become real __dict__ entries, configure() after a
    monkeypatched-then-undone test still works.
    """
    cfg = restore_config

    with monkeypatch.context() as m:
        m.setattr(cfg, "PROJECT_ROOT", tmp_path / "patched")
        assert cfg.PROJECT_ROOT == tmp_path / "patched"
    # After undo, configure() must still take effect.
    cfg.configure(project_root=tmp_path / "fresh")
    assert cfg.PROJECT_ROOT == (tmp_path / "fresh").resolve()


def test_grist_env_var_emits_deprecation_warning(restore_config, monkeypatch, tmp_path):
    """Reading a GRIST_* var works but warns (decision D6)."""
    import brightsmith.config as cfg

    monkeypatch.delenv("BRIGHTSMITH_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("GRIST_PROJECT_ROOT", str(tmp_path))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        snap = cfg._build_from_env()

    messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("GRIST_PROJECT_ROOT" in m for m in messages)
    # The deprecated variable still functions.
    assert snap.project_root == tmp_path.resolve()


def test_brightsmith_env_var_takes_precedence_no_warning(monkeypatch, tmp_path):
    """BRIGHTSMITH_* wins over GRIST_* and emits no deprecation warning."""
    import brightsmith.config as cfg

    monkeypatch.setenv("BRIGHTSMITH_PROJECT_ROOT", str(tmp_path / "bs"))
    monkeypatch.setenv("GRIST_PROJECT_ROOT", str(tmp_path / "grist"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        snap = cfg._build_from_env()

    assert snap.project_root == (tmp_path / "bs").resolve()
    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]
