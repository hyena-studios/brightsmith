"""Collision resolution for concept normalization.

When multiple source classifications map to the same canonical concept,
collision rules declare which is primary. This is domain-agnostic: it
handles ICD-10 codes, XBRL tags, product taxonomies, or any classification
system with synonyms or versioned codes.

Usage:
    from brightsmith.silver.concept_normalization.collision import CollisionResolver
    resolver = CollisionResolver()
    winner = resolver.resolve("Revenue", ["Revenues", "SalesRevenue", "RevenueNet"])
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CollisionRule:
    """A single collision resolution rule for a canonical concept."""

    canonical_concept: str
    primary_sources: list[str]
    primary_unit: str | None
    resolution_strategy: str
    rationale: str
    approved_by: str | None
    approved_at: str | None


class CollisionResolver:
    """Resolves collisions when multiple source codes map to the same concept.

    Loads rules from governance/concept-normalization/collision-rules.json.
    If no rules exist, returns the first source encountered (with a warning).
    """

    def __init__(self, rules_path: Path | None = None):
        from brightsmith.config import PROJECT_ROOT

        self._rules: dict[str, CollisionRule] = {}
        path = rules_path or PROJECT_ROOT / "governance" / "concept-normalization" / "collision-rules.json"

        if not path.exists():
            logger.info("No collision rules found at %s — first-encountered wins", path)
            return

        data = json.loads(path.read_text())
        for concept, rule_data in data.get("rules", {}).items():
            self._rules[concept] = CollisionRule(
                canonical_concept=concept,
                primary_sources=rule_data.get("primary_sources", []),
                primary_unit=rule_data.get("primary_unit"),
                resolution_strategy=rule_data.get("resolution_strategy", "prefer_primary_order"),
                rationale=rule_data.get("rationale", ""),
                approved_by=rule_data.get("approved_by"),
                approved_at=rule_data.get("approved_at"),
            )

        logger.info("Loaded %d collision rules from %s", len(self._rules), path)

    def resolve(self, canonical_concept: str, source_keys: list[str]) -> str | None:
        """Resolve which source key should be primary for a canonical concept.

        Args:
            canonical_concept: The normalized business concept name.
            source_keys: All source codes that mapped to this concept.

        Returns:
            The winning source key, or None if no sources provided.
        """
        if not source_keys:
            return None
        if len(source_keys) == 1:
            return source_keys[0]

        rule = self._rules.get(canonical_concept)
        if rule is None:
            logger.warning(
                "No collision rule for '%s' — using first encountered: %s",
                canonical_concept, source_keys[0],
            )
            return source_keys[0]

        # Find the first source key that matches primary_sources ordering
        for preferred in rule.primary_sources:
            if preferred in source_keys:
                return preferred

        # Fallback to first encountered
        logger.warning(
            "Collision rule for '%s' has primary_sources %s but none match provided keys %s",
            canonical_concept, rule.primary_sources, source_keys,
        )
        return source_keys[0]

    def get_rule(self, canonical_concept: str) -> CollisionRule | None:
        """Get the collision rule for a concept."""
        return self._rules.get(canonical_concept)

    def get_uncovered_concepts(self, multi_source_concepts: dict[str, list[str]]) -> list[str]:
        """Find concepts with multiple sources but no collision rule.

        Args:
            multi_source_concepts: {concept: [source_key_1, source_key_2, ...]}

        Returns:
            List of concepts that need collision rules.
        """
        return [
            concept for concept, sources in multi_source_concepts.items()
            if len(sources) > 1 and concept not in self._rules
        ]

    def get_unapproved_rules(self) -> list[CollisionRule]:
        """Get all rules that haven't been approved yet."""
        return [r for r in self._rules.values() if r.approved_by is None]
