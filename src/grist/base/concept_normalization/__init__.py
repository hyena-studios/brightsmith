"""Generic concept normalization engine.

Loads concept → business term mappings from JSON config files.
Works with any taxonomy — XBRL, CPT codes, meter types, etc.
"""

from .normalize import ConceptNormalizer

__all__ = ["ConceptNormalizer"]
