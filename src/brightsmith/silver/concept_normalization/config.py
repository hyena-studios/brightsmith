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
    """
    try:
        from brightsmith.domain_loader import load_manifest
        manifest = load_manifest()
        return manifest.hints.concept_mappings
    except (FileNotFoundError, Exception) as e:
        logger.info("No domain manifest or concept_mappings hint: %s", e)
        return None
