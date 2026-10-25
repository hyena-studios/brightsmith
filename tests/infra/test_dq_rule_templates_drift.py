"""Drift guard: the packaged DQ rule template copy must never diverge from
the canonical governance/ copy (W7b — docs/technical-audit-2026-07-02.md).

Two copies of the mandatory gold-zone DQ rule templates exist by design (see
governance/dq-rule-templates/README.md): `governance/dq-rule-templates/` is
the human-edited canonical copy referenced by this repo's own docs/specs, and
`src/brightsmith/_templates/dq-rule-templates/` is the copy that actually
ships inside the pip-installable wheel (audit finding H4a —
`governance/dq-rule-templates/` is a sibling of `src/`, so it is invisible to
a `pip install` consumer; `brightsmith.setup._copy_dq_templates` reads from
the packaged copy only). Nothing else enforces that an edit to one gets
mirrored to the other — this test fails loudly the moment they drift, rather
than letting a wheel-installed consumer silently receive stale templates.

Scope is deliberately `*.json` only: the two directories' README.md files
describe their own role/location and differ on purpose (one says "packaged
copy", the other says "source of truth") — that's not drift, it's content
correctly describing where it lives.
"""

from __future__ import annotations

from pathlib import Path

# tests/infra/this_file -> repo root is parents[2].
REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DIR = REPO_ROOT / "governance" / "dq-rule-templates"
PACKAGED_DIR = REPO_ROOT / "src" / "brightsmith" / "_templates" / "dq-rule-templates"


def test_packaged_templates_are_byte_identical_to_canonical():
    """Same filenames, same bytes, on both sides — or fail with the exact
    filenames that drifted, not just a generic "they differ"."""
    canonical_json = sorted(p.name for p in CANONICAL_DIR.glob("*.json"))
    packaged_json = sorted(p.name for p in PACKAGED_DIR.glob("*.json"))

    assert canonical_json, f"no *.json templates found under {CANONICAL_DIR}"
    assert canonical_json == packaged_json, (
        "packaged and canonical DQ rule template filenames differ — "
        f"canonical={canonical_json}, packaged={packaged_json}"
    )

    mismatched = [
        name
        for name in canonical_json
        if (CANONICAL_DIR / name).read_bytes() != (PACKAGED_DIR / name).read_bytes()
    ]

    assert not mismatched, (
        f"DQ rule templates have drifted between {CANONICAL_DIR} and "
        f"{PACKAGED_DIR}: {mismatched}. Sync the packaged copy (or script the "
        "sync) so wheel-installed consumers get the same templates as a "
        "source checkout."
    )
