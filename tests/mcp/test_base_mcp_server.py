"""Tests for BaseMCPServer — framework base class for AI-Ready zone."""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# WP-1.5 — Read-only MCP SQL (S1 / D5)
# ---------------------------------------------------------------------------


class TestQueryIcebergSecurity:
    """query_iceberg must enforce read-only access on the untrusted MCP surface."""

    def test_copy_to_rejected_file_not_created(self, server, tmp_path):
        """COPY TO is rejected by the allowlist; the output file must not appear.

        This is the primary acceptance test from WP-1.5: the allowlist rejects
        'COPY' before DuckDB executes, so /tmp/x.csv (or any path) is never
        written.
        """
        output_file = tmp_path / "exfiltration.csv"
        sql = f"COPY (SELECT 1 AS x) TO '{output_file}'"

        result = server.query_iceberg(sql)

        # Must return a structured error list, not rows
        assert isinstance(result, list), "return type must be list[dict]"
        assert len(result) == 1
        assert "error" in result[0], f"expected an error dict, got: {result[0]}"
        assert "COPY" in result[0]["error"] or "rejected" in result[0]["error"].lower(), (
            f"error should mention COPY or rejection, got: {result[0]['error']}"
        )
        # The file must not have been created
        assert not output_file.exists(), (
            f"COPY TO must not create {output_file} when rejected by the allowlist"
        )

    def test_external_file_read_blocked(self, server):
        """read_csv against a local file is blocked by the external-access lock.

        SELECT * FROM read_csv('…') passes the allowlist (starts with SELECT)
        but is then blocked at the DuckDB level by
        ``SET enable_external_access=false``.  Since S1, query_iceberg no longer
        lets the DuckDB error escape the handler — a blocked/invalid query
        returns a structured ``[{"error": …}]`` (and closes its connection in
        ``finally``) rather than raising.  The critical property is that the file
        contents are NEVER returned; a permission/filesystem error is surfaced
        instead.
        """
        sql = "SELECT * FROM read_csv('/etc/hosts')"
        result = server.query_iceberg(sql)

        assert isinstance(result, list), "return type must be list[dict]"
        assert len(result) == 1
        assert "error" in result[0], f"expected an error dict, got: {result[0]}"
        err = result[0]["error"].lower()
        assert "permission" in err or "file system" in err or "disabled" in err, (
            f"expected a permission/filesystem error, got: {result[0]['error']}"
        )

    def test_plain_select_still_works(self, server):
        """A plain SELECT that references no external files must succeed.

        Verifies that the security additions do not break the legitimate
        read-only query path.
        """
        result = server.query_iceberg("SELECT 1 AS value, 'ok' AS status")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["value"] == 1
        assert result[0]["status"] == "ok"

    def test_invalid_sql_returns_error_not_raises(self, server):
        """A syntactically-valid but semantically-invalid query (unknown column)
        must return a structured error, not raise out of the tool handler (S1).

        On a persistent stdio server, a raising handler would surface as a
        protocol-level crash and leak the DuckDB connection; the contract is that
        execution errors become ``[{"error": …}]`` just like allowlist rejections.
        """
        result = server.query_iceberg("SELECT no_such_column FROM (SELECT 1) t")

        assert isinstance(result, list)
        assert len(result) == 1
        assert "error" in result[0], f"expected an error dict, got: {result[0]}"
        assert "query failed" in result[0]["error"].lower()


class TestValidateReadOnlySql:
    """Unit tests for the _validate_read_only_sql helper."""

    def test_select_allowed(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("SELECT 1") is None

    def test_with_cte_allowed(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("WITH cte AS (SELECT 1) SELECT * FROM cte") is None

    def test_describe_allowed(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("DESCRIBE my_table") is None

    def test_show_allowed(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("SHOW TABLES") is None

    def test_copy_rejected(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        result = _validate_read_only_sql("COPY (SELECT 1) TO '/tmp/x.csv'")
        assert result is not None
        assert "COPY" in result

    def test_insert_rejected(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("INSERT INTO t VALUES (1)") is not None

    def test_create_rejected(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("CREATE TABLE t (x INT)") is not None

    def test_drop_rejected(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("DROP TABLE t") is not None

    def test_install_rejected(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("INSTALL httpfs") is not None

    def test_load_rejected(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("LOAD httpfs") is not None

    def test_attach_rejected(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("ATTACH 'db.duckdb'") is not None

    def test_comment_stripping_select(self):
        """-- and /* */ comments before SELECT must still be allowed."""
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        sql = "-- find revenue\n/* audit: Q4 */ SELECT revenue FROM t"
        assert _validate_read_only_sql(sql) is None

    def test_comment_stripping_copy_rejected(self):
        """/* */ comment before COPY must still be rejected."""
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        sql = "/* sneaky */ COPY (SELECT 1) TO '/tmp/x'"
        assert _validate_read_only_sql(sql) is not None

    def test_leading_paren_with_allowed(self):
        """Leading paren before WITH must still be allowed."""
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("(WITH cte AS (SELECT 1) SELECT * FROM cte)") is None

    def test_multiple_statements_rejected(self):
        """Embedded semicolon must be rejected regardless of first keyword."""
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        sql = "SELECT 1; DROP TABLE t"
        assert _validate_read_only_sql(sql) is not None

    def test_empty_sql_rejected(self):
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("   ") is not None

    def test_trailing_semicolon_select_allowed(self):
        """A single trailing semicolon is fine — SQL convention."""
        from brightsmith.mcp.base_mcp_server import _validate_read_only_sql
        assert _validate_read_only_sql("SELECT 1;") is None
