"""Base MCP server for AI-Ready zone — the standard deliverable.

Every Grist pipeline produces an MCP server as its AI-Ready zone output.
Domain projects extend BaseMCPServer with domain-specific tools and
resources. The framework handles MCP protocol, tool registration,
Iceberg query execution, and governance metadata attachment.

Usage:
    class MyDomainServer(BaseMCPServer):
        def get_tools(self) -> list[ToolDef]:
            return [
                ToolDef(
                    name="query_financials",
                    description="Query financial data by company and period",
                    input_schema={...},
                    handler=self._query_financials,
                ),
            ]

        def get_resources(self) -> list[ResourceDef]:
            return [
                ResourceDef(
                    uri="grist://domain-context",
                    name="Domain Context",
                    description="Domain knowledge for financial data",
                    handler=self._get_domain_context,
                ),
            ]
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from grist.infra.iceberg_setup import get_catalog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool and resource definitions (framework-level, not MCP types)
# ---------------------------------------------------------------------------


@dataclass
class ToolDef:
    """A tool definition for the MCP server.

    Framework-level definition that gets converted to MCP Tool objects.
    """

    name: str
    description: str
    input_schema: dict
    handler: Any  # callable(dict) -> dict


@dataclass
class ResourceDef:
    """A resource definition for the MCP server.

    Framework-level definition that gets converted to MCP Resource objects.
    """

    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"
    handler: Any = None  # callable() -> str


# ---------------------------------------------------------------------------
# BaseMCPServer
# ---------------------------------------------------------------------------


class BaseMCPServer:
    """Framework base class for AI-Ready zone MCP servers.

    Parallel to BaseIngestor for the Raw zone — domain projects extend
    this class with domain-specific tools and resources.

    Args:
        warehouse_path: Path to Iceberg warehouse.
        catalog_path: Path to SQLite catalog.
        grounding_docs_path: Optional path to grounding docs directory.
        server_name: Name for the MCP server.
    """

    def __init__(
        self,
        warehouse_path: str | Path,
        catalog_path: str | Path,
        grounding_docs_path: str | Path | None = None,
        server_name: str = "grist",
    ):
        self.warehouse_path = Path(warehouse_path)
        self.catalog_path = Path(catalog_path)
        self.grounding_docs_path = Path(grounding_docs_path) if grounding_docs_path else None
        self.server_name = server_name
        self._catalog = None

    @property
    def catalog(self):
        if self._catalog is None:
            self._catalog = get_catalog(self.warehouse_path, self.catalog_path)
        return self._catalog

    # --- Abstract methods (domain projects override) ---

    def get_tools(self) -> list[ToolDef]:
        """Override in domain project. Returns domain-specific tool definitions.

        Framework tools (query_table, list_tables, etc.) are always included
        automatically. Domain tools are added on top.
        """
        return []

    def get_resources(self) -> list[ResourceDef]:
        """Override in domain project. Returns domain-specific resource definitions.

        Framework resources (domain context, glossary, etc.) are always
        included automatically. Domain resources are added on top.
        """
        return []

    # --- Framework-provided tools ---

    def _all_tools(self) -> list[ToolDef]:
        """Combine framework tools with domain tools."""
        framework_tools = [
            ToolDef(
                name="query_table",
                description="Query a consumable Iceberg table with optional filters",
                input_schema={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Full table name (e.g., consumable.company_financials)"},
                        "filters": {"type": "object", "description": "Column-value filter pairs", "default": {}},
                        "columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to return (empty = all)"},
                        "limit": {"type": "integer", "description": "Max rows to return", "default": 100},
                    },
                    "required": ["table"],
                },
                handler=self._handle_query_table,
            ),
            ToolDef(
                name="list_tables",
                description="List available Iceberg tables with descriptions",
                input_schema={"type": "object", "properties": {}},
                handler=self._handle_list_tables,
            ),
            ToolDef(
                name="get_data_quality",
                description="Get data quality scorecard for a table",
                input_schema={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                    },
                    "required": ["table"],
                },
                handler=self._handle_get_data_quality,
            ),
            ToolDef(
                name="get_lineage",
                description="Get lineage information for a table",
                input_schema={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                    },
                    "required": ["table"],
                },
                handler=self._handle_get_lineage,
            ),
            ToolDef(
                name="get_contract",
                description="Get data contract for a table",
                input_schema={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                    },
                    "required": ["table"],
                },
                handler=self._handle_get_contract,
            ),
        ]
        return framework_tools + self.get_tools()

    # --- Framework-provided resources ---

    def _all_resources(self) -> list[ResourceDef]:
        """Combine framework resources with domain resources."""
        from grist.config import PROJECT_ROOT

        framework_resources = []

        # Domain context
        domain_ctx = PROJECT_ROOT / "governance" / "domain-context.md"
        if domain_ctx.exists():
            framework_resources.append(ResourceDef(
                uri="grist://domain-context",
                name="Domain Context",
                description="Canonical domain knowledge for interpreting this data",
                mime_type="text/markdown",
                handler=lambda p=domain_ctx: p.read_text(),
            ))

        # Business glossary
        glossary = PROJECT_ROOT / "governance" / "business-glossary.json"
        if glossary.exists():
            framework_resources.append(ResourceDef(
                uri="grist://business-glossary",
                name="Business Glossary",
                description="Business term definitions with CDE/PII flags",
                mime_type="application/json",
                handler=lambda p=glossary: p.read_text(),
            ))

        # Data dictionary
        data_dict = PROJECT_ROOT / "governance" / "data-dictionary.json"
        if data_dict.exists():
            framework_resources.append(ResourceDef(
                uri="grist://data-dictionary",
                name="Data Dictionary",
                description="Field-level documentation for all tables",
                mime_type="application/json",
                handler=lambda p=data_dict: p.read_text(),
            ))

        # Grounding docs
        if self.grounding_docs_path and self.grounding_docs_path.exists():
            for md_file in sorted(self.grounding_docs_path.glob("*.md")):
                framework_resources.append(ResourceDef(
                    uri=f"grist://grounding/{md_file.stem}",
                    name=f"Grounding: {md_file.stem}",
                    description=f"Grounding document: {md_file.stem}",
                    mime_type="text/markdown",
                    handler=lambda p=md_file: p.read_text(),
                ))

        return framework_resources + self.get_resources()

    # --- Tool handlers ---

    def _handle_query_table(self, input_dict: dict) -> dict:
        """Query a consumable table with filters."""
        table_name = input_dict["table"]
        filters = input_dict.get("filters", {})
        columns = input_dict.get("columns", [])
        limit = input_dict.get("limit", 100)

        rows = self.query_iceberg_simple(table_name, filters, columns, limit)
        return self.attach_governance({"data": rows, "row_count": len(rows)}, table_name)

    def _handle_list_tables(self, input_dict: dict) -> dict:
        """List available tables."""
        tables = []
        for ns_tuple in self.catalog.list_namespaces():
            ns = ns_tuple[0] if isinstance(ns_tuple, tuple) else ns_tuple
            try:
                for table_id in self.catalog.list_tables(ns):
                    tbl = table_id[1] if isinstance(table_id, tuple) else table_id
                    tables.append({"namespace": ns, "table": tbl, "full_name": f"{ns}.{tbl}"})
            except Exception:
                pass
        return {"tables": tables}

    def _handle_get_data_quality(self, input_dict: dict) -> dict:
        """Get DQ scorecard for a table."""
        from grist.config import DQ_SCORECARDS_DIR
        table_name = input_dict["table"]
        # Find scorecard files matching the table
        if DQ_SCORECARDS_DIR.exists():
            for f in DQ_SCORECARDS_DIR.glob("*.md"):
                if table_name.replace(".", "-") in f.stem or table_name.split(".")[-1] in f.stem:
                    return {"table": table_name, "scorecard": f.read_text()}
        return {"table": table_name, "scorecard": "No scorecard found"}

    def _handle_get_lineage(self, input_dict: dict) -> dict:
        """Get lineage for a table."""
        from grist.config import PROJECT_ROOT
        table_name = input_dict["table"]
        lineage_dir = PROJECT_ROOT / "governance" / "lineage"
        if lineage_dir.exists():
            for f in lineage_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    if table_name in str(data):
                        return {"table": table_name, "lineage": data}
                except Exception:
                    pass
        return {"table": table_name, "lineage": "No lineage found"}

    def _handle_get_contract(self, input_dict: dict) -> dict:
        """Get data contract for a table."""
        try:
            from grist.infra.contract import list_contracts, load_contract
            contracts = list_contracts()
            for c in contracts:
                if c.get("table") == input_dict["table"]:
                    contract = load_contract(c["name"])
                    return {"table": input_dict["table"], "contract": contract}
        except Exception:
            pass
        return {"table": input_dict["table"], "contract": "No contract found"}

    # --- Query utility ---

    def query_iceberg_simple(
        self,
        table_name: str,
        filters: dict | None = None,
        columns: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query an Iceberg table with simple filters.

        Args:
            table_name: Full table name (namespace.table).
            filters: Column-value equality filters.
            columns: Columns to return (empty = all).
            limit: Max rows.

        Returns:
            List of row dicts.
        """
        from grist.infra.iceberg_setup import read_with_duckdb

        try:
            table = self.catalog.load_table(table_name)
            rows = read_with_duckdb(table)
        except Exception as e:
            return [{"error": f"Cannot query {table_name}: {e}"}]

        # Apply filters
        if filters:
            for col, val in filters.items():
                rows = [r for r in rows if str(r.get(col, "")) == str(val)]

        # Select columns
        if columns:
            rows = [{k: r.get(k) for k in columns} for r in rows]

        return rows[:limit]

    def query_iceberg(self, sql: str) -> list[dict]:
        """Execute arbitrary SQL against Iceberg tables via DuckDB.

        This is the single choke point for all data access. Future RLS
        filters, entitlement checks, and audit logging inject here.
        """
        con = duckdb.connect()
        con.install_extension("iceberg")
        con.load_extension("iceberg")

        for ns_tuple in self.catalog.list_namespaces():
            ns = ns_tuple[0] if isinstance(ns_tuple, tuple) else ns_tuple
            try:
                for table_id in self.catalog.list_tables(ns):
                    tbl_name = table_id[1] if isinstance(table_id, tuple) else table_id
                    view_name = f"{ns}_{tbl_name}"
                    full_id = f"{ns}.{tbl_name}"
                    try:
                        iceberg_table = self.catalog.load_table(full_id)
                        metadata_path = iceberg_table.metadata_location
                        con.execute(
                            f"CREATE VIEW IF NOT EXISTS {view_name} AS "
                            f"SELECT * FROM iceberg_scan('{metadata_path}')"
                        )
                    except Exception:
                        pass
            except Exception:
                pass

        result = con.execute(sql).fetchall()
        columns_list = [desc[0] for desc in con.description]
        con.close()
        return [dict(zip(columns_list, row)) for row in result]

    # --- Governance metadata ---

    def attach_governance(self, result: dict, table_name: str) -> dict:
        """Attach governance metadata to a tool response.

        Every tool response includes: contract info, DQ status, lineage,
        and last update time. The LLM client can use this to calibrate confidence.
        """
        governance = {"table": table_name}

        try:
            from grist.infra.contract import list_contracts
            for c in list_contracts():
                if c.get("table") == table_name:
                    governance["contract_version"] = c.get("version", "?")
                    governance["contract_status"] = c.get("status", "?")
                    break
        except Exception:
            pass

        result["governance"] = governance
        return result

    # --- Grounding docs ---

    def load_grounding_docs(self, path: str | Path | None = None) -> str:
        """Load grounding documents into a single string.

        Concatenates all .md files in the grounding docs directory.
        """
        docs_path = Path(path) if path else self.grounding_docs_path
        if not docs_path or not docs_path.exists():
            return ""

        parts = []
        for md_file in sorted(docs_path.glob("*.md")):
            parts.append(f"## {md_file.stem}\n\n{md_file.read_text()}")

        return "\n\n---\n\n".join(parts)

    # --- MCP server creation ---

    def create_mcp_server(self):
        """Create and configure an MCP Server instance.

        Returns a configured mcp.server.Server ready to run.
        """
        from mcp.server import Server
        from mcp.types import Resource, TextContent, Tool

        server = Server(self.server_name)
        all_tools = self._all_tools()
        all_resources = self._all_resources()
        tool_handlers = {t.name: t.handler for t in all_tools}

        @server.list_tools()
        async def handle_list_tools():
            return [
                Tool(
                    name=t.name,
                    description=t.description,
                    inputSchema=t.input_schema,
                )
                for t in all_tools
            ]

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict | None = None):
            handler = tool_handlers.get(name)
            if not handler:
                return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
            try:
                result = handler(arguments or {})
                text = result if isinstance(result, str) else json.dumps(result, default=str)
            except Exception as e:
                text = json.dumps({"error": str(e)})
            return [TextContent(type="text", text=text)]

        @server.list_resources()
        async def handle_list_resources():
            return [
                Resource(
                    uri=r.uri,
                    name=r.name,
                    description=r.description,
                    mimeType=r.mime_type,
                )
                for r in all_resources
            ]

        @server.read_resource()
        async def handle_read_resource(uri: str):
            for r in all_resources:
                if r.uri == str(uri):
                    content = r.handler() if r.handler else ""
                    return content
            return f"Resource not found: {uri}"

        return server

    async def serve(self) -> None:
        """Start the MCP server in stdio mode."""
        from mcp.server.stdio import stdio_server

        server = self.create_mcp_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
