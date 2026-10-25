"""Tests for `pipeline:` manifest shape parsing in run.py (audit finding H5.2).

`_load_zone_registry` must support both the flat shape
(`pipeline: {zone: {module, function}}`) and the nested shape
(`pipeline: {zones: {zone: [steps]}}`) that multi-source field manifests
produce naturally — and must fail loudly, never silently leave the registry
empty, when the `pipeline:` block matches neither shape.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import brightsmith.config as cfg
from brightsmith.run import (
    _ZONE_REGISTRY,
    PipelineManifestShapeError,
    ZoneNotRegisteredError,
    _execute_zone_module,
    _load_zone_registry,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "run"


@pytest.fixture
def isolated_registry_and_config():
    """Snapshot/restore both the global config and the zone registry.

    `_load_zone_registry` is a no-op once `_ZONE_REGISTRY` is non-empty, so
    every test needs a clean registry; and it reads `domain/manifest.yaml`
    from the live `config.PROJECT_ROOT`, so every test needs an isolated root.
    """
    saved_config = cfg.get_config()
    saved_registry = dict(_ZONE_REGISTRY)
    _ZONE_REGISTRY.clear()
    yield
    _ZONE_REGISTRY.clear()
    _ZONE_REGISTRY.update(saved_registry)
    cfg.configure(
        project_root=saved_config.project_root,
        project_name=saved_config.project_name,
        require_human_approval=saved_config.require_human_approval,
    )


def _write_manifest(tmp_path: Path, pipeline: dict) -> None:
    domain_dir = tmp_path / "domain"
    domain_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "shape-test",
        "version": "0.1",
        "description": "",
        "sources": [],
        "pipeline": pipeline,
    }
    (domain_dir / "manifest.yaml").write_text(yaml.dump(manifest, sort_keys=False))


class TestFlatShape:
    """Flat pipeline.{zone}.{module,function} shape must keep working."""

    def test_flat_shape_registers_single_step(self, tmp_path, isolated_registry_and_config):
        (tmp_path / "flat_transform.py").write_text(
            "def main():\n    return {'rows_promoted': 5, 'rows_skipped': 1}\n"
        )
        _write_manifest(tmp_path, {"bronze": {"module": "flat_transform", "function": "main"}})
        cfg.configure(project_root=tmp_path)

        sys.path.insert(0, str(tmp_path))
        try:
            _load_zone_registry()
            assert "bronze" in _ZONE_REGISTRY
            assert len(_ZONE_REGISTRY["bronze"]) == 1
            result = _execute_zone_module("bronze")
        finally:
            sys.path.remove(str(tmp_path))

        assert result == {"rows_promoted": 5, "rows_skipped": 1}

    def test_flat_shape_zone_without_module_is_skipped(self, tmp_path, isolated_registry_and_config):
        """A zone entry with no `module` key is a legitimate stub, not an error."""
        _write_manifest(tmp_path, {"bronze": {"status": "draft"}})
        cfg.configure(project_root=tmp_path)

        _load_zone_registry()

        assert "bronze" not in _ZONE_REGISTRY


class TestNestedShape:
    """Nested pipeline.zones.{zone}: [steps] shape (H5.2, field manifest)."""

    def _load_fixture(self, tmp_path):
        """Copy the trimmed futureproof-style manifest fixture into tmp_path
        and drop matching stub transform modules at the file paths it declares."""
        fixture = yaml.safe_load((FIXTURES_DIR / "nested_pipeline_manifest.yaml").read_text())
        domain_dir = tmp_path / "domain"
        domain_dir.mkdir(parents=True, exist_ok=True)
        (domain_dir / "manifest.yaml").write_text(yaml.dump(fixture, sort_keys=False))

        stub = "def transform():\n    return {{'rows_promoted': {p}, 'rows_skipped': {s}}}\n"
        steps = [
            ("src/silver/source_one_transformer.py", 3, 0),
            ("src/silver/source_two_transformer.py", 4, 1),
            ("src/gold/aggregate_transformer.py", 2, 0),
        ]
        for rel_path, promoted, skipped in steps:
            path = tmp_path / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(stub.format(p=promoted, s=skipped))

        # The mcp entry's module is never imported by the zone registry (it's
        # class-based, skipped) — no stub file needed for it to prove that.

        cfg.configure(project_root=tmp_path)

    def test_nested_shape_registers_all_zones_and_steps(self, tmp_path, isolated_registry_and_config):
        self._load_fixture(tmp_path)

        _load_zone_registry()

        assert len(_ZONE_REGISTRY["silver"]) == 2
        assert [s.module for s in _ZONE_REGISTRY["silver"]] == [
            "src/silver/source_one_transformer.py",
            "src/silver/source_two_transformer.py",
        ]
        assert len(_ZONE_REGISTRY["gold"]) == 1
        # mcp is a server declaration (class:), not a callable transform step —
        # must NOT be registered as an executable zone.
        assert "mcp" not in _ZONE_REGISTRY

    def test_file_path_module_executes_and_aggregates(self, tmp_path, isolated_registry_and_config):
        self._load_fixture(tmp_path)
        _load_zone_registry()

        result = _execute_zone_module("silver")

        # Two steps: (3 promoted, 0 skipped) + (4 promoted, 1 skipped).
        assert result == {"rows_promoted": 7, "rows_skipped": 1}

    def test_single_dict_per_zone_also_accepted(self, tmp_path, isolated_registry_and_config):
        """A single mapping (not a list) per zone under pipeline.zones is
        accepted too (matches docs/specs/headless-pipeline-runner.md)."""
        (tmp_path / "single_step.py").write_text(
            "def transform():\n    return {'rows_promoted': 9, 'rows_skipped': 0}\n"
        )
        _write_manifest(
            tmp_path,
            {"zones": {"silver": {"module": "single_step", "function": "transform"}}},
        )
        cfg.configure(project_root=tmp_path)

        sys.path.insert(0, str(tmp_path))
        try:
            _load_zone_registry()
            assert len(_ZONE_REGISTRY["silver"]) == 1
            result = _execute_zone_module("silver")
        finally:
            sys.path.remove(str(tmp_path))

        assert result == {"rows_promoted": 9, "rows_skipped": 0}


class TestUnrecognizedShape:
    """A pipeline: block matching neither shape must fail loudly (H5.2)."""

    def test_unrecognized_top_level_shape_raises(self, tmp_path, isolated_registry_and_config):
        """Values that are neither dicts (flat) nor a 'zones' wrapper (nested)."""
        _write_manifest(tmp_path, {"silver": "not-a-mapping"})
        cfg.configure(project_root=tmp_path)

        with pytest.raises(PipelineManifestShapeError):
            _load_zone_registry()

    def test_pipeline_not_a_mapping_raises(self, tmp_path, isolated_registry_and_config):
        domain_dir = tmp_path / "domain"
        domain_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "name": "shape-test", "version": "0.1", "description": "", "sources": [],
            "pipeline": ["not", "a", "mapping"],
        }
        (domain_dir / "manifest.yaml").write_text(yaml.dump(manifest, sort_keys=False))
        cfg.configure(project_root=tmp_path)

        with pytest.raises(PipelineManifestShapeError):
            _load_zone_registry()

    def test_nested_zone_steps_not_list_or_dict_raises(self, tmp_path, isolated_registry_and_config):
        _write_manifest(tmp_path, {"zones": {"silver": "not-a-list-or-dict"}})
        cfg.configure(project_root=tmp_path)

        with pytest.raises(PipelineManifestShapeError):
            _load_zone_registry()

    def test_no_pipeline_section_leaves_registry_empty_no_error(self, tmp_path, isolated_registry_and_config):
        """Absence of a pipeline: section entirely is legitimate (fresh project)."""
        domain_dir = tmp_path / "domain"
        domain_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"name": "shape-test", "version": "0.1", "description": "", "sources": []}
        (domain_dir / "manifest.yaml").write_text(yaml.dump(manifest, sort_keys=False))
        cfg.configure(project_root=tmp_path)

        _load_zone_registry()  # must not raise

        assert _ZONE_REGISTRY == {}


class TestExecuteZoneModuleUnregistered:
    def test_unregistered_zone_raises_zone_not_registered(self, isolated_registry_and_config):
        with pytest.raises(ZoneNotRegisteredError):
            _execute_zone_module("gold")


# ---------------------------------------------------------------------------
# Full-CLI regression: the audit's H5.2 headline claim was that
# "python -m brightsmith.run cannot drive the field project at all" — this
# drives the real `python -m brightsmith.run` entry point in a fresh
# subprocess against the nested-shape fixture, not just the internal
# functions exercised above.
# ---------------------------------------------------------------------------


def test_headless_run_cli_drives_nested_shape_manifest(tmp_path):
    fixture = yaml.safe_load((FIXTURES_DIR / "nested_pipeline_manifest.yaml").read_text())
    domain_dir = tmp_path / "domain"
    domain_dir.mkdir(parents=True, exist_ok=True)
    (domain_dir / "manifest.yaml").write_text(yaml.dump(fixture, sort_keys=False))

    (tmp_path / "src" / "silver").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "gold").mkdir(parents=True, exist_ok=True)
    stub = "def transform():\n    return {{'rows_promoted': {p}, 'rows_skipped': 0}}\n"
    (tmp_path / "src" / "silver" / "source_one_transformer.py").write_text(stub.format(p=1))
    (tmp_path / "src" / "silver" / "source_two_transformer.py").write_text(stub.format(p=2))
    (tmp_path / "src" / "gold" / "aggregate_transformer.py").write_text(stub.format(p=3))

    env = {
        **os.environ,
        "BRIGHTSMITH_PROJECT_ROOT": str(tmp_path),
        "PYTHONPATH": str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "brightsmith.run", "--zone", "silver", "--output", "json"],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    import json as _json

    result = _json.loads(proc.stdout)
    assert result["zones"]["silver"]["status"] == "SUCCESS"
    assert result["zones"]["silver"]["rows_promoted"] == 3  # 1 + 2 across both steps
