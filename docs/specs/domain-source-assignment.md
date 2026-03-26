# Spec: Domain-Source Assignment

**Status:** COMPLETE
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-25

## Problem Statement

Brightforge's multi-domain view (Spec 16) displays a three-level sidebar hierarchy: **Domain > Source > Zones**. It reads `domain.name` and `sources[0]` from `manifest.yaml` to populate this hierarchy. When `domain.name` is missing, the sidebar falls back to showing the project name flat — losing the domain context that helps users understand what their data represents.

Currently, `manifest.yaml` has no `domain` section. The business domain is identified by `@domain-context` during pipeline execution and written to `governance/domain-context.md`, but this knowledge is never propagated back to the manifest. Brightforge can't read `governance/domain-context.md` — it reads `manifest.yaml` because that's the lightweight config file it parses on startup.

The fix: `@domain-context` should write the identified domain name back to `manifest.yaml` as `domain.name` after synthesizing domain knowledge. This closes the loop between Brightsmith's domain discovery and Brightforge's UI display.

## How It Works

```
@data-analyst → EDA report (domain discovery)
        │
        ▼
@domain-context → reads EDA, identifies domain
        │
        ├──▶ governance/domain-context.md  (existing — detailed domain knowledge)
        │
        └──▶ domain/manifest.yaml          (NEW — writes domain.name back)
                │
                ▼
        Brightforge reads manifest.yaml on startup
                │
                ▼
        Sidebar: "Financial Reporting > SEC EDGAR > Bronze/Silver/Gold/MCP"
```

## What Brightforge Expects

Brightforge's `detect_brightsmith_project()` in `backend/app/config.py` reads:

```python
domain_section = data.get("domain", {})
business_domain = None
if isinstance(domain_section, dict):
    business_domain = domain_section.get("name")
```

So `manifest.yaml` needs:

```yaml
domain:
  name: "Financial Reporting"  # Set by @domain-context agent
```

Brightforge has a fallback — if `domain.name` is absent, `businessDomain` is `null` and the sidebar skips that level. This is backward compatible.

## Success Criteria

- [ ] `@domain-context` writes `domain.name` to `manifest.yaml` after synthesizing domain knowledge
- [ ] Domain name matches what's in `governance/domain-context.md` "Domain Identification" section
- [ ] Existing manifest fields (name, version, description, sources, hints) are preserved when writing
- [ ] `DomainManifest` dataclass includes optional `domain_name` field
- [ ] `load_manifest()` reads `domain.name` if present
- [ ] `manifest.yaml.example` updated to show the `domain` section
- [ ] Brightforge displays the domain name in the sidebar hierarchy when present
- [ ] Projects without `domain.name` continue to work (backward compatible)
- [ ] All new code has tests

## Technical Design

### 1. Manifest Schema Extension

**File:** `domain/manifest.yaml`

Add an optional `domain` section:

```yaml
name: sec-edgar-financials
version: "1.0"
description: "SEC EDGAR company financial facts"

domain:
  name: "Financial Reporting"        # Set by @domain-context agent
  sub_domain: "SEC XBRL Filings"    # Optional, more specific classification
  confidence: "High"                 # How confident the agent was
  assigned_by: "@domain-context"     # Which agent assigned this
  assigned_at: "2026-03-25"          # When it was assigned

sources:
  - name: sec_edgar
    source_config: domain/sources/sec_edgar.yaml

hints:
  entity_id_field: cik
  time_field: filed
```

The `domain` section is entirely optional. Only `domain.name` is required for Brightforge display — the rest (`sub_domain`, `confidence`, `assigned_by`, `assigned_at`) is governance metadata.

### 2. Domain Loader Extension

**File:** `src/brightsmith/domain_loader.py`

Add `domain_name` to `DomainManifest` and a function to write it back:

```python
@dataclass
class DomainAssignment:
    """Agent-assigned business domain classification."""
    name: str
    sub_domain: str | None = None
    confidence: str = "Medium"  # High, Medium, Low
    assigned_by: str = "@domain-context"
    assigned_at: str = ""


@dataclass
class DomainManifest:
    """Top-level domain manifest."""
    name: str
    version: str
    description: str
    sources: list[SourceConfig]
    hints: DomainHints
    domain: DomainAssignment | None = None  # NEW


def assign_domain(
    domain_name: str,
    sub_domain: str | None = None,
    confidence: str = "Medium",
    manifest_path: Path | None = None,
) -> None:
    """Write the domain assignment to manifest.yaml.

    Reads the existing manifest, adds/updates the domain section,
    and writes back preserving all other fields.

    Args:
        domain_name: The identified business domain (e.g., "Financial Reporting").
        sub_domain: Optional more specific classification.
        confidence: Agent's confidence in the assignment.
        manifest_path: Override for manifest path.
    """
```

**Key constraint:** The write-back must preserve existing YAML content. Use `yaml.safe_load` → modify dict → `yaml.dump` with `default_flow_style=False, sort_keys=False` to maintain readability.

### 3. Domain Context Agent Update

**File:** `.claude/agents/domain-context.md`

Add a new step to the agent's process:

After Step 6 (synthesize domain-context.md), add:

**Step 7: Write domain assignment to manifest**

```bash
python3 -m brightsmith.domain_loader assign-domain \
  --name "Financial Reporting" \
  --sub-domain "SEC XBRL Filings" \
  --confidence "High"
```

The agent extracts `domain_name` and `sub_domain` from its own "Domain Identification" section in `governance/domain-context.md` and writes them to the manifest. This ensures the manifest always reflects the latest domain classification.

### 4. CLI Extension

**File:** `src/brightsmith/domain_loader.py`

Add a CLI subcommand:

```bash
# Assign domain to manifest
python -m brightsmith.domain_loader assign-domain --name "Financial Reporting" [--sub-domain "SEC XBRL"] [--confidence High]

# Show current domain assignment
python -m brightsmith.domain_loader show-domain
```

### 5. Backward Compatibility

- `load_manifest()` returns `domain=None` if the section doesn't exist
- Brightforge already handles `domain.name` being absent (returns `businessDomain: null`)
- Existing tests for `load_manifest()` continue to pass since `domain` is optional
- The `manifest.yaml.example` gets updated but existing manifests don't need changes

## Tests

### `tests/test_domain_loader.py` (additions)

| # | Test | What it validates |
|---|------|-------------------|
| 1 | `test_load_manifest_with_domain` | `domain.name` is parsed into `DomainAssignment` |
| 2 | `test_load_manifest_without_domain` | Missing `domain` section returns `domain=None` |
| 3 | `test_assign_domain_creates_section` | `assign_domain()` adds `domain` section to manifest |
| 4 | `test_assign_domain_preserves_existing` | Existing manifest fields (sources, hints) are unchanged |
| 5 | `test_assign_domain_updates_existing` | Re-running `assign_domain()` updates rather than duplicates |
| 6 | `test_assign_domain_with_sub_domain` | `sub_domain` field is written when provided |
| 7 | `test_assign_domain_default_confidence` | Defaults to "Medium" confidence |
| 8 | `test_assign_domain_timestamps` | `assigned_at` is populated with current date |
| 9 | `test_domain_assignment_dataclass` | `DomainAssignment` serializes/deserializes correctly |
| 10 | `test_cli_assign_domain` | CLI `assign-domain` command works end-to-end |
| 11 | `test_cli_show_domain` | CLI `show-domain` displays current assignment |

## Relationship to Other Specs

| Spec | Relationship |
|------|-------------|
| Brightforge Spec 16 (Multi-Domain View) | **Unblocks full display** — sidebar shows domain hierarchy when `domain.name` exists |
| `cab-agent.md` | No impact — CAB reviews schema changes, not manifest changes |
| Any future multi-domain spec | Foundation — `domain` section in manifest is the hook for multi-domain support |

## Implementation Order

1. **`src/brightsmith/domain_loader.py`** — Add `DomainAssignment` dataclass, update `DomainManifest`, add `assign_domain()` function and CLI
2. **`tests/test_domain_loader.py`** — Add tests for domain assignment
3. **`.claude/agents/domain-context.md`** — Add Step 7 (write domain to manifest)
4. **`domain/manifest.yaml.example`** — Add `domain` section with comments
5. **`CLAUDE.md`** — Document the domain assignment convention
