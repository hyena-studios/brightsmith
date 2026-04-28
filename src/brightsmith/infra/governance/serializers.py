"""Serialization and normalization helpers for governance records."""

from __future__ import annotations

ZONE_ALIASES = {
    "raw": "bronze",
    "base": "silver",
    "consumable": "gold",
    "ai_ready": "mcp",
}
CANONICAL_ZONES = {"bronze", "silver", "gold", "mcp"}


def normalize_zone(zone: str | None) -> str:
    """Return the canonical medallion zone name."""
    if not zone:
        return ""
    return ZONE_ALIASES.get(zone, zone)


def normalize_table_name(table_name: str | None) -> str:
    """Normalize the namespace portion of a namespace.table reference."""
    if not table_name or "." not in table_name:
        return table_name or ""
    namespace, name = table_name.split(".", 1)
    return f"{normalize_zone(namespace)}.{name}"

