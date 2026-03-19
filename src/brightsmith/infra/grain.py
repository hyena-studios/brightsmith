"""Deterministic grain hashing for idempotent promotes.

Computes a deterministic ID from a row's business grain fields using
SHA-256. Same input always produces the same output — this is the
foundation of the idempotent promote pattern.

Usage:
    from brightsmith.infra.grain import compute_grain_id

    row = {"cik": 320193, "fy": 2024, "fp": "FY"}
    record_id = compute_grain_id(row, ["cik", "fy", "fp"], prefix="CF")
    # "CF-a3f2b8c1d4e5f607"
"""

from __future__ import annotations

import hashlib


def compute_grain_id(row: dict, grain_fields: list[str], prefix: str = "") -> str:
    """Compute a deterministic ID from a row's grain fields.

    The ID is the first 16 characters of the SHA-256 hex digest of the
    pipe-delimited grain values. Deterministic: same input → same output.

    Args:
        row: The data row.
        grain_fields: Ordered list of column names that define uniqueness.
        prefix: Optional prefix for readability (e.g., "FF" for financial facts).

    Returns:
        Deterministic hash string. With prefix: "FF-a3f2b8c1d4e5f607".
        Without prefix: "a3f2b8c1d4e5f607".
    """
    grain_values = "|".join(str(row.get(f, "")) for f in grain_fields)
    hash_hex = hashlib.sha256(grain_values.encode()).hexdigest()[:16]
    return f"{prefix}-{hash_hex}" if prefix else hash_hex
