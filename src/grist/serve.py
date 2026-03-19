"""MCP server entry point.

Starts the Grist MCP server in stdio mode. Discovers the domain
MCP server class from the manifest, or falls back to the framework
base server with generic tools.

Usage:
    python -m grist.serve
"""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


def _load_server():
    """Load the MCP server instance.

    Tries to load a domain-specific server from the manifest,
    falls back to the framework base server.
    """
    from grist.config import CATALOG_PATH, PROJECT_ROOT, WAREHOUSE_PATH

    grounding_path = PROJECT_ROOT / "data" / "ai_ready" / "grounding"

    # Try to load domain-specific server from manifest
    try:
        from grist.domain_loader import load_manifest
        manifest = load_manifest()
        mcp_config = getattr(manifest, "mcp", None)
        if mcp_config:
            import importlib
            module_path = mcp_config.get("module", "")
            class_name = mcp_config.get("class", "")
            if module_path and class_name:
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                return cls(
                    warehouse_path=WAREHOUSE_PATH,
                    catalog_path=CATALOG_PATH,
                    grounding_docs_path=grounding_path if grounding_path.exists() else None,
                )
    except Exception:
        pass

    # Fall back to base server
    from grist.ai_ready.base_mcp_server import BaseMCPServer
    return BaseMCPServer(
        warehouse_path=WAREHOUSE_PATH,
        catalog_path=CATALOG_PATH,
        grounding_docs_path=grounding_path if grounding_path.exists() else None,
    )


def main() -> None:
    """Start the MCP server."""
    server = _load_server()
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
