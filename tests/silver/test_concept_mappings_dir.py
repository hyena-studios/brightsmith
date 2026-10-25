"""get_concept_mappings_dir: only a MISSING manifest means discovery mode.

It used to catch ``(FileNotFoundError, Exception)``, so a broken manifest
silently dropped the normalizer into discovery mode and ignored the project's
curated mappings (2026-09-08 audit §7, the live no-swallow bypass).
"""

from __future__ import annotations

import pytest

import brightsmith.config as cfg
from brightsmith.silver.concept_normalization.config import get_concept_mappings_dir


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    return tmp_path


def test_missing_manifest_is_discovery_mode(project):
    assert get_concept_mappings_dir() is None


def test_broken_manifest_raises(project):
    (project / "domain").mkdir()
    (project / "domain" / "manifest.yaml").write_text("name: x\nsources: [ {unclosed\n")
    with pytest.raises(Exception, match=r"(?i)yaml|flow|expected"):
        get_concept_mappings_dir()


def test_manifest_without_hint_returns_none(project):
    (project / "domain").mkdir()
    (project / "domain" / "manifest.yaml").write_text("name: x\nversion: '1'\ndescription: ''\nsources: []\n")
    assert get_concept_mappings_dir() is None
