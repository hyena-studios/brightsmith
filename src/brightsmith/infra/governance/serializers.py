"""Serialization and normalization helpers for governance records."""

from __future__ import annotations

CANONICAL_ZONES = {"bronze", "silver", "gold", "mcp"}


def normalize_zone(zone: str | None) -> str:
    """Return the zone name as a string (empty for falsy input).

    Brightsmith uses the physical medallion namespaces
    (bronze/silver/gold/mcp) everywhere; this is a null-safe accessor, not a
    translation layer. Any non-empty name is returned verbatim.
    """
    return zone or ""


def normalize_table_name(table_name: str | None) -> str:
    """Return a ``namespace.table`` reference as a string (empty for falsy input).

    No namespace translation is performed — the physical medallion namespace
    is the only namespace.
    """
    return table_name or ""
