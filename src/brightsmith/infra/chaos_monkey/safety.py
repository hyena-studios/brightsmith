"""Three-layer safety gate for chaos monkey operations.

All three conditions must be true before any corruption is injected:
1. CHAOS_MONKEY_ENABLED=True (environment variable)
2. GRIST_ENV=dev (environment variable)
3. Target namespace starts with 'shadow_' prefix

This is non-negotiable. No bypass, no override, no "just this once."
"""

from __future__ import annotations

import os

SHADOW_PREFIX = "shadow_"


class SafetyViolation(Exception):
    """Raised when chaos monkey safety checks fail."""


class SafetyGate:
    """Three-layer kill switch for chaos monkey operations."""

    @staticmethod
    def check(target_namespace: str) -> None:
        """Verify all three safety conditions. Raises SafetyViolation on failure."""
        errors = []

        if os.environ.get("CHAOS_MONKEY_ENABLED", "").lower() != "true":
            errors.append("CHAOS_MONKEY_ENABLED is not 'true'")

        if os.environ.get("GRIST_ENV", "").lower() != "dev":
            errors.append("GRIST_ENV is not 'dev'")

        if not target_namespace.startswith(SHADOW_PREFIX):
            errors.append(
                f"Target namespace '{target_namespace}' does not start with '{SHADOW_PREFIX}'"
            )

        if errors:
            raise SafetyViolation(
                "Chaos monkey safety check FAILED:\n  - " + "\n  - ".join(errors)
            )

    @staticmethod
    def is_safe(target_namespace: str) -> bool:
        """Check safety without raising. Returns True if all conditions met."""
        try:
            SafetyGate.check(target_namespace)
            return True
        except SafetyViolation:
            return False
