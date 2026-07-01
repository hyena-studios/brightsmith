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

    Raises:
        ValueError: if a grain field KEY is absent from ``row``. A missing key
            (e.g. a typo'd field name) would otherwise silently collapse distinct
            rows into one hash — data loss via dedup. A key that is present but
            ``None`` is allowed and hashes deterministically as the string "None".
    """
    parts = []
    for f in grain_fields:
        if f not in row:
            raise ValueError(
                f"Grain field {f!r} is absent from the row "
                f"(available keys: {sorted(row)}). A missing grain field would "
                f"silently collapse distinct rows into one hash. If the value is "
                f"genuinely unknown, set it to None explicitly."
            )
        # Escape the delimiter so distinct tuples can never collide:
        # ("a|b", "c") must differ from ("a", "b|c").
        parts.append(str(row[f]).replace("|", "\\|"))
    grain_values = "|".join(parts)
    hash_hex = hashlib.sha256(grain_values.encode()).hexdigest()[:16]
    return f"{prefix}-{hash_hex}" if prefix else hash_hex
