"""Generic tiered concept normalization engine.

Loads concept → business term mappings from JSON config files.
Works with any taxonomy — XBRL, CPT codes, meter types, etc.

Usage:
    from grist.base.concept_normalization import ConceptNormalizer
    normalizer = ConceptNormalizer(mappings_dir)
    result = normalizer.classify("RevenueFromContractWithCustomer")
    print(result.confidence)  # 0.7
    print(result.requires_approval)  # False (at floor)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class NormalizationResult:
    """Structured result from concept normalization.

    Every normalization call returns this instead of a bare dict.
    Confidence scores drive approval gates, DQ rules, and manual review.
    """

    source_key: str
    """The raw classification code/tag/identifier that was classified."""

    canonical_concept: str | None
    """The business term name this maps to, or None if unmapped."""

    business_term_id: str | None
    """Business glossary term ID (e.g., BT-024), or None if unmapped."""

    financial_statement: str | None
    """Which category/group this belongs to (domain-agnostic name kept for compatibility)."""

    category: str | None
    """Sub-classification within the statement/group."""

    tier: int
    """Match tier: 1=exact, 2=prefix, 3=pattern, 4=heuristic, 0=unmapped."""

    confidence: float
    """Confidence score: 1.0 (exact), 0.7 (prefix), 0.6 (pattern), 0.3 (heuristic), 0.0 (unmapped)."""

    match_method: str
    """How the match was made: exact_match, prefix_match, pattern_match, heuristic, unmapped."""

    matched_rule: str | None
    """Which specific rule triggered the match (e.g., prefix string, pattern regex)."""

    source_mapping: str | None
    """Which mapping file produced this result."""

    requires_approval: bool
    """True if confidence < CONFIDENCE_FLOOR and human approval is needed."""

    def to_dict(self) -> dict:
        """Convert to dict for backward compatibility and serialization."""
        return {
            "source_key": self.source_key,
            "business_term_id": self.business_term_id,
            "business_term": self.canonical_concept,
            "financial_statement": self.financial_statement,
            "category": self.category,
            "tier": self.tier,
            "confidence": self.confidence,
            "mapping_method": self.match_method,
            "matched_rule": self.matched_rule,
            "source_mapping": self.source_mapping,
            "requires_approval": self.requires_approval,
        }


class ConceptNormalizer:
    """Generic tiered concept normalization engine.

    Loads concept → business term mappings from JSON config files.
    Works with any taxonomy — XBRL, CPT codes, meter types, etc.
    """

    def __init__(self, mappings_dir: Path | None = None):
        """Load mappings from all JSON files in mappings_dir.

        If mappings_dir is None or doesn't exist, operates in
        discovery mode — all concepts return as unmapped.
        """
        self._business_terms: dict[str, dict] = {}
        self._exact_mappings: dict[str, tuple[str, str, str]] = {}
        self._prefix_rules: list[tuple[str, str, str, str]] = []
        self._pattern_rules: list[tuple[str, str, str, str]] = []
        self._heuristic_categories: list[tuple[str, str, str]] = []
        self._source_mappings: list[str] = []
        self._unmapped_concepts: list[str] = []
        self._classify_counts: dict[str, int] = {
            "total": 0, "tier_1": 0, "tier_2_prefix": 0,
            "tier_2_pattern": 0, "tier_3": 0, "unmapped": 0,
        }

        if mappings_dir is None or not Path(mappings_dir).exists():
            if mappings_dir is not None:
                logger.info(
                    "No concept mappings found at %s. "
                    "Operating in discovery mode — all concepts will be unmapped.",
                    mappings_dir,
                )
            else:
                logger.info(
                    "No concept mappings found. "
                    "Operating in discovery mode — all concepts will be unmapped.",
                )
            return

        mappings_path = Path(mappings_dir)
        json_files = sorted(mappings_path.glob("*.json"))

        if not json_files:
            logger.info(
                "No concept mappings found in %s. "
                "Operating in discovery mode — all concepts will be unmapped.",
                mappings_dir,
            )
            return

        for json_file in json_files:
            self._load_mapping_file(json_file)

        logger.info(
            "Loaded concept mappings: %d exact, %d prefix, %d pattern, %d heuristic from %s",
            len(self._exact_mappings),
            len(self._prefix_rules),
            len(self._pattern_rules),
            len(self._heuristic_categories),
            [s for s in self._source_mappings],
        )

    def _load_mapping_file(self, path: Path) -> None:
        """Load a single mapping JSON file."""
        with open(path) as f:
            data = json.load(f)

        metadata = data.get("mapping_metadata", {})
        source_name = metadata.get("name", path.stem)
        self._source_mappings.append(source_name)

        for bt_id, bt_data in data.get("business_terms", {}).items():
            self._business_terms[bt_id] = bt_data

        for concept, mapping in data.get("exact_mappings", {}).items():
            self._exact_mappings[concept] = (mapping[0], mapping[1], mapping[2])

        for rule in data.get("prefix_rules", []):
            self._prefix_rules.append((
                rule["prefix"],
                rule["business_term_id"],
                rule["financial_statement"],
                rule["category"],
            ))

        for rule in data.get("pattern_rules", []):
            self._pattern_rules.append((
                rule["pattern"],
                rule["business_term_id"],
                rule["financial_statement"],
                rule["category"],
            ))

        for substring, heuristic in data.get("heuristic_categories", {}).items():
            self._heuristic_categories.append((
                substring,
                heuristic["financial_statement"],
                heuristic["category"],
            ))

    def classify(self, concept: str) -> NormalizationResult:
        """Classify a concept through the tier hierarchy.

        Args:
            concept: The raw classification code/tag to normalize.

        Returns:
            NormalizationResult with confidence score, tier, and approval flag.
            Use result.to_dict() for backward-compatible dict format.
        """
        from grist.config import CONFIDENCE_FLOOR

        self._classify_counts["total"] += 1
        source = self._source_mappings[0] if self._source_mappings else None

        # No mappings loaded → discovery mode
        if not self._exact_mappings and not self._prefix_rules and not self._pattern_rules:
            self._classify_counts["unmapped"] += 1
            self._unmapped_concepts.append(concept)
            return NormalizationResult(
                source_key=concept,
                canonical_concept=None,
                business_term_id=None,
                financial_statement=None,
                category=None,
                tier=0,
                confidence=0.0,
                match_method="unmapped",
                matched_rule=None,
                source_mapping=None,
                requires_approval=False,
            )

        # Tier 1: Exact match (confidence 1.0)
        if concept in self._exact_mappings:
            business_term_id, stmt, cat = self._exact_mappings[concept]
            bt_name = self._business_terms.get(business_term_id, {}).get("name")
            self._classify_counts["tier_1"] += 1
            return NormalizationResult(
                source_key=concept,
                canonical_concept=bt_name,
                business_term_id=business_term_id,
                financial_statement=stmt,
                category=cat,
                tier=1,
                confidence=1.0,
                match_method="exact_match",
                matched_rule=concept,
                source_mapping=source,
                requires_approval=False,
            )

        # Tier 2: Prefix match (confidence 0.7)
        for prefix, business_term_id, stmt, cat in self._prefix_rules:
            if concept.startswith(prefix):
                bt_name = self._business_terms.get(business_term_id, {}).get("name")
                self._classify_counts["tier_2_prefix"] += 1
                return NormalizationResult(
                    source_key=concept,
                    canonical_concept=bt_name,
                    business_term_id=business_term_id,
                    financial_statement=stmt,
                    category=cat,
                    tier=2,
                    confidence=0.7,
                    match_method="prefix_match",
                    matched_rule=prefix,
                    source_mapping=source,
                    requires_approval=0.7 < CONFIDENCE_FLOOR,
                )

        # Tier 3: Pattern match (confidence 0.6)
        for pattern, business_term_id, stmt, cat in self._pattern_rules:
            if re.match(pattern, concept):
                bt_name = self._business_terms.get(business_term_id, {}).get("name")
                self._classify_counts["tier_2_pattern"] += 1
                return NormalizationResult(
                    source_key=concept,
                    canonical_concept=bt_name,
                    business_term_id=business_term_id,
                    financial_statement=stmt,
                    category=cat,
                    tier=3,
                    confidence=0.6,
                    match_method="pattern_match",
                    matched_rule=pattern,
                    source_mapping=source,
                    requires_approval=0.6 < CONFIDENCE_FLOOR,
                )

        # Tier 4: Heuristic category fallback (confidence 0.3)
        stmt, cat = self._heuristic_category(concept)
        if stmt != "other":
            self._classify_counts["tier_3"] += 1
            return NormalizationResult(
                source_key=concept,
                canonical_concept=None,
                business_term_id=None,
                financial_statement=stmt,
                category=cat,
                tier=4,
                confidence=0.3,
                match_method="heuristic",
                matched_rule=None,
                source_mapping=source,
                requires_approval=0.3 < CONFIDENCE_FLOOR,
            )

        # Tier 0: Truly unmapped
        self._classify_counts["unmapped"] += 1
        self._unmapped_concepts.append(concept)
        return NormalizationResult(
            source_key=concept,
            canonical_concept=None,
            business_term_id=None,
            financial_statement=stmt,
            category=cat,
            tier=0,
            confidence=0.0,
            match_method="unmapped",
            matched_rule=None,
            source_mapping=source,
            requires_approval=False,
        )

    def _heuristic_category(self, concept: str) -> tuple[str, str]:
        """Assign a heuristic category based on substrings."""
        for substring, stmt, cat in self._heuristic_categories:
            if substring in concept:
                return stmt, cat
        return "other", "uncategorized"

    def get_unmapped_concepts(self) -> list[str]:
        """Return all concepts that have been classified as unmapped."""
        return list(self._unmapped_concepts)

    def get_mapping_coverage(self) -> dict:
        """Return mapping coverage stats."""
        return dict(self._classify_counts)
