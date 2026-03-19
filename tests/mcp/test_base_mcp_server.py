"""Tests for BaseMCPServer — framework base class for AI-Ready zone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brightsmith.mcp.base_mcp_server import BaseMCPServer, ResourceDef, ToolDef


class ConcreteServer(BaseMCPServer):
    """Test implementation of BaseMCPServer."""

    def get_tools(self) -> list[ToolDef]:
        return [
            ToolDef(
                name="test_tool",
                description="A test tool",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                handler=self._handle_test,
            ),
        ]

    def get_resources(self) -> list[ResourceDef]:
        return [
            ResourceDef(
                uri="brightsmith://test-resource",
                name="Test Resource",
                description="A test resource",
                handler=lambda: "test content",
            ),
        ]

    def _handle_test(self, input_dict: dict) -> dict:
        return {"result": f"answer for {input_dict.get('query', 'unknown')}"}


@pytest.fixture
def server(tmp_path):
    return ConcreteServer(
        warehouse_path=tmp_path / "warehouse",
        catalog_path=tmp_path / "catalog.db",
        server_name="test-server",
    )


class TestToolRegistration:
    def test_domain_tools_registered(self, server):
        """Domain tools should be included in the tool list."""
        all_tools = server._all_tools()
        names = [t.name for t in all_tools]
        assert "test_tool" in names

    def test_framework_tools_included(self, server):
        """Framework tools should always be present."""
        all_tools = server._all_tools()
        names = [t.name for t in all_tools]
        assert "query_table" in names
        assert "list_tables" in names
        assert "get_data_quality" in names
        assert "get_lineage" in names
        assert "get_contract" in names

    def test_tool_has_correct_schema(self, server):
        """Tools should have name, description, and input_schema."""
        all_tools = server._all_tools()
        for tool in all_tools:
            assert tool.name
            assert tool.description
            assert isinstance(tool.input_schema, dict)
            assert tool.handler is not None


class TestResourceRegistration:
    def test_domain_resources_registered(self, server):
        """Domain resources should be included in the resource list."""
        all_resources = server._all_resources()
        uris = [r.uri for r in all_resources]
        assert "brightsmith://test-resource" in uris

    def test_resource_handler_returns_content(self, server):
        """Resource handlers should return string content."""
        all_resources = server._all_resources()
        for r in all_resources:
            if r.handler:
                content = r.handler()
                assert isinstance(content, str)

    def test_framework_resources_from_grounding_docs(self, tmp_path):
        """Grounding docs should be exposed as resources."""
        docs_dir = tmp_path / "grounding"
        docs_dir.mkdir()
        (docs_dir / "context.md").write_text("Domain context here.")

        server = ConcreteServer(
            warehouse_path=tmp_path / "warehouse",
            catalog_path=tmp_path / "catalog.db",
            grounding_docs_path=docs_dir,
        )
        all_resources = server._all_resources()
        uris = [r.uri for r in all_resources]
        assert "brightsmith://grounding/context" in uris


class TestToolHandlers:
    def test_domain_tool_handler(self, server):
        """Domain tool handler should be callable."""
        all_tools = server._all_tools()
        test_tool = next(t for t in all_tools if t.name == "test_tool")
        result = test_tool.handler({"query": "revenue"})
        assert result["result"] == "answer for revenue"

    def test_list_tables_handler(self, server):
        """list_tables should return a dict with tables key."""
        result = server._handle_list_tables({})
        assert "tables" in result
        assert isinstance(result["tables"], list)

    def test_get_data_quality_handler(self, server):
        """get_data_quality should return a dict."""
        result = server._handle_get_data_quality({"table": "consumable.test"})
        assert "table" in result

    def test_get_contract_handler(self, server):
        """get_contract should return a dict."""
        result = server._handle_get_contract({"table": "consumable.test"})
        assert "table" in result


class TestGovernanceMetadata:
    def test_governance_attached(self, server):
        """attach_governance should add governance section to result."""
        result = server.attach_governance({"data": []}, "consumable.test")
        assert "governance" in result
        assert result["governance"]["table"] == "consumable.test"


class TestGroundingDocs:
    def test_load_grounding_docs_empty(self, server):
        """No grounding docs path should return empty string."""
        assert server.load_grounding_docs() == ""

    def test_load_grounding_docs_with_files(self, tmp_path):
        """Should concatenate all .md files."""
        docs_dir = tmp_path / "grounding"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("First doc.")
        (docs_dir / "b.md").write_text("Second doc.")

        server = ConcreteServer(
            warehouse_path=tmp_path / "w",
            catalog_path=tmp_path / "c.db",
            grounding_docs_path=docs_dir,
        )
        docs = server.load_grounding_docs()
        assert "First doc." in docs
        assert "Second doc." in docs


class TestMCPServerCreation:
    def test_create_mcp_server(self, server):
        """create_mcp_server should return an MCP Server instance."""
        from mcp.server import Server
        mcp_server = server.create_mcp_server()
        assert isinstance(mcp_server, Server)


class TestBaseClass:
    def test_base_class_default_tools(self, tmp_path):
        """Base class without overrides should return only framework tools."""
        base = BaseMCPServer(
            warehouse_path=tmp_path / "w",
            catalog_path=tmp_path / "c.db",
        )
        domain_tools = base.get_tools()
        assert domain_tools == []
        all_tools = base._all_tools()
        assert len(all_tools) == 5  # framework tools only

    def test_base_class_default_resources(self, tmp_path):
        """Base class without overrides should return only framework resources."""
        base = BaseMCPServer(
            warehouse_path=tmp_path / "w",
            catalog_path=tmp_path / "c.db",
        )
        domain_resources = base.get_resources()
        assert domain_resources == []
