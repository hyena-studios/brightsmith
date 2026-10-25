"""Configuration for concept normalization pipeline.

Reads mapping paths from the domain manifest.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_concept_mappings_dir() -> Path | None:
    """Get the concept mappings directory from the domain manifest.

    Returns None if the manifest doesn't exist or doesn't specify
    a concept_mappings path — the normalizer will operate in discovery mode.
    A manifest that exists but cannot be loaded raises: silently dropping to
    discovery mode would ignore the project's curated mappings.
    """
    from brightsmith.domain_loader import load_manifest

    try:
        manifest = load_manifest()
    except FileNotFoundError as e:
        logger.info("No domain manifest; concept normalization in discovery mode: %s", e)
        return None
    return manifest.hints.concept_mappings
