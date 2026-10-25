"""Tests for brightsmith.serve — domain MCP server discovery (audit finding H1).

`_load_server` must try, in order: a top-level `mcp:` manifest block, a
`pipeline.zones.mcp` entry (the shape field manifests use), then the
framework base server — logging which was selected and why. Before the fix,
`DomainManifest` had no `mcp` field at all, so the top-level block could never
be parsed and consumers silently got the base server with no domain tools.
"""

from __future__ import annotations

import logging

import pytest
import yaml

import brightsmith.config as cfg
from brightsmith.mcp.base_mcp_server import BaseMCPServer
from brightsmith.serve import _load_server


@pytest.fixture
def restore_config():
    """Snapshot and restore the global config around a test (see test_config.py)."""
    saved = cfg.get_config()
    yield cfg
    cfg.configure(
        project_root=saved.project_root,
        project_name=saved.project_name,
        require_human_approval=saved.require_human_approval,
    )


def _write_manifest(tmp_path, pipeline=None, mcp=None, sources=None):
    manifest: dict = {
        "name": "test-domain",
        "version": "1.0",
        "description": "",
        "sources": sources or [],
    }
    if pipeline is not None:
        manifest["pipeline"] = pipeline
    if mcp is not None:
        manifest["mcp"] = mcp

    domain_dir = tmp_path / "domain"
    domain_dir.mkdir(parents=True, exist_ok=True)
    (domain_dir / "manifest.yaml").write_text(yaml.dump(manifest, sort_keys=False))


_DUMMY_SERVER_SOURCE = (
    "from brightsmith.mcp.base_mcp_server import BaseMCPServer\n\n"
    "class DummyDomainServer(BaseMCPServer):\n"
    "    pass\n"
)


class TestTopLevelMcpBlock:
    """(a) manifest with top-level mcp: block -> named class is loaded."""

    def test_top_level_mcp_block_loads_named_class(self, tmp_path, restore_config):
        (tmp_path / "dummy_server.py").write_text(_DUMMY_SERVER_SOURCE)
        _write_manifest(tmp_path, mcp={"module": "dummy_server", "class": "DummyDomainServer"})
        cfg.configure(project_root=tmp_path)

        import sys

        sys.path.insert(0, str(tmp_path))
        try:
            server = _load_server()
        finally:
            sys.path.remove(str(tmp_path))

        assert type(server).__name__ == "DummyDomainServer"
        assert isinstance(server, BaseMCPServer)


class TestPipelineZonesMcpEntry:
    """(b) manifest with pipeline.zones.mcp entry carrying module+class -> loaded."""

    def test_pipeline_zones_mcp_entry_loads_class(self, tmp_path, restore_config):
        server_file = tmp_path / "src" / "mcp_server" / "fixture_server.py"
        server_file.parent.mkdir(parents=True, exist_ok=True)
        server_file.write_text(_DUMMY_SERVER_SOURCE.replace("DummyDomainServer", "FixtureMCPServer"))

        _write_manifest(
            tmp_path,
            pipeline={
                "zones": {
                    "mcp": [
                        {
                            "name": "fixture_server",
                            "module": "src/mcp_server/fixture_server.py",
                            "class": "FixtureMCPServer",
                        }
                    ]
                }
            },
        )
        cfg.configure(project_root=tmp_path)

        server = _load_server()

        assert type(server).__name__ == "FixtureMCPServer"
        assert isinstance(server, BaseMCPServer)

    def test_pipeline_zones_mcp_single_dict_entry_loads_class(self, tmp_path, restore_config):
        """A single dict (not a list) under pipeline.zones.mcp is also accepted."""
        server_file = tmp_path / "src" / "mcp_server" / "fixture_server.py"
        server_file.parent.mkdir(parents=True, exist_ok=True)
        server_file.write_text(_DUMMY_SERVER_SOURCE.replace("DummyDomainServer", "FixtureMCPServer"))

        _write_manifest(
            tmp_path,
            pipeline={
                "zones": {
                    "mcp": {
                        "module": "src/mcp_server/fixture_server.py",
                        "class": "FixtureMCPServer",
                    }
                }
            },
        )
        cfg.configure(project_root=tmp_path)

        server = _load_server()

        assert type(server).__name__ == "FixtureMCPServer"


class TestNoDomainServerConfigured:
    """(c) neither present -> BaseMCPServer, no exception."""

    def test_no_manifest_at_all_uses_base_server(self, tmp_path, restore_config):
        cfg.configure(project_root=tmp_path)  # no domain/manifest.yaml written
        server = _load_server()
        assert type(server) is BaseMCPServer

    def test_manifest_without_mcp_sections_uses_base_server(self, tmp_path, restore_config):
        _write_manifest(tmp_path, pipeline={"bronze": {"module": "x", "function": "main"}})
        cfg.configure(project_root=tmp_path)
        server = _load_server()
        assert type(server) is BaseMCPServer

    def test_top_level_block_takes_priority_over_pipeline_zones(self, tmp_path, restore_config):
        """When both shapes are present, the top-level mcp: block wins."""
        (tmp_path / "dummy_server.py").write_text(_DUMMY_SERVER_SOURCE)
        server_file = tmp_path / "src" / "mcp_server" / "fixture_server.py"
        server_file.parent.mkdir(parents=True, exist_ok=True)
        server_file.write_text(_DUMMY_SERVER_SOURCE.replace("DummyDomainServer", "FixtureMCPServer"))

        _write_manifest(
            tmp_path,
            mcp={"module": "dummy_server", "class": "DummyDomainServer"},
            pipeline={
                "zones": {
                    "mcp": [{"module": "src/mcp_server/fixture_server.py", "class": "FixtureMCPServer"}]
                }
            },
        )
        cfg.configure(project_root=tmp_path)

        import sys

        sys.path.insert(0, str(tmp_path))
        try:
            server = _load_server()
        finally:
            sys.path.remove(str(tmp_path))

        assert type(server).__name__ == "DummyDomainServer"


class TestGenuineErrorLogging:
    """The fallback warning must only fire on genuine errors (bad class/module),
    never merely because a shape wasn't used."""

    def test_broken_top_level_config_logs_warning_and_falls_back(self, tmp_path, restore_config, caplog):
        _write_manifest(tmp_path, mcp={"module": "no_such_module_xyz", "class": "Nope"})
        cfg.configure(project_root=tmp_path)

        with caplog.at_level(logging.WARNING):
            server = _load_server()

        assert type(server) is BaseMCPServer
        assert any("Could not load domain MCP server" in r.message for r in caplog.records)

    def test_no_mcp_config_at_all_does_not_warn(self, tmp_path, restore_config, caplog):
        _write_manifest(tmp_path, pipeline={"bronze": {"module": "x", "function": "main"}})
        cfg.configure(project_root=tmp_path)

        with caplog.at_level(logging.WARNING):
            _load_server()

        assert not any("Could not load domain MCP server" in r.message for r in caplog.records)
