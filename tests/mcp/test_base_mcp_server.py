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
        assert "get_semantic_model" in names

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

    def test_osi_semantic_model_resource_served_when_present(self, server, tmp_path, monkeypatch):
        """The OSI semantic model is exposed as a resource iff the file exists
        (docs/specs/osi-semantic-model-export.md WP-2)."""
        from brightsmith import config

        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        osi_path = tmp_path / "governance" / "semantic-model.osi.yaml"
        osi_path.parent.mkdir(parents=True)
        osi_path.write_text("version: '1.0'\nsemantic_model:\n  name: test\n  datasets: []\n")

        all_resources = server._all_resources()
        osi_res = next(r for r in all_resources if r.uri == "brightsmith://semantic-model")
        assert osi_res.mime_type == "application/yaml"
        assert "semantic_model" in osi_res.handler()

    def test_osi_semantic_model_resource_absent_without_file(self, server, tmp_path, monkeypatch):
        from brightsmith import config

        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        uris = [r.uri for r in server._all_resources()]
        assert "brightsmith://semantic-model" not in uris

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


class TestSemanticModelTool:
    """get_semantic_model — model-invokable counterpart of the
    brightsmith://semantic-model resource (osi-semantic-model-export follow-up)."""

    OSI_DOC = (
        "version: '1.0'\n"
        "semantic_model:\n"
        "  name: testproj\n"
        "  ai_context:\n"
        "    instructions: 'Very long domain context that scoped calls must omit.'\n"
        "  datasets:\n"
        "    - name: companies\n"
        "      source: testproj.gold.companies\n"
        "      primary_key: [company_id]\n"
        "      fields:\n"
        "        - name: company_id\n"
        "    - name: company_metrics\n"
        "      source: testproj.gold.company_metrics\n"
        "      primary_key: [company_id, fiscal_year]\n"
        "      fields:\n"
        "        - name: company_id\n"
        "        - name: revenue\n"
        "    - name: unrelated\n"
        "      source: testproj.gold.unrelated\n"
        "      fields: []\n"
        "  relationships:\n"
        "    - name: company_metrics__companies\n"
        "      from: company_metrics\n"
        "      to: companies\n"
        "      from_columns: [company_id]\n"
        "      to_columns: [company_id]\n"
        "  metrics:\n"
        "    - name: total_revenue\n"
        "      expression:\n"
        "        dialects:\n"
        "          - dialect: ANSI_SQL\n"
        "            expression: SUM(revenue)\n"
    )

    @pytest.fixture
    def osi_project(self, tmp_path, monkeypatch):
        from brightsmith import config

        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        path = tmp_path / "governance" / "semantic-model.osi.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(self.OSI_DOC)
        return path

    def test_unscoped_returns_full_model(self, server, osi_project):
        result = server._handle_get_semantic_model({})
        assert result["version"] == "1.0"
        model = result["semantic_model"]
        assert {d["name"] for d in model["datasets"]} == {"companies", "company_metrics", "unrelated"}
        assert "instructions" in model["ai_context"]

    def test_scoped_returns_dataset_relationships_metrics(self, server, osi_project):
        result = server._handle_get_semantic_model({"table": "gold.company_metrics"})
        assert result["dataset"]["name"] == "company_metrics"
        assert result["dataset"]["primary_key"] == ["company_id", "fiscal_year"]
        assert [r["name"] for r in result["relationships"]] == ["company_metrics__companies"]
        assert [m["name"] for m in result["metrics"]] == ["total_revenue"]
        # The token-heavy model-level ai_context is omitted from scoped calls
        assert "semantic_model" not in result and "ai_context" not in result

    def test_scoped_accepts_bare_dataset_name(self, server, osi_project):
        result = server._handle_get_semantic_model({"table": "companies"})
        assert result["dataset"]["name"] == "companies"
        assert [r["name"] for r in result["relationships"]] == ["company_metrics__companies"]

    def test_scoped_dataset_without_relationships(self, server, osi_project):
        result = server._handle_get_semantic_model({"table": "unrelated"})
        assert result["dataset"]["name"] == "unrelated"
        assert result["relationships"] == []

    def test_unknown_table_lists_available_datasets(self, server, osi_project):
        result = server._handle_get_semantic_model({"table": "gold.nope"})
        assert "No dataset named" in result["error"]
        assert result["available_datasets"] == ["companies", "company_metrics", "unrelated"]

    def test_missing_file_returns_generate_hint(self, server, tmp_path, monkeypatch):
        from brightsmith import config

        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        result = server._handle_get_semantic_model({})
        assert result["semantic_model"] is None
        assert "brightsmith.infra.osi generate" in result["message"]

    def test_malformed_yaml_returns_structured_error(self, server, tmp_path, monkeypatch):
        from brightsmith import config

        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        path = tmp_path / "governance" / "semantic-model.osi.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("{not: valid: yaml: [")
        result = server._handle_get_semantic_model({})
        assert "unreadable" in result["error"]

    def test_wrong_shape_returns_structured_error(self, server, tmp_path, monkeypatch):
        from brightsmith import config

        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        path = tmp_path / "governance" / "semantic-model.osi.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("- just\n- a\n- list\n")
        result = server._handle_get_semantic_model({})
        assert "unexpected shape" in result["error"]


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
        assert len(all_tools) == 6  # framework tools only (incl. get_semantic_model)

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


class TestQueryIcebergRealTable:
    """C1 — query_iceberg must be able to read real Iceberg tables while
    staying read-only (docs/technical-audit-2026-07-02.md, sketch A).
    """

    @staticmethod
    def _make_table(warehouse_path, catalog_path):
        from pyiceberg.schema import Schema
        from pyiceberg.types import IntegerType, NestedField, StringType

        from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table

        catalog = get_catalog(warehouse_path, catalog_path)
        schema = Schema(
            NestedField(1, "id", IntegerType(), required=True),
            NestedField(2, "name", StringType(), required=True),
        )
        table = get_or_create_table(catalog, "gold", "verify_tbl", schema)
        append_data(table, [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}], strict=False)
        return table

    def test_query_iceberg_reads_real_table(self, tmp_path):
        """A real Iceberg table created via the project's own helpers must be
        queryable through query_iceberg — this is the C1 regression: before
        the fix, the view existed but every scan failed with
        'Table … does not exist' because external access was disabled before
        the (lazy) iceberg_scan view was ever read.
        """
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path)

        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)
        result = server.query_iceberg("SELECT * FROM gold_verify_tbl ORDER BY id")

        assert isinstance(result, list)
        assert len(result) == 2, f"expected 2 real rows, got: {result}"
        assert "error" not in result[0]
        assert result[0] == {"id": 1, "name": "alice"}
        assert result[1] == {"id": 2, "name": "bob"}

    def test_read_csv_still_blocked_after_real_table_query(self, tmp_path):
        """read_csv against an arbitrary local file must still be blocked
        after a successful real-table query on the same server instance —
        the allowed_directories scoping must not leak into a broader grant.
        """
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path)

        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)

        real_result = server.query_iceberg("SELECT * FROM gold_verify_tbl ORDER BY id")
        assert len(real_result) == 2
        assert "error" not in real_result[0]

        blocked_result = server.query_iceberg("SELECT * FROM read_csv('/etc/hosts')")
        assert isinstance(blocked_result, list)
        assert len(blocked_result) == 1
        assert "error" in blocked_result[0]
        err = blocked_result[0]["error"].lower()
        assert "permission" in err or "file system" in err or "disabled" in err, (
            f"expected a permission/filesystem error, got: {blocked_result[0]['error']}"
        )

    def test_set_enable_external_access_rejected(self, tmp_path):
        """A direct attempt to re-enable external access must be rejected —
        SET is not an allowed leading keyword on the untrusted surface
        (Layer 1), and lock_configuration=true backs this up at the DuckDB
        level (Layer 2) even if the allowlist were ever bypassed.
        """
        server = ConcreteServer(
            warehouse_path=tmp_path / "warehouse",
            catalog_path=tmp_path / "catalog.db",
        )
        result = server.query_iceberg("SET enable_external_access=true")

        assert isinstance(result, list)
        assert len(result) == 1
        assert "error" in result[0]


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


# ---------------------------------------------------------------------------
# W3c/d — predicate pushdown (query_iceberg_simple) and cached view
# registrations (query_iceberg), docs/technical-audit-2026-07-02.md M1/M6.
# ---------------------------------------------------------------------------


class TestQueryIcebergSimplePushdown:
    """query_iceberg_simple must push filters/columns/limit into SQL against
    a cached iceberg_scan view, never materialize the whole table in Python.
    """

    @staticmethod
    def _make_table(warehouse_path, catalog_path, rows):
        from pyiceberg.schema import Schema
        from pyiceberg.types import IntegerType, NestedField, StringType

        from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table

        catalog = get_catalog(warehouse_path, catalog_path)
        schema = Schema(
            NestedField(1, "id", IntegerType(), required=True),
            NestedField(2, "name", StringType(), required=True),
        )
        table = get_or_create_table(catalog, "gold", "simple_tbl", schema)
        append_data(table, rows, strict=False)
        return table

    def test_non_matching_filter_returns_empty_without_full_materialization(self, tmp_path, monkeypatch):
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, [
            {"id": 1, "name": "alice"}, {"id": 2, "name": "bob"},
        ])

        # If query_iceberg_simple ever fell back to reading the whole table
        # into Python (the pre-fix behavior), this spy would be hit.
        import brightsmith.infra.iceberg_setup as iceberg_mod
        calls = []
        monkeypatch.setattr(
            iceberg_mod, "read_with_duckdb",
            lambda *a, **k: calls.append((a, k)) or (_ for _ in ()).throw(AssertionError("full materialization used")),
        )

        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)
        result = server.query_iceberg_simple("gold.simple_tbl", filters={"name": "nobody"})

        assert result == []
        assert calls == []

    def test_filter_pushed_down_returns_matching_row(self, tmp_path):
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, [
            {"id": 1, "name": "alice"}, {"id": 2, "name": "bob"},
        ])
        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)

        result = server.query_iceberg_simple("gold.simple_tbl", filters={"name": "bob"})
        assert result == [{"id": 2, "name": "bob"}]

    def test_columns_projects_only_requested_fields(self, tmp_path):
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, [{"id": 1, "name": "alice"}])
        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)

        result = server.query_iceberg_simple("gold.simple_tbl", columns=["name"])
        assert result == [{"name": "alice"}]

    def test_limit_caps_rows(self, tmp_path):
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, [
            {"id": i, "name": f"n{i}"} for i in range(5)
        ])
        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)

        result = server.query_iceberg_simple("gold.simple_tbl", limit=2)
        assert len(result) == 2

    def test_filter_value_is_bind_parameter_not_interpolated(self, tmp_path):
        """A filter value containing a SQL-meaningful character (quote) must
        not corrupt the query — proves values are bound, not interpolated."""
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, [{"id": 1, "name": "o'brien"}])
        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)

        result = server.query_iceberg_simple("gold.simple_tbl", filters={"name": "o'brien"})
        assert result == [{"id": 1, "name": "o'brien"}]

    def test_invalid_table_name_rejected(self, tmp_path):
        server = ConcreteServer(warehouse_path=tmp_path / "w", catalog_path=tmp_path / "c.db")
        result = server.query_iceberg_simple("gold; DROP TABLE x")
        assert "error" in result[0]

    def test_invalid_column_identifier_rejected(self, tmp_path):
        server = ConcreteServer(warehouse_path=tmp_path / "w", catalog_path=tmp_path / "c.db")
        result = server.query_iceberg_simple("gold.simple_tbl", columns=["name; DROP TABLE x"])
        assert "error" in result[0]

    def test_invalid_filter_column_rejected(self, tmp_path):
        server = ConcreteServer(warehouse_path=tmp_path / "w", catalog_path=tmp_path / "c.db")
        result = server.query_iceberg_simple("gold.simple_tbl", filters={"name; DROP": "x"})
        assert "error" in result[0]

    def test_unknown_table_returns_structured_error(self, tmp_path):
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)
        result = server.query_iceberg_simple("gold.does_not_exist")
        assert "error" in result[0]


class TestQueryConnectionCaching:
    """query_iceberg/query_iceberg_simple share a cached, persistent DuckDB
    connection (M6) — rebuilt only when the catalog's table set changes."""

    @staticmethod
    def _make_table(warehouse_path, catalog_path, namespace, table, rows):
        from pyiceberg.schema import Schema
        from pyiceberg.types import IntegerType, NestedField

        from brightsmith.infra.iceberg_setup import append_data, get_catalog, get_or_create_table

        catalog = get_catalog(warehouse_path, catalog_path)
        schema = Schema(NestedField(1, "id", IntegerType(), required=True))
        tbl = get_or_create_table(catalog, namespace, table, schema)
        append_data(tbl, rows, strict=False)
        return tbl

    def test_second_query_reuses_cached_connection_no_table_reload(self, tmp_path, monkeypatch):
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, "gold", "cache_tbl", [{"id": 1}])

        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)
        first = server.query_iceberg("SELECT * FROM gold_cache_tbl")
        assert first == [{"id": 1}]
        con_after_first = server._query_con

        load_calls = []
        original_load_table = server.catalog.load_table

        def spy_load_table(ident, *a, **k):
            load_calls.append(ident)
            return original_load_table(ident, *a, **k)

        monkeypatch.setattr(server.catalog, "load_table", spy_load_table)

        second = server.query_iceberg("SELECT * FROM gold_cache_tbl")
        assert second == [{"id": 1}]
        assert load_calls == [], "unchanged catalog must not re-load any table on the second query"
        assert server._query_con is con_after_first, "connection must be reused, not rebuilt"

    def test_new_table_triggers_rebuild_and_is_queryable(self, tmp_path):
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, "gold", "cache_tbl", [{"id": 1}])

        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)
        server.query_iceberg("SELECT * FROM gold_cache_tbl")
        con_before = server._query_con

        # A new table appears in the catalog after the first query.
        self._make_table(warehouse_path, catalog_path, "gold", "cache_tbl2", [{"id": 2}])

        result = server.query_iceberg("SELECT * FROM gold_cache_tbl2")
        assert result == [{"id": 2}]
        assert server._query_con is not con_before, "new table must trigger a connection rebuild"

    def test_read_csv_still_blocked_after_cache_refresh(self, tmp_path):
        """The security-critical regression: after a cache refresh (new table
        triggers close+reopen), the fresh connection must still have
        enable_external_access=false / lock_configuration=true applied —
        refresh must not accidentally reopen the door."""
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, "gold", "cache_tbl", [{"id": 1}])

        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)
        server.query_iceberg("SELECT * FROM gold_cache_tbl")

        # Trigger a rebuild.
        self._make_table(warehouse_path, catalog_path, "gold", "cache_tbl2", [{"id": 2}])
        server.query_iceberg("SELECT * FROM gold_cache_tbl2")

        blocked = server.query_iceberg("SELECT * FROM read_csv('/etc/hosts')")
        assert isinstance(blocked, list)
        assert "error" in blocked[0]
        err = blocked[0]["error"].lower()
        assert "permission" in err or "file system" in err or "disabled" in err

    def test_query_iceberg_simple_and_query_iceberg_share_the_cache(self, tmp_path):
        warehouse_path = tmp_path / "warehouse"
        catalog_path = tmp_path / "catalog.db"
        self._make_table(warehouse_path, catalog_path, "gold", "cache_tbl", [{"id": 1}])

        server = ConcreteServer(warehouse_path=warehouse_path, catalog_path=catalog_path)
        server.query_iceberg_simple("gold.cache_tbl")
        con_after_simple = server._query_con
        assert con_after_simple is not None

        result = server.query_iceberg("SELECT * FROM gold_cache_tbl")
        assert result == [{"id": 1}]
        assert server._query_con is con_after_simple, "both methods must reuse the same cached connection"


class TestQueryConnectionLifecycle:
    def test_close_query_connection_is_idempotent(self, tmp_path):
        server = ConcreteServer(warehouse_path=tmp_path / "w", catalog_path=tmp_path / "c.db")
        server.close_query_connection()  # no-op, never queried
        server.query_iceberg("SELECT 1 AS x")
        assert server._query_con is not None
        server.close_query_connection()
        assert server._query_con is None
        server.close_query_connection()  # idempotent second call
