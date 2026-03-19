"""Tests for business glossary schema validator."""

import json
from unittest.mock import patch

from grist.infra.glossary_validator import validate_glossary


def _make_valid_term(**overrides):
    """Create a valid term dict with all required fields."""
    term = {
        "term_id": "BT-001",
        "name": "Revenue",
        "definition": "Total income from operations",
        "source": "project-specific",
        "source_reference": "domain-context.md",
        "synonyms": [],
        "related_terms": [],
        "category": "measurement",
        "owner": "Data Governance",
        "used_in_models": [],
        "is_cde": False,
        "is_pii": False,
        "approval_status": "approved",
    }
    term.update(overrides)
    return term


def _write_glossary(tmp_path, terms):
    """Write a glossary file with the given terms."""
    path = tmp_path / "business-glossary.json"
    path.write_text(json.dumps({"glossary_metadata": {"version": "1.0"}, "terms": terms}))
    return path


def test_valid_glossary_passes(tmp_path):
    """A glossary with all required fields should pass validation."""
    path = _write_glossary(tmp_path, [_make_valid_term()])
    valid, issues = validate_glossary(path)
    assert valid is True
    assert issues == []


def test_missing_required_field_fails(tmp_path):
    """A term missing a required field should fail."""
    term = _make_valid_term()
    del term["source_reference"]
    path = _write_glossary(tmp_path, [term])
    valid, issues = validate_glossary(path)
    assert valid is False
    assert any("source_reference" in i for i in issues)


def test_invalid_related_term_ref_fails(tmp_path):
    """A related_terms entry that doesn't exist should fail."""
    term = _make_valid_term(related_terms=["BT-999"])
    path = _write_glossary(tmp_path, [term])
    valid, issues = validate_glossary(path)
    assert valid is False
    assert any("BT-999" in i for i in issues)


def test_cde_without_rationale_fails(tmp_path):
    """is_cde=True without cde_rationale should fail."""
    term = _make_valid_term(is_cde=True)
    path = _write_glossary(tmp_path, [term])
    valid, issues = validate_glossary(path)
    assert valid is False
    assert any("cde_rationale" in i for i in issues)


def test_pii_without_rationale_fails(tmp_path):
    """is_pii=True without pii_rationale should fail."""
    term = _make_valid_term(is_pii=True)
    path = _write_glossary(tmp_path, [term])
    valid, issues = validate_glossary(path)
    assert valid is False
    assert any("pii_rationale" in i for i in issues)


def test_cde_with_rationale_passes(tmp_path):
    """is_cde=True with cde_rationale should pass."""
    term = _make_valid_term(is_cde=True, cde_rationale="Required for regulatory reporting")
    path = _write_glossary(tmp_path, [term])
    valid, issues = validate_glossary(path)
    assert valid is True


def test_valid_related_term_passes(tmp_path):
    """related_terms referencing existing term IDs should pass."""
    terms = [
        _make_valid_term(term_id="BT-001", related_terms=["BT-002"]),
        _make_valid_term(term_id="BT-002", name="Net Income", related_terms=["BT-001"]),
    ]
    path = _write_glossary(tmp_path, terms)
    valid, issues = validate_glossary(path)
    assert valid is True


def test_missing_glossary_file_fails(tmp_path):
    """Non-existent glossary file should fail."""
    valid, issues = validate_glossary(tmp_path / "nonexistent.json")
    assert valid is False
    assert any("not found" in i for i in issues)
