"""MCP server entry point.

Starts the Brightsmith MCP server in stdio mode. Discovers the domain
MCP server class from the manifest, or falls back to the framework
base server with generic tools.

Domain MCP servers are discovered from ``domain/manifest.yaml`` in two
possible shapes, tried in order (H1 / H5.2):

1. A top-level ``mcp: {module, class}`` block — the documented shape.
2. A ``pipeline.zones.mcp`` entry carrying ``module``/``class`` keys — the
   shape multi-source field manifests produce naturally (field evidence:
   futureproof-data's ``domain/manifest.yaml``).

If neither is present (or loading fails), the framework's generic
``BaseMCPServer`` is used. Every path logs which server was selected and why.

Usage:
    python -m brightsmith.serve
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _import_module_by_path(module_path: str):
    """Import a manifest ``module:`` value.

    Accepts either a dotted import path (``domain.mcp_server``, the top-level
    ``mcp:`` block's convention) or a filesystem path (``src/mcp_server/x.py``,
    the convention ``pipeline.zones.mcp`` entries use — same file-path support
    as ``run.py``'s zone step loader), resolved against ``PROJECT_ROOT``.
    """
    if module_path.endswith(".py") or "/" in module_path:
        from brightsmith.config import PROJECT_ROOT

        file_path = Path(module_path)
        if not file_path.is_absolute():
            file_path = PROJECT_ROOT / file_path
        if not file_path.exists():
            raise ImportError(f"MCP server module file not found: {file_path}")
        spec = importlib.util.spec_from_file_location(
            f"_brightsmith_mcp_server__{file_path.stem}", file_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load MCP server module from file: {file_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return importlib.import_module(module_path)


def _find_pipeline_mcp_zone(pipeline: dict | None) -> dict | None:
    """Find a ``class:``-based MCP server entry under ``pipeline.zones.mcp``.

    This is the shape field manifests use (see H5.2): a list of step dicts
    under ``pipeline.zones.mcp``, one of which names the server ``module``
    and ``class``. Returns the first entry carrying both keys, or ``None``.
    """
    if not isinstance(pipeline, dict):
        return None
    zones = pipeline.get("zones")
    if not isinstance(zones, dict):
        return None
    mcp_entries = zones.get("mcp")
    if isinstance(mcp_entries, dict):
        mcp_entries = [mcp_entries]
    if not isinstance(mcp_entries, list):
        return None
    for entry in mcp_entries:
        if isinstance(entry, dict) and entry.get("module") and entry.get("class"):
            return entry
    return None


def _try_load_domain_server(
    config: dict | None,
    source: str,
    warehouse_path,
    catalog_path,
    grounding_docs_path,
):
    """Attempt to instantiate a domain MCP server from a ``{module, class}`` config.

    Returns ``None`` (no log) when ``config`` is absent or incomplete — that's
    a normal "this shape wasn't used" outcome, not an error. Returns ``None``
    with a logged warning when the config IS present but loading fails (bad
    module path, missing class, constructor error, …) — a genuine problem the
    operator should see, per audit finding H1.
    """
    if not config:
        return None
    module_path = config.get("module", "")
    class_name = config.get("class", "")
    if not module_path or not class_name:
        return None

    try:
        mod = _import_module_by_path(module_path)
        cls = getattr(mod, class_name)
        server = cls(
            warehouse_path=warehouse_path,
            catalog_path=catalog_path,
            grounding_docs_path=grounding_docs_path,
        )
    except Exception:
        # Domain-specific server couldn't be loaded from this config — the
        # caller falls back to the next source (or the base server). Logged
        # as a warning because config WAS present: this is a genuine failure,
        # never silent.
        logger.warning(
            "Could not load domain MCP server '%s' from %s (module '%s'); trying next fallback",
            class_name,
            source,
            module_path,
            exc_info=True,
        )
        return None

    logger.info(
        "Loaded domain MCP server '%s' from %s (module '%s')", class_name, source, module_path
    )
    return server


def _make_base_server(warehouse_path, catalog_path, grounding_docs_path):
    from brightsmith.mcp.base_mcp_server import BaseMCPServer

    return BaseMCPServer(
        warehouse_path=warehouse_path,
        catalog_path=catalog_path,
        grounding_docs_path=grounding_docs_path,
    )


def _load_server():
    """Load the MCP server instance.

    Tries, in order: the top-level ``mcp:`` manifest block, a
    ``pipeline.zones.mcp`` entry, then the framework base server. Every
    outcome is logged with which server was selected and why (H1).
    """
    from brightsmith.config import CATALOG_PATH, PROJECT_ROOT, WAREHOUSE_PATH

    grounding_path = PROJECT_ROOT / "data" / "mcp" / "grounding"
    grounding_docs_path = grounding_path if grounding_path.exists() else None

    from brightsmith.domain_loader import load_manifest

    try:
        manifest = load_manifest()
    except FileNotFoundError:
        logger.info("No domain manifest found; using framework base MCP server")
        return _make_base_server(WAREHOUSE_PATH, CATALOG_PATH, grounding_docs_path)

    server = _try_load_domain_server(
        manifest.mcp,
        "top-level 'mcp:' manifest block",
        WAREHOUSE_PATH,
        CATALOG_PATH,
        grounding_docs_path,
    )
    if server is not None:
        return server

    mcp_zone_entry = _find_pipeline_mcp_zone(manifest.pipeline)
    server = _try_load_domain_server(
        mcp_zone_entry,
        "'pipeline.zones.mcp' manifest entry",
        WAREHOUSE_PATH,
        CATALOG_PATH,
        grounding_docs_path,
    )
    if server is not None:
        return server

    logger.info("No domain MCP server configured in manifest; using framework base MCP server")
    return _make_base_server(WAREHOUSE_PATH, CATALOG_PATH, grounding_docs_path)


def main() -> None:
    """Start the MCP server."""
    server = _load_server()
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
