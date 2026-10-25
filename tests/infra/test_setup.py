"""Tests for brightsmith.setup — domain project scaffolding (audit finding H4).

H4a: DQ rule templates must ship inside the installed package (not resolved
via a repo-root escape that only exists in a source checkout) and copying
must fail loudly if they're missing.
H4b: the scaffolded warehouse path must match config.py's default
(data/bronze/iceberg_warehouse), not the stale data/raw/... path.
H4c: covered by module docstring accuracy (not independently testable —
see src/brightsmith/setup.py's module docstring for what init() promises).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brightsmith import setup


class TestScaffoldDirectories:
    def test_warehouse_path_matches_config_default(self, tmp_path):
        """Scaffolded warehouse dir must be data/bronze/... matching config.py's
        default — not the stale data/raw/... path (audit finding H4b)."""
        root = setup.init(project_name="test-proj", output_dir=tmp_path / "proj")

        assert (root / "data" / "bronze" / "iceberg_warehouse").is_dir()
        assert not (root / "data" / "raw" / "iceberg_warehouse").exists()

    def test_catalog_dir_created(self, tmp_path):
        root = setup.init(project_name="test-proj", output_dir=tmp_path / "proj")
        assert (root / "data" / "catalog").is_dir()

    def test_governance_directories_created(self, tmp_path):
        root = setup.init(project_name="test-proj", output_dir=tmp_path / "proj")
        for d in ("dq-rules", "dq-results", "dq-scorecards", "golden-datasets", "data-contracts"):
            assert (root / "governance" / d).is_dir(), f"missing governance/{d}"


class TestDQTemplatesCopy:
    """H4a — templates must ship in the wheel and be copied reliably."""

    def test_templates_copied_and_non_empty(self, tmp_path):
        """DQ rule templates must land in the scaffolded project as non-empty,
        valid JSON — the "mandatory patterns for gold zone" (H4a)."""
        root = setup.init(project_name="test-proj", output_dir=tmp_path / "proj")

        dst = root / "governance" / "dq-rule-templates"
        json_files = [f for f in dst.glob("*.json")]
        assert json_files, "expected at least one DQ rule template JSON file"
        for f in json_files:
            assert f.stat().st_size > 0, f"{f} is empty"
            json.loads(f.read_text())  # must be valid JSON, not a placeholder

    def test_packaged_templates_dir_lives_inside_the_package(self):
        """The template source must be resolvable purely from the installed
        package location (`__file__`), not a repo-root escape — this is what
        makes it reachable from a pip-installed wheel (H4a)."""
        assert setup._DQ_TEMPLATES_DIR.is_relative_to(Path(setup.__file__).parent)

    def test_copy_works_from_arbitrary_cwd(self, tmp_path, monkeypatch):
        """The copy must succeed regardless of the process's cwd or any
        repo-root governance/ directory being reachable from it — proving it
        does not depend on the OLD `_TEMPLATES_DIR.parent.parent.parent`
        repo-root escape, which only worked in a source checkout run from the
        repo root (H4a)."""
        isolated_cwd = tmp_path / "nowhere-near-a-repo-checkout"
        isolated_cwd.mkdir()
        monkeypatch.chdir(isolated_cwd)

        root = setup.init(project_name="test-proj", output_dir=tmp_path / "proj2")

        dst = root / "governance" / "dq-rule-templates"
        assert list(dst.glob("*.json")), "templates must be copied even from an arbitrary cwd"

    def test_missing_packaged_templates_raises_loudly(self, tmp_path, monkeypatch):
        """If the packaged templates directory is missing, init() must fail
        loudly (FileNotFoundError), never silently scaffold an empty dir."""
        monkeypatch.setattr(setup, "_DQ_TEMPLATES_DIR", tmp_path / "nonexistent-templates")

        with pytest.raises(FileNotFoundError, match="DQ rule templates"):
            setup.init(project_name="test-proj", output_dir=tmp_path / "proj3")

    def test_empty_packaged_templates_dir_raises_loudly(self, tmp_path, monkeypatch):
        """An existing-but-empty packaged templates dir is also a loud failure,
        not a silent no-op."""
        empty_dir = tmp_path / "empty-templates"
        empty_dir.mkdir()
        monkeypatch.setattr(setup, "_DQ_TEMPLATES_DIR", empty_dir)

        with pytest.raises(FileNotFoundError, match="no .json files"):
            setup.init(project_name="test-proj", output_dir=tmp_path / "proj4")


class TestPyprojectAndGitignore:
    def test_pyproject_written(self, tmp_path):
        root = setup.init(project_name="test-proj", output_dir=tmp_path / "proj")
        content = (root / "pyproject.toml").read_text()
        assert "test-proj" in content
        # Canonical repo (owner decision 2026-07-02): the org URL, not the
        # jcernauske transfer-redirect — scaffolded projects must not depend
        # on the 301 surviving.
        assert "hyena-studios/brightsmith" in content

    def test_gitignore_written(self, tmp_path):
        root = setup.init(project_name="test-proj", output_dir=tmp_path / "proj")
        assert (root / ".gitignore").exists()


class TestInitReturnsRoot:
    def test_init_returns_created_root(self, tmp_path):
        target = tmp_path / "my-project"
        root = setup.init(project_name="my-project", output_dir=target)
        assert root == target
        assert root.is_dir()

    def test_init_defaults_output_dir_to_cwd_project_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        root = setup.init(project_name="default-dir-proj")
        assert root == tmp_path / "default-dir-proj"
        assert root.is_dir()
