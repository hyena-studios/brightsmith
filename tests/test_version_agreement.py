"""Every place that states the release version must state the same one.

pyproject, the plugin manifest, the marketplace entry and the CHANGELOG were
bumped by hand and nothing checked they agreed (2026-09-08 audit §7 P2).
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import brightsmith

ROOT = Path(__file__).resolve().parents[1]


def _stated_versions() -> dict[str, str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    [bs_entry] = [p for p in market["plugins"] if p["name"] == plugin["name"]]
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", (ROOT / "CHANGELOG.md").read_text(), re.MULTILINE)
    return {
        "pyproject.toml": pyproject["project"]["version"],
        "brightsmith.__version__": brightsmith.__version__,
        ".claude-plugin/plugin.json": plugin["version"],
        ".claude-plugin/marketplace.json metadata": market["metadata"]["version"],
        ".claude-plugin/marketplace.json plugin entry": bs_entry["version"],
        "CHANGELOG.md latest release": released[0],
    }


def test_all_version_statements_agree():
    versions = _stated_versions()
    assert len(set(versions.values())) == 1, versions
