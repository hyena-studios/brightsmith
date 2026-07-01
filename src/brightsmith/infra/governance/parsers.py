"""Mermaid erDiagram parsers for governance model backfill."""

from __future__ import annotations

import re

__all__ = ["_parse_mermaid_columns", "_parse_mermaid_erdiagram"]


def _parse_mermaid_erdiagram(markdown: str) -> dict | None:
    """Parse a Mermaid erDiagram block from markdown into a simple dict.

    Returns a dict with 'entities' and 'relationships' lists, or None if
    no erDiagram block is found.

    Entity format: {"name": str, "columns": list[dict]}
    Column format: {"column_name": str, "data_type": str, "is_pk": bool,
                    "is_fk": bool, "description": str|None, "source_mapping": str|None}
    Relationship format: {"relationship_id": str, "source_entity": str,
                          "target_entity": str, "source_cardinality": str|None,
                          "target_cardinality": str|None, "label": str|None}
    """
    # Extract erDiagram block
    block_match = re.search(r"```mermaid\s*\nerDiagram\s*\n(.*?)```", markdown, re.DOTALL)
    if not block_match:
        return None
    block = block_match.group(1).strip()

    entities: list[dict] = []
    entity_names: set[str] = set()

    # Parse entity blocks: entity_name { ... }
    entity_pattern = re.compile(r"(\w{2,})\s*\{([^}]*)\}", re.DOTALL)
    for m in entity_pattern.finditer(block):
        entity_name = m.group(1)
        body = m.group(2).strip()
        columns = _parse_mermaid_columns(body)
        entities.append({"name": entity_name, "columns": columns})
        entity_names.add(entity_name)

    relationships: list[dict] = []
    rel_pattern = re.compile(
        r"(\w+)\s+([|o{}]{2})(--|-\.)([|o{}]{2})\s+(\w+)\s*:\s*\"([^\"]*)\""
    )
    _cardinality_map = {
        "||": "1", "|o": "0..1", "o|": "0..1",
        "}|": "1..*", "|}": "1..*", "}o": "0..*",
        "o{": "0..*", "|{": "1..*",
    }
    for m in rel_pattern.finditer(block):
        source = m.group(1)
        left_card = _cardinality_map.get(m.group(2), m.group(2))
        right_card = _cardinality_map.get(m.group(4), m.group(4))
        target = m.group(5)
        label = m.group(6)

        # Add entities that only appear in relationships (conceptual models)
        if source not in entity_names:
            entities.append({"name": source, "columns": []})
            entity_names.add(source)
        if target not in entity_names:
            entities.append({"name": target, "columns": []})
            entity_names.add(target)

        rel_id = f"{source}__{target}"
        relationships.append({
            "relationship_id": rel_id,
            "source_entity": source,
            "target_entity": target,
            "source_cardinality": left_card,
            "target_cardinality": right_card,
            "label": label,
            "source_column": None,
            "target_column": None,
        })

    return {"entities": entities, "relationships": relationships}


def _parse_mermaid_columns(body: str) -> list[dict]:
    """Parse column lines from a Mermaid entity body.

    Handles: TYPE column_name [PK|FK] ["description | source_mapping"]
    """
    columns = []
    col_pattern = re.compile(
        r"(\w+)\s+(\w+)(?:\s+(PK|FK))?(?:\s+\"([^\"]*)\")?"
    )
    for i, line in enumerate(body.split("\n")):
        line = line.strip()
        if not line:
            continue
        m = col_pattern.match(line)
        if m:
            data_type = m.group(1)
            col_name = m.group(2)
            key_marker = m.group(3)
            desc_raw = m.group(4)

            description = None
            source_mapping = None
            if desc_raw:
                if "|" in desc_raw:
                    parts = desc_raw.split("|", 1)
                    description = parts[0].strip() or None
                    source_mapping = parts[1].strip() or None
                else:
                    description = desc_raw.strip() or None

            columns.append({
                "column_name": col_name,
                "data_type": data_type,
                "is_pk": key_marker == "PK",
                "is_fk": key_marker == "FK",
                "nullable": key_marker not in ("PK",),
                "description": description,
                "source_mapping": source_mapping,
                "ordinal_position": i,
            })
    return columns
