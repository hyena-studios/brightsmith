"""Base chat agent for AI-Ready zone — the standard deliverable.

Every Grist pipeline produces a tool-use chat agent as its AI-Ready zone
output. Domain projects extend BaseChatAgent with domain-specific tools
and system prompts. The framework handles Anthropic SDK setup, tool
registration, conversation loop, and Iceberg query execution.

Usage:
    class MyDomainAgent(BaseChatAgent):
        def get_system_prompt(self) -> str:
            return "You are an expert on ..."

        def register_tools(self) -> list[dict]:
            return [
                {
                    "name": "query_revenue",
                    "description": "Query revenue data",
                    "input_schema": {...},
                    "handler": self._query_revenue,
                },
            ]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from grist.infra.iceberg_setup import get_catalog


class BaseChatAgent:
    """Framework base class for AI-Ready zone chat agents.

    Parallel to BaseIngestor for the Raw zone — domain projects extend
    this class with domain-specific tools and system prompts.

    Args:
        warehouse_path: Path to Iceberg warehouse.
        catalog_path: Path to SQLite catalog.
        grounding_docs_path: Optional path to grounding docs directory.
        model: Anthropic model to use.
        max_tokens: Max tokens per response.
    """

    def __init__(
        self,
        warehouse_path: str | Path,
        catalog_path: str | Path,
        grounding_docs_path: str | Path | None = None,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
    ):
        self.warehouse_path = Path(warehouse_path)
        self.catalog_path = Path(catalog_path)
        self.grounding_docs_path = Path(grounding_docs_path) if grounding_docs_path else None
        self.model = model
        self.max_tokens = max_tokens
        self._catalog = None
        self._tools: list[dict] = []
        self._tool_handlers: dict[str, callable] = {}

    @property
    def catalog(self):
        if self._catalog is None:
            self._catalog = get_catalog(self.warehouse_path, self.catalog_path)
        return self._catalog

    def get_system_prompt(self) -> str:
        """Override in domain project. Returns the system prompt for the chat agent."""
        raise NotImplementedError("Domain projects must implement get_system_prompt()")

    def register_tools(self) -> list[dict]:
        """Override in domain project. Returns tool definitions with handlers.

        Each tool dict should have:
        - name: str
        - description: str
        - input_schema: dict (JSON Schema)
        - handler: callable(dict) -> str  (takes input dict, returns result string)
        """
        raise NotImplementedError("Domain projects must implement register_tools()")

    def load_grounding_docs(self, path: str | Path | None = None) -> str:
        """Load grounding documents into a single string for the system prompt.

        Concatenates all .md files in the grounding docs directory.
        """
        docs_path = Path(path) if path else self.grounding_docs_path
        if not docs_path or not docs_path.exists():
            return ""

        parts = []
        for md_file in sorted(docs_path.glob("*.md")):
            parts.append(f"## {md_file.stem}\n\n{md_file.read_text()}")

        return "\n\n---\n\n".join(parts)

    def query_iceberg(self, sql: str) -> list[dict]:
        """Execute a SQL query against Iceberg tables via DuckDB.

        Uses the same pattern as iceberg_setup.read_with_duckdb() but allows
        arbitrary SQL across multiple tables.
        """
        con = duckdb.connect()
        con.install_extension("iceberg")
        con.load_extension("iceberg")

        # Register any tables referenced in the SQL
        # Simple approach: scan all namespaces for tables
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
        columns = [desc[0] for desc in con.description]
        con.close()
        return [dict(zip(columns, row)) for row in result]

    def _build_system_prompt(self) -> str:
        """Assemble full system prompt with grounding docs."""
        base = self.get_system_prompt()
        grounding = self.load_grounding_docs()
        if grounding:
            return f"{base}\n\n# Reference Data\n\n{grounding}"
        return base

    def _build_tools(self) -> list[dict]:
        """Build Anthropic API tool definitions from registered tools."""
        registered = self.register_tools()
        self._tools = []
        self._tool_handlers = {}

        for tool in registered:
            handler = tool.pop("handler", None)
            self._tools.append(tool)
            if handler:
                self._tool_handlers[tool["name"]] = handler

        return self._tools

    def _handle_tool_call(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool call and return the result string."""
        handler = self._tool_handlers.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            result = handler(tool_input)
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def chat(self, user_message: str, messages: list[dict] | None = None) -> str:
        """Process a user message through the tool-use conversation loop.

        Args:
            user_message: The user's input.
            messages: Optional existing conversation history.

        Returns:
            The assistant's final text response.
        """
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package required for chat agents. "
                "Install with: pip install anthropic"
            )

        client = anthropic.Anthropic()
        system = self._build_system_prompt()
        tools = self._build_tools()

        if messages is None:
            messages = []
        messages.append({"role": "user", "content": user_message})

        while True:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )

            # Check if the response requires tool use
            if response.stop_reason == "tool_use":
                # Collect all tool uses from the response
                assistant_content = response.content
                messages.append({"role": "assistant", "content": assistant_content})

                tool_results = []
                for block in assistant_content:
                    if block.type == "tool_use":
                        result = self._handle_tool_call(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "user", "content": tool_results})
                continue

            # Extract text response
            text_parts = [
                block.text for block in response.content if hasattr(block, "text")
            ]
            return "\n".join(text_parts)
