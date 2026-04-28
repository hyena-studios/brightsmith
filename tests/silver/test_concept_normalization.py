"""Tests for generic concept normalization engine."""

import json

from brightsmith.silver.concept_normalization.normalize import ConceptNormalizer, NormalizationResult


def test_normalizer_loads_from_json(tmp_path):
    """ConceptNormalizer should load mappings from JSON files."""
    mappings = {
        "mapping_metadata": {"name": "test-mappings", "taxonomy": "test"},
        "business_terms": {
            "BT-001": {"name": "Test Term", "financial_statement": "balance_sheet", "category": "test"},
        },
        "exact_mappings": {
            "TestConcept": ["BT-001", "balance_sheet", "test"],
        },
        "prefix_rules": [],
        "pattern_rules": [],
        "heuristic_categories": {},
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    normalizer = ConceptNormalizer(tmp_path)
    result = normalizer.classify("TestConcept")
    assert isinstance(result, NormalizationResult)
    assert result.tier == 1
    assert result.business_term_id == "BT-001"
    assert result.canonical_concept == "Test Term"
    assert result.source_mapping == "test-mappings"
    assert result.confidence == 1.0
    assert result.match_method == "exact_match"
    assert result.matched_rule == "TestConcept"
    assert result.requires_approval is False


def test_normalizer_discovery_mode_no_dir():
    """When mappings_dir is None, all concepts are unmapped."""
    normalizer = ConceptNormalizer(None)
    result = normalizer.classify("AnyConcept")
    assert result.tier == 0
    assert result.confidence == 0.0
    assert result.match_method == "unmapped"
    assert result.source_mapping is None
    assert result.source_key == "AnyConcept"


def test_normalizer_discovery_mode_missing_dir(tmp_path):
    """When mappings_dir doesn't exist, all concepts are unmapped."""
    normalizer = ConceptNormalizer(tmp_path / "nonexistent")
    result = normalizer.classify("AnyConcept")
    assert result.tier == 0
    assert result.confidence == 0.0


def test_normalizer_discovery_mode_empty_dir(tmp_path):
    """When mappings_dir exists but has no JSON files, all concepts are unmapped."""
    normalizer = ConceptNormalizer(tmp_path)
    result = normalizer.classify("AnyConcept")
    assert result.tier == 0
    assert result.confidence == 0.0


def test_normalizer_tracks_unmapped_concepts():
    """get_unmapped_concepts should return all concepts classified as unmapped."""
    normalizer = ConceptNormalizer(None)
    normalizer.classify("Foo")
    normalizer.classify("Bar")
    assert normalizer.get_unmapped_concepts() == ["Foo", "Bar"]


def test_normalizer_mapping_coverage():
    """get_mapping_coverage should return classify counts."""
    normalizer = ConceptNormalizer(None)
    normalizer.classify("Foo")
    normalizer.classify("Bar")
    coverage = normalizer.get_mapping_coverage()
    assert coverage["total"] == 2
    assert coverage["unmapped"] == 2


def test_normalizer_prefix_match(tmp_path):
    """Prefix rules should match concepts starting with the prefix."""
    mappings = {
        "mapping_metadata": {"name": "test"},
        "business_terms": {"BT-001": {"name": "Revenue", "financial_statement": "is", "category": "rev"}},
        "exact_mappings": {},
        "prefix_rules": [
            {"prefix": "Revenue", "business_term_id": "BT-001", "financial_statement": "income_statement", "category": "revenue"},
        ],
        "pattern_rules": [],
        "heuristic_categories": {},
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    normalizer = ConceptNormalizer(tmp_path)
    result = normalizer.classify("RevenueFromContractWithCustomer")
    assert result.tier == 2
    assert result.confidence == 0.7
    assert result.match_method == "prefix_match"
    assert result.matched_rule == "Revenue"


def test_normalizer_pattern_match(tmp_path):
    """Pattern rules should match concepts via regex."""
    mappings = {
        "mapping_metadata": {"name": "test"},
        "business_terms": {"BT-001": {"name": "Net Income", "financial_statement": "is", "category": "ni"}},
        "exact_mappings": {},
        "prefix_rules": [],
        "pattern_rules": [
            {"pattern": "(?i).*NetIncome.*", "business_term_id": "BT-001", "financial_statement": "income_statement", "category": "net_income"},
        ],
        "heuristic_categories": {},
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    normalizer = ConceptNormalizer(tmp_path)
    result = normalizer.classify("ConsolidatedNetIncomeLoss")
    assert result.tier == 3
    assert result.confidence == 0.6
    assert result.match_method == "pattern_match"
    assert result.requires_approval is True  # 0.6 < 0.7 floor


def test_normalizer_heuristic_category(tmp_path):
    """Heuristic categories should assign financial_statement and category."""
    mappings = {
        "mapping_metadata": {"name": "test"},
        "business_terms": {},
        "exact_mappings": {"SomeExact": ["BT-001", "x", "y"]},
        "prefix_rules": [],
        "pattern_rules": [],
        "heuristic_categories": {
            "Debt": {"financial_statement": "balance_sheet", "category": "debt"},
        },
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    normalizer = ConceptNormalizer(tmp_path)
    result = normalizer.classify("LongTermDebtMaturity")
    assert result.tier == 4
    assert result.confidence == 0.3
    assert result.match_method == "heuristic"
    assert result.financial_statement == "balance_sheet"
    assert result.category == "debt"
    assert result.requires_approval is True  # 0.3 < 0.7 floor


def test_normalizer_multiple_files(tmp_path):
    """ConceptNormalizer should load from multiple JSON files."""
    m1 = {
        "mapping_metadata": {"name": "source-a"},
        "business_terms": {"BT-001": {"name": "TermA"}},
        "exact_mappings": {"ConceptA": ["BT-001", "stmt_a", "cat_a"]},
        "prefix_rules": [], "pattern_rules": [], "heuristic_categories": {},
    }
    m2 = {
        "mapping_metadata": {"name": "source-b"},
        "business_terms": {"BT-002": {"name": "TermB"}},
        "exact_mappings": {"ConceptB": ["BT-002", "stmt_b", "cat_b"]},
        "prefix_rules": [], "pattern_rules": [], "heuristic_categories": {},
    }
    (tmp_path / "a.json").write_text(json.dumps(m1))
    (tmp_path / "b.json").write_text(json.dumps(m2))

    normalizer = ConceptNormalizer(tmp_path)
    assert normalizer.classify("ConceptA").business_term_id == "BT-001"
    assert normalizer.classify("ConceptB").business_term_id == "BT-002"


# --- New tests for confidence scoring (Change 1) ---


def test_exact_match_returns_confidence_1_0(tmp_path):
    """Tier 1 exact match should always return confidence 1.0."""
    mappings = {
        "mapping_metadata": {"name": "test"},
        "business_terms": {"BT-001": {"name": "Revenue"}},
        "exact_mappings": {"Revenues": ["BT-001", "is", "revenue"]},
        "prefix_rules": [], "pattern_rules": [], "heuristic_categories": {},
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    result = ConceptNormalizer(tmp_path).classify("Revenues")
    assert result.confidence == 1.0
    assert result.tier == 1
    assert result.requires_approval is False


def test_prefix_match_returns_confidence_0_7(tmp_path):
    """Tier 2 prefix match should return confidence 0.7."""
    mappings = {
        "mapping_metadata": {"name": "test"},
        "business_terms": {"BT-001": {"name": "Revenue"}},
        "exact_mappings": {},
        "prefix_rules": [
            {"prefix": "Revenue", "business_term_id": "BT-001", "financial_statement": "is", "category": "rev"},
        ],
        "pattern_rules": [], "heuristic_categories": {},
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    result = ConceptNormalizer(tmp_path).classify("RevenueFromSomething")
    assert result.confidence == 0.7
    assert result.tier == 2


def test_unmapped_returns_confidence_0_0():
    """Unmapped concepts should return confidence 0.0."""
    result = ConceptNormalizer(None).classify("Unknown")
    assert result.confidence == 0.0
    assert result.tier == 0


def test_below_floor_requires_approval(tmp_path):
    """Mappings below CONFIDENCE_FLOOR should set requires_approval=True."""
    mappings = {
        "mapping_metadata": {"name": "test"},
        "business_terms": {"BT-001": {"name": "Net Income"}},
        "exact_mappings": {},
        "prefix_rules": [],
        "pattern_rules": [
            {"pattern": ".*NetIncome.*", "business_term_id": "BT-001", "financial_statement": "is", "category": "ni"},
        ],
        "heuristic_categories": {},
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    result = ConceptNormalizer(tmp_path).classify("NetIncomeLoss")
    assert result.confidence == 0.6
    assert result.requires_approval is True  # 0.6 < default floor 0.7


def test_result_dataclass_fields_complete(tmp_path):
    """NormalizationResult should have all required fields populated."""
    mappings = {
        "mapping_metadata": {"name": "test"},
        "business_terms": {"BT-001": {"name": "Revenue"}},
        "exact_mappings": {"Revenues": ["BT-001", "is", "revenue"]},
        "prefix_rules": [], "pattern_rules": [], "heuristic_categories": {},
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    result = ConceptNormalizer(tmp_path).classify("Revenues")
    assert result.source_key == "Revenues"
    assert result.canonical_concept == "Revenue"
    assert result.business_term_id == "BT-001"
    assert result.financial_statement == "is"
    assert result.category == "revenue"
    assert result.tier == 1
    assert result.confidence == 1.0
    assert result.match_method == "exact_match"
    assert result.matched_rule == "Revenues"
    assert result.source_mapping == "test"
    assert result.requires_approval is False


def test_to_dict_backward_compatible(tmp_path):
    """to_dict() should return a dict compatible with the old format."""
    mappings = {
        "mapping_metadata": {"name": "test"},
        "business_terms": {"BT-001": {"name": "Revenue"}},
        "exact_mappings": {"Revenues": ["BT-001", "is", "revenue"]},
        "prefix_rules": [], "pattern_rules": [], "heuristic_categories": {},
    }
    (tmp_path / "test.json").write_text(json.dumps(mappings))

    result = ConceptNormalizer(tmp_path).classify("Revenues")
    d = result.to_dict()
    assert d["business_term_id"] == "BT-001"
    assert d["business_term"] == "Revenue"
    assert d["tier"] == 1
    assert d["confidence"] == 1.0
    assert d["mapping_method"] == "exact_match"
