"""Base MCP server for AI-Ready zone — the standard deliverable.

Every Brightsmith pipeline produces an MCP server as its AI-Ready zone output.
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
                    uri="brightsmith://domain-context",
                    name="Domain Context",
                    description="Domain knowledge for financial data",
                    handler=self._get_domain_context,
                ),
            ]
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from brightsmith.infra.iceberg_setup import get_catalog, list_catalog_table_locations

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL identifier validation (W3c/d — docs/technical-audit-2026-07-02.md M1/M6)
# ---------------------------------------------------------------------------

# Table/namespace/column identifiers are validated against this strict
# pattern before ever being interpolated into SQL text. VALUES (filter
# values) are never interpolated — they are always passed as DuckDB bind
# parameters. Adapted from futureproof-data's `_query_engine.py` (a field
# rewrite of these same helpers) `_IDENT_PATTERN`.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ---------------------------------------------------------------------------
# Read-only SQL validation (WP-1.5 / S1)
# ---------------------------------------------------------------------------

# Keywords whose statements are permitted on the untrusted MCP surface.
_ALLOWED_FIRST_KEYWORDS: frozenset[str] = frozenset({"SELECT", "WITH", "DESCRIBE", "SHOW"})

_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_LINE_COMMENT = re.compile(r"--[^\n]*")
_RE_FIRST_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _strip_sql_comments(sql: str) -> str:
    """Remove /* … */ block comments and -- line comments from SQL."""
    sql = _RE_BLOCK_COMMENT.sub(" ", sql)
    sql = _RE_LINE_COMMENT.sub(" ", sql)
    return sql.strip()


def _validate_read_only_sql(sql: str) -> str | None:
    """Validate that *sql* is a single, read-only statement.

    Returns ``None`` when the statement is allowed.  Returns a human-readable
    rejection reason string when it should be blocked.

    Allowed first keywords: SELECT, WITH, DESCRIBE, SHOW.
    Everything else (COPY, INSTALL, LOAD, ATTACH, CREATE, DROP, ALTER,
    INSERT, UPDATE, DELETE, PRAGMA that sets state, …) is rejected.

    The check is robust to:
    * Leading/trailing whitespace
    * ``/* … */`` block comments and ``--`` line comments
    * Leading parentheses (e.g. ``(WITH cte AS …)``)

    Multiple statements (detected via embedded semicolons) are also rejected.
    Note: semicolons inside string literals are a known edge case; the
    conservative approach here is to reject such SQL — better to false-positive
    than to allow an injected second statement on an untrusted surface.
    """
    cleaned = _strip_sql_comments(sql)
    if not cleaned:
        return "SQL rejected: empty statement."

    # Multiple-statement check: strip trailing semicolon(s), then flag any
    # remaining semicolons as an embedded statement separator.
    without_trailing = cleaned.rstrip(";").rstrip()
    if ";" in without_trailing:
        return (
            "SQL rejected: multiple statements are not allowed "
            "(semicolon detected inside the statement)."
        )

    # Strip leading parentheses/whitespace to reach the first keyword.
    # Handles forms like (WITH cte AS …) or (SELECT …).
    stripped = cleaned.lstrip("(").strip()

    m = _RE_FIRST_IDENT.match(stripped)
    if not m:
        return "SQL rejected: could not parse first keyword."

    first_keyword = m.group(0).upper()
    if first_keyword not in _ALLOWED_FIRST_KEYWORDS:
        allowed = ", ".join(sorted(_ALLOWED_FIRST_KEYWORDS))
        return (
            f"SQL rejected: statement type '{first_keyword}' is not permitted "
            f"on this read-only surface. Allowed statement types: {allowed}."
        )

    return None


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

    Trust boundary
    --------------
    Two SQL execution paths exist with different trust levels:

    * **Trusted (operator-supplied):** DQ-rule SQL loaded from governance JSON
      files in ``governance/dq-rules/``.  These are written by the
      ``@dq-rule-writer`` agent, reviewed, and committed to the repo.  They
      execute through ``dq_runner.run_rules()`` without going through
      ``query_iceberg``.

    * **Untrusted (MCP-client-supplied):** SQL arriving via ``query_iceberg``
      from an LLM client.  This surface is prompt-injection territory.
      ``query_iceberg`` enforces two layers of read-only protection:

      1. **Statement allowlist** — only SELECT, WITH, DESCRIBE, and SHOW are
         permitted as the leading statement keyword; anything else (COPY, DDL,
         DML, INSTALL, LOAD, ATTACH, …) is rejected before DuckDB sees it.
      2. **DuckDB external-access lock** — ``SET enable_external_access=false``
         is applied on every connection, blocking file reads/writes
         (``read_csv``, ``read_parquet``, ``COPY … TO``, etc.) even if a
         statement somehow passed the allowlist.

    RLS / entitlements are explicitly deferred until non-localhost deployment
    (decision D5 in the audit-remediation spec).

    Args:
        warehouse_path: Path to Iceberg warehouse.
        catalog_path: Path to SQLite catalog.
        grounding_docs_path: Optional path to grounding docs directory.
        server_name: Name for the MCP server.
        formatter: Optional value formatter for response enrichment.
        anomaly_checker: Optional anomaly checker for response enrichment.
        system_prompt: Optional system prompt builder.
    """

    def __init__(
        self,
        warehouse_path: str | Path,
        catalog_path: str | Path,
        grounding_docs_path: str | Path | None = None,
        server_name: str = "brightsmith",
        formatter: Any | None = None,
        anomaly_checker: Any | None = None,
        system_prompt: Any | None = None,
    ):
        self.warehouse_path = Path(warehouse_path)
        self.catalog_path = Path(catalog_path)
        self.grounding_docs_path = Path(grounding_docs_path) if grounding_docs_path else None
        self.server_name = server_name
        self.formatter = formatter
        self.anomaly_checker = anomaly_checker
        self.system_prompt = system_prompt
        self._catalog = None

        # --- Cached query connection (M6) ---
        # See _ensure_query_connection for the caching/refresh design. A
        # persistent duckdb connection is opened lazily on first query and
        # reused across query_iceberg / query_iceberg_simple calls until the
        # catalog's table set or a table's metadata_location changes.
        self._query_con: duckdb.DuckDBPyConnection | None = None
        self._view_registry: dict[str, str] = {}  # view_name -> metadata_location
        self._table_locations_seen: dict[tuple[str, str], str] | None = None
        self._query_lock = threading.RLock()

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
                description="Query a gold-zone Iceberg table with optional filters",
                input_schema={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Full table name (e.g., gold.company_financials)"},
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
            ToolDef(
                name="get_semantic_model",
                description=(
                    "Get the OSI semantic model: datasets, primary keys, field "
                    "descriptions/synonyms, relationships (join columns), and metric "
                    "definitions. Call this BEFORE writing queries to learn correct "
                    "join keys, column meanings, and metric expressions. Pass 'table' "
                    "to get just that dataset plus its relationships and the model's "
                    "metrics (omits the full domain-context instructions)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "table": {
                            "type": "string",
                            "description": "Optional table to scope to (e.g. gold.company_metrics or company_metrics). Empty = full model.",
                        },
                    },
                },
                handler=self._handle_get_semantic_model,
            ),
        ]
        return framework_tools + self.get_tools()

    # --- Framework-provided resources ---

    def _all_resources(self) -> list[ResourceDef]:
        """Combine framework resources with domain resources."""
        from brightsmith.config import PROJECT_ROOT

        framework_resources = []

        # Domain context
        domain_ctx = PROJECT_ROOT / "governance" / "domain-context.md"
        if domain_ctx.exists():
            framework_resources.append(ResourceDef(
                uri="brightsmith://domain-context",
                name="Domain Context",
                description="Canonical domain knowledge for interpreting this data",
                mime_type="text/markdown",
                handler=lambda p=domain_ctx: p.read_text(),
            ))

        # Business glossary
        glossary = PROJECT_ROOT / "governance" / "business-glossary.json"
        if glossary.exists():
            framework_resources.append(ResourceDef(
                uri="brightsmith://business-glossary",
                name="Business Glossary",
                description="Business term definitions with CDE/PII flags",
                mime_type="application/json",
                handler=lambda p=glossary: p.read_text(),
            ))

        # OSI semantic model (generated by `python -m brightsmith.infra.osi generate`
        # from contracts/glossary/models — see docs/specs/osi-semantic-model-export.md).
        # Served verbatim so OSI-aware clients get datasets/relationships/metrics in
        # the vendor-neutral interchange format rather than our internal artifacts.
        osi_model = PROJECT_ROOT / "governance" / "semantic-model.osi.yaml"
        if osi_model.exists():
            framework_resources.append(ResourceDef(
                uri="brightsmith://semantic-model",
                name="OSI Semantic Model",
                description="Open Semantic Interchange (OSI) semantic model: datasets, relationships, metrics, AI context",
                mime_type="application/yaml",
                handler=lambda p=osi_model: p.read_text(),
            ))

        # Data dictionary
        data_dict = PROJECT_ROOT / "governance" / "data-dictionary.json"
        if data_dict.exists():
            framework_resources.append(ResourceDef(
                uri="brightsmith://data-dictionary",
                name="Data Dictionary",
                description="Field-level documentation for all tables",
                mime_type="application/json",
                handler=lambda p=data_dict: p.read_text(),
            ))

        # Grounding docs
        if self.grounding_docs_path and self.grounding_docs_path.exists():
            for md_file in sorted(self.grounding_docs_path.glob("*.md")):
                framework_resources.append(ResourceDef(
                    uri=f"brightsmith://grounding/{md_file.stem}",
                    name=f"Grounding: {md_file.stem}",
                    description=f"Grounding document: {md_file.stem}",
                    mime_type="text/markdown",
                    handler=lambda p=md_file: p.read_text(),
                ))

        # System prompt (if configured)
        if self.system_prompt is not None:
            framework_resources.append(ResourceDef(
                uri="brightsmith://system-prompt",
                name="Recommended System Prompt",
                description="Data-aware system prompt for LLM clients using this MCP server",
                mime_type="text/markdown",
                handler=self.system_prompt.build,
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
                # Advisory list-tables tool: a namespace we can't enumerate is
                # skipped so the rest still list; logged, never silent.
                logger.warning("list_tables: skipping namespace %s", ns, exc_info=True)
        return {"tables": tables}

    def _handle_get_data_quality(self, input_dict: dict) -> dict:
        """Get DQ scorecard for a table."""
        from brightsmith.config import DQ_SCORECARDS_DIR
        table_name = input_dict["table"]
        # Find scorecard files matching the table
        if DQ_SCORECARDS_DIR.exists():
            for f in DQ_SCORECARDS_DIR.glob("*.md"):
                if table_name.replace(".", "-") in f.stem or table_name.split(".")[-1] in f.stem:
                    return {"table": table_name, "scorecard": f.read_text()}
        return {"table": table_name, "scorecard": "No scorecard found"}

    def _handle_get_lineage(self, input_dict: dict) -> dict:
        """Get lineage for a table. Queries Iceberg events, falls back to governance docs."""
        table_name = input_dict["table"]

        # 1. Try Iceberg lineage_events table (runtime data)
        try:
            from brightsmith.infra.lineage import query_lineage_events
            events = query_lineage_events(table_name, limit=5)
            if events:
                latest = events[0]
                input_tables_raw = latest.get("input_tables", "[]")
                try:
                    input_tables = json.loads(input_tables_raw) if isinstance(input_tables_raw, str) else input_tables_raw
                except (json.JSONDecodeError, TypeError):
                    input_tables = []
                return self.attach_governance({
                    "table": table_name,
                    "source": "runtime",
                    "latest_event": {
                        "run_id": latest.get("run_id"),
                        "event_time": str(latest.get("event_time", "")),
                        "row_count": latest.get("row_count"),
                        "snapshot_id": latest.get("output_snapshot_id"),
                        "duration_ms": latest.get("duration_ms"),
                        "dq_passed": latest.get("dq_rules_passed"),
                        "dq_total": latest.get("dq_rules_total"),
                    },
                    "input_tables": input_tables,
                    "event_count": len(events),
                }, table_name)
        except Exception:
            # Governance-DB lineage unavailable — fall back to files below.
            # Logged so the fallback is visible, never silent.
            logger.debug("get_lineage: governance-DB lookup failed, falling back to files", exc_info=True)

        # 2. Fall back to governance/lineage/ files
        from brightsmith.config import PROJECT_ROOT
        lineage_dir = PROJECT_ROOT / "governance" / "lineage"
        if lineage_dir.exists():
            for f in lineage_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    if table_name in str(data):
                        return {"table": table_name, "source": "governance_doc", "lineage": data}
                except (OSError, ValueError) as e:
                    # Unreadable/invalid lineage JSON is skipped; logged so the
                    # omission is visible, never silent.
                    logger.debug("get_lineage: skipping %s: %s", f, e)
        return {"table": table_name, "lineage": "No lineage found"}

    def _handle_get_contract(self, input_dict: dict) -> dict:
        """Get data contract for a table."""
        try:
            from brightsmith.infra.contract import list_contracts, load_contract
            contracts = list_contracts()
            for c in contracts:
                if c.get("table") == input_dict["table"]:
                    contract = load_contract(c["name"])
                    return {"table": input_dict["table"], "contract": contract}
        except Exception:
            # Advisory contract lookup: on failure the tool reports "No contract
            # found" rather than crashing; logged so the failure is visible.
            logger.debug("get_contract: lookup failed for %s", input_dict.get("table"), exc_info=True)
        return {"table": input_dict["table"], "contract": "No contract found"}

    def _handle_get_semantic_model(self, input_dict: dict) -> dict:
        """Return the OSI semantic model, optionally scoped to one dataset.

        Model-invokable counterpart of the ``brightsmith://semantic-model``
        resource (docs/specs/osi-semantic-model-export.md follow-up): MCP
        resources are client-pulled and not all clients auto-attach them, so
        a tool guarantees the model can fetch semantic context mid-conversation.

        Unscoped: the full document (including model-level ``ai_context``).
        Scoped via ``table`` (``namespace.table`` or bare dataset name): just
        that dataset, the relationships touching it, and the model's metrics —
        the model-level ``ai_context`` (the full domain-context text, the token
        hog) is omitted; read the resource or call unscoped when you need it.
        """
        import yaml

        from brightsmith.config import PROJECT_ROOT

        path = PROJECT_ROOT / "governance" / "semantic-model.osi.yaml"
        if not path.exists():
            return {
                "semantic_model": None,
                "message": (
                    "No OSI semantic model found. Generate it with: "
                    "python -m brightsmith.infra.osi generate"
                ),
            }
        try:
            doc = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError) as e:
            # Advisory tool: an unreadable model becomes a structured error
            # (matching get_contract/get_lineage), never a handler crash.
            logger.warning("get_semantic_model: %s is unreadable: %s", path, e)
            return {"error": f"Semantic model file is unreadable: {e}"}

        if not isinstance(doc, dict) or not isinstance(doc.get("semantic_model"), dict):
            return {"error": f"Semantic model file has an unexpected shape: {path}"}
        model = doc["semantic_model"]

        table = str(input_dict.get("table") or "").strip()
        if not table:
            return {"version": doc.get("version"), "semantic_model": model}

        wanted = (table.split(".", 1)[1] if "." in table else table).lower()
        datasets = model.get("datasets", [])
        match = next((d for d in datasets if str(d.get("name", "")).lower() == wanted), None)
        if match is None:
            return {
                "error": f"No dataset named {table!r} in the semantic model",
                "available_datasets": sorted(str(d.get("name", "")) for d in datasets),
            }

        name = match.get("name")
        relationships = [
            r for r in model.get("relationships", [])
            if r.get("from") == name or r.get("to") == name
        ]
        return {
            "version": doc.get("version"),
            "dataset": match,
            "relationships": relationships,
            "metrics": model.get("metrics", []),
        }

    # --- Query utility ---

    def _view_name_for(self, table_name: str) -> str:
        """Translate ``namespace.table`` -> the ``namespace_table`` view name
        registered by :meth:`_ensure_query_connection`, validating both
        halves are safe SQL identifiers before they are ever interpolated
        into a SQL statement.

        Raises:
            ValueError: ``table_name`` isn't a ``namespace.table`` pair of
                identifiers matching ``_IDENT_RE``.
        """
        parts = table_name.split(".", 1)
        if len(parts) != 2 or not all(_IDENT_RE.match(p) for p in parts):
            raise ValueError(f"invalid table name: {table_name!r}")
        ns, tbl = parts
        return f"{ns}_{tbl}"

    def _ensure_query_connection(self) -> duckdb.DuckDBPyConnection:
        """Return a ready-to-query DuckDB connection with every current
        Iceberg table registered as a view.

        Design (docs/technical-audit-2026-07-02.md M6 / W3d): a PERSISTENT
        connection is cached on the instance and reused across
        ``query_iceberg``/``query_iceberg_simple`` calls instead of being
        rebuilt from scratch every time. Each call still pays one CHEAP
        staleness check — a single raw-SQLite read of the catalog's
        (namespace, table) -> metadata_location rows via
        :func:`list_catalog_table_locations` (no PyIceberg ``Table``
        construction, no ``metadata.json`` parsing) — compared against the
        last-registered snapshot:

        * Unchanged since last call (the common case): the cached,
          already-locked connection is returned as-is. No view DDL, no
          per-table catalog I/O beyond that one cheap read.
        * Changed (a table was added/removed, or an existing table's
          metadata_location moved because new data was committed): the
          connection is torn down and rebuilt from scratch. This is the only
          path that pays the guarded ``catalog.load_table()`` per table
          (which re-applies the relocation guard) and re-creates every view —
          all BEFORE ``lock_configuration`` is set. A locked connection
          cannot re-register views, so "refresh" here always means
          close-and-reopen, never mutating a locked connection in place.

        Security ordering is identical to (and only executed during) a
        rebuild, matching the C1 fix: allowed_directories ->
        enable_external_access=false -> view registrations ->
        lock_configuration=true -> (caller executes SQL against the result).
        """
        with self._query_lock:
            current = list_catalog_table_locations(self.catalog_path)
            if self._query_con is not None and current == self._table_locations_seen:
                return self._query_con

            self.close_query_connection()

            con = duckdb.connect()
            con.install_extension("iceberg")
            con.load_extension("iceberg")

            # `iceberg_scan()` views are lazy: the file reads they need happen
            # when the view is *queried*, not when it is created. A blanket
            # `enable_external_access=false` blocks those reads unconditionally,
            # so disabling access before registering views made every real-table
            # query fail with "Table … does not exist" (audit finding C1) — the
            # views were created but could never be scanned. Fix (sketch A):
            # scope external access to the warehouse root via
            # `allowed_directories`, which DuckDB honors even with
            # `enable_external_access=false`, *before* disabling access, so
            # `iceberg_scan` can still read warehouse files while everything
            # else (read_csv, read_parquet outside the warehouse, COPY, …)
            # stays blocked. Verified empirically against the pinned DuckDB
            # 1.5.0 (uv.lock) — see docs/technical-audit-2026-07-02.md sketch A.
            warehouse_root = str(self.warehouse_path.resolve())
            con.execute("SET allowed_directories = ?", [[warehouse_root]])
            # Disable all other file-system access (read_csv, COPY TO, read_parquet, …).
            # Pragma name verified against DuckDB 1.5.0 (uv.lock).
            con.execute("SET enable_external_access=false")

            registry: dict[str, str] = {}
            for ns, tbl in current:
                full_id = f"{ns}.{tbl}"
                view_name = f"{ns}_{tbl}"
                try:
                    iceberg_table = self.catalog.load_table(full_id)
                    metadata_path = iceberg_table.metadata_location
                    con.execute(
                        f"CREATE VIEW IF NOT EXISTS {view_name} AS "
                        f"SELECT * FROM iceberg_scan('{metadata_path}')"
                    )
                    registry[view_name] = metadata_path
                except (duckdb.Error, OSError) as e:
                    # A single unloadable/relocated table must not sink the
                    # whole connection — it just won't be queryable. Logged so
                    # the omission is visible, never silent.
                    logger.warning("query_iceberg: skipping view for %s: %s", full_id, e)

            # Lock the configuration so untrusted SQL executed against this
            # connection cannot flip `enable_external_access` /
            # `allowed_directories` back open. Defense-in-depth: Layer 1 of
            # query_iceberg already rejects SET as a leading keyword, so this
            # only matters if that allowlist is ever bypassed. Once set, this
            # connection can never register another view — the only way to
            # pick up new/changed tables is the close-and-reopen path above.
            con.execute("SET lock_configuration=true")

            self._query_con = con
            self._view_registry = registry
            self._table_locations_seen = current
            return con

    def close_query_connection(self) -> None:
        """Close and drop the cached persistent query connection, if any.

        Idempotent — safe to call with no connection open. Called
        automatically by ``__del__`` so a forgotten close doesn't leak a
        DuckDB connection (see tests/infra/test_no_unclosed_connections.py);
        domain projects managing an explicit server shutdown may also call it
        directly.
        """
        con = self._query_con
        self._query_con = None
        self._view_registry = {}
        self._table_locations_seen = None
        if con is not None:
            try:
                con.close()
            except Exception:
                # Best-effort cleanup during teardown/refresh/finalization —
                # a close failure here must never raise out of __del__.
                # (Allowlisted in tests/infra/test_no_swallowed_exceptions.py.)
                logger.debug("query connection close failed", exc_info=True)

    def __del__(self) -> None:
        # getattr guard: if __init__ raised before _query_con was set (e.g. a
        # subclass failing in its own __init__ before calling super()),
        # __del__ must not itself raise an AttributeError during finalization.
        if getattr(self, "_query_con", None) is not None:
            self.close_query_connection()

    def query_iceberg_simple(
        self,
        table_name: str,
        filters: dict | None = None,
        columns: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query an Iceberg table with simple filters — predicate pushdown.

        Docs/technical-audit-2026-07-02.md M1/W3c: this used to read the
        WHOLE table into Python (``read_with_duckdb``) and filter/limit
        there. It now builds a parameterized SQL statement against the
        cached ``iceberg_scan`` view (see ``_ensure_query_connection``, M6)
        so filtering and limiting happen inside DuckDB — a non-matching
        filter or a small ``limit`` never materializes more than DuckDB
        itself needs to scan.

        ``table_name``/``columns``/filter *keys* are validated against a
        strict identifier pattern (``_IDENT_RE``) before being interpolated
        into SQL; filter *values* are always passed as DuckDB bind
        parameters, never interpolated — adapted from futureproof-data's
        ``_query_engine.py`` (``query_filtered``), which upstreams this exact
        pattern back into the framework.

        Args:
            table_name: Full table name (namespace.table).
            filters: Column-value equality filters (values are bind params).
            columns: Columns to return (empty = all).
            limit: Max rows, pushed into the SQL as ``LIMIT``.

        Returns:
            List of row dicts, or ``[{"error": "..."}]`` on a validation or
            execution failure (matching ``query_iceberg``'s error shape).
        """
        try:
            view_name = self._view_name_for(table_name)
        except ValueError as e:
            return [{"error": str(e)}]

        if columns:
            for c in columns:
                if not _IDENT_RE.match(c):
                    return [{"error": f"invalid column identifier: {c!r}"}]

        where_parts: list[str] = []
        params: list[Any] = []
        if filters:
            for col, val in filters.items():
                if not _IDENT_RE.match(col):
                    return [{"error": f"invalid filter column: {col!r}"}]
                where_parts.append(f"{col} = ?")
                params.append(val)
        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        select_cols = ", ".join(columns) if columns else "*"
        safe_limit = max(0, int(limit))
        sql = f"SELECT {select_cols} FROM {view_name}{where_clause} LIMIT {safe_limit}"

        try:
            with self._query_lock:
                self._ensure_query_connection()
                if view_name not in self._view_registry:
                    return [{"error": f"Cannot query {table_name}: table not found in catalog"}]
                con = self._query_con
                assert con is not None  # _ensure_query_connection always sets this
                cur = con.execute(sql, params) if params else con.execute(sql)
                rows_raw = cur.fetchall()
                col_names = [d[0] for d in con.description]
        except Exception as e:
            # Any failure here (unknown column, mid-flight table drift, engine
            # error) becomes a structured result — never raises out of an MCP
            # tool handler. (Allowlisted in test_no_swallowed_exceptions.py.)
            return [{"error": f"Cannot query {table_name}: {e}"}]

        return [dict(zip(col_names, row, strict=False)) for row in rows_raw]

    def query_iceberg(self, sql: str) -> list[dict]:
        """Execute read-only SQL against Iceberg tables via DuckDB.

        This is the single choke point for MCP-client data access.  Two
        layers of read-only enforcement protect the host:

        1. ``_validate_read_only_sql`` rejects any statement whose first
           keyword is not in {SELECT, WITH, DESCRIBE, SHOW}, returning a
           structured ``[{"error": "…"}]`` without touching the query
           connection.
        2. ``SET enable_external_access=false`` (DuckDB 1.x pragma) is set on
           the cached connection (see ``_ensure_query_connection``), blocking
           file-system reads and writes even if a statement somehow passed
           the allowlist.

        Unlike round-1, the DuckDB connection is now a PERSISTENT,
        per-instance cache (M6) rather than opened and closed on every call —
        see ``_ensure_query_connection`` for the refresh design. It is closed
        via ``close_query_connection``/``__del__``, not a per-call ``finally``.

        Future RLS filters, entitlement checks, and audit logging inject here.
        """
        # --- Layer 1: allowlist validation (before touching the connection) ---
        rejection = _validate_read_only_sql(sql)
        if rejection is not None:
            logger.warning("query_iceberg rejected SQL: %s", rejection)
            return [{"error": rejection}]

        # --- Layer 2: cached, locked, read-only DuckDB connection ---
        with self._query_lock:
            con = self._ensure_query_connection()

            # Execution of the untrusted statement itself. A bad column / unknown
            # table / type error becomes a structured error result (matching the
            # allowlist-rejection shape) rather than raising out of the tool
            # handler. Genuine bugs surface because we catch only duckdb.Error.
            try:
                result = con.execute(sql).fetchall()
                columns_list = [desc[0] for desc in con.description]
            except duckdb.Error as e:
                logger.warning("query_iceberg execution failed: %s", e)
                return [{"error": f"query failed: {e}"}]

            return [dict(zip(columns_list, row, strict=False)) for row in result]

    # --- Governance metadata ---

    def attach_governance(self, result: dict, table_name: str) -> dict:
        """Attach governance metadata to a tool response.

        Every tool response includes: contract info, DQ status, lineage,
        and last update time. The LLM client can use this to calibrate confidence.
        """
        governance = {"table": table_name}

        try:
            from brightsmith.infra.contract import list_contracts
            for c in list_contracts():
                if c.get("table") == table_name:
                    governance["contract_version"] = c.get("version", "?")
                    governance["contract_status"] = c.get("status", "?")
                    break
        except Exception:
            # Best-effort governance enrichment on a tool response — absence of
            # contract metadata must not fail the response; logged, not silent.
            logger.debug("attach_governance: contract lookup failed for %s", table_name, exc_info=True)

        result["governance"] = governance
        return result

    def enrich_response(self, result: dict, table_name: str) -> dict:
        """Full response enrichment pipeline.

        1. Format values (if formatter configured)
        2. Flag anomalies (if anomaly checker configured)
        3. Attach governance metadata (always)

        This is the recommended wrapper for tool responses. Falls back to
        ``attach_governance()`` behavior when no intelligence layer components
        are configured.
        """
        # Step 1: Format values
        if self.formatter and "data" in result and isinstance(result["data"], list):
            result["data"] = self.formatter.format_rows(result["data"])

        # Step 2: Flag anomalies
        if self.anomaly_checker and "data" in result and isinstance(result["data"], list):
            result["data"] = self.anomaly_checker.check_rows(result["data"])

        # Step 3: Attach governance metadata (always)
        return self.attach_governance(result, table_name)

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
                    uri=r.uri,  # type: ignore[arg-type]  # mcp SDK wants AnyUrl; str is accepted at runtime
                    name=r.name,
                    description=r.description,
                    mimeType=r.mime_type,
                )
                for r in all_resources
            ]

        @server.read_resource()  # type: ignore[arg-type]  # mcp SDK handler is typed (AnyUrl)->...; str works at runtime
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
