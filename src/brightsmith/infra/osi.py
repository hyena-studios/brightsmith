"""Open Semantic Interchange (OSI) semantic-model exporter.

Composes the governance artifacts the pipeline already produces — data
contracts (datasets, fields, grain keys), the business glossary (descriptions,
synonyms, CDE/PII flags), Mermaid ER data models (relationships), the domain
context document (AI instructions), and optional domain metric hints — into a
single OSI v1.0 YAML document (https://github.com/open-semantic-interchange/OSI)
at ``governance/semantic-model.osi.yaml``.

The OSI document is an EXPORT BOUNDARY, never an internal format (spec decision
D1): contracts, glossary, and models remain the sources of truth; the emitted
file is generated, never hand-edited. Re-exporting with unchanged inputs is
byte-identical (D4 — no timestamps, sorted collections), which is what makes
``check`` a meaningful drift gate.

Usage:
    python -m brightsmith.infra.osi generate [--zones gold] [--output PATH]
    python -m brightsmith.infra.osi check    [--zones gold] [--output PATH]
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from brightsmith import config
from brightsmith.infra.governance.serializers import normalize_zone

logger = logging.getLogger(__name__)

# OSI spec version pinned in the emitted document (spec decision D8). The spec
# is young (v1.0 released 2026-01-27) — bump here, in one place, when it revs.
OSI_SPEC_VERSION = "1.0"

# The single SQL dialect Brightsmith emits (spec decision D3): DuckDB executes
# ANSI-compatible SQL; translating to vendor dialects is a consumer concern.
_DIALECT = "ANSI_SQL"

DEFAULT_ZONES = ("gold",)


class OSIExportError(Exception):
    """Raised when the OSI export cannot proceed (spec decision D7).

    A MISSING optional artifact (glossary, models, metrics, domain context)
    leaves its section empty and is logged. A MALFORMED artifact, or a project
    with zero contracts in the requested zones, raises this instead — an empty
    or wrong semantic model published to downstream platforms is worse than no
    model.
    """


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------


def _default_output_path() -> Path:
    return config.PROJECT_ROOT / "governance" / "semantic-model.osi.yaml"


def _load_glossary_terms() -> dict[str, dict]:
    """Load business-glossary terms keyed by BOTH term_id and lowercased name.

    Missing glossary → empty mapping (optional artifact). Malformed glossary →
    :class:`OSIExportError` (D7).
    """
    path = config.PROJECT_ROOT / "governance" / "business-glossary.json"
    if not path.exists():
        logger.info("OSI export: no business glossary at %s — field descriptions/synonyms omitted", path)
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise OSIExportError(f"Business glossary is unreadable or invalid JSON: {path}: {e}") from e
    if not isinstance(data, dict):
        raise OSIExportError(
            f"Business glossary {path}: top level must be an object with a 'terms' list, "
            f"got {type(data).__name__}"
        )
    terms = data.get("terms", [])

    lookup: dict[str, dict] = {}
    for term in terms:
        if not isinstance(term, dict):
            raise OSIExportError(f"Business glossary {path}: 'terms' entries must be objects, got {type(term).__name__}")
        term_id = term.get("term_id")
        if term_id:
            lookup[str(term_id)] = term
        name = term.get("name")
        if name:
            lookup.setdefault(str(name).lower(), term)
    return lookup


def _load_er_models() -> list[dict]:
    """Parse every Mermaid erDiagram under ``governance/models/*.md``.

    Missing directory / no diagrams → empty list (optional artifact).
    """
    from brightsmith.infra.governance.parsers import _parse_mermaid_erdiagram

    models_dir = config.PROJECT_ROOT / "governance" / "models"
    if not models_dir.exists():
        return []
    parsed: list[dict] = []
    for path in sorted(models_dir.glob("*.md")):
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError) as e:
            raise OSIExportError(f"Data model file is unreadable: {path}: {e}") from e
        diagram = _parse_mermaid_erdiagram(text)
        if diagram is not None:
            parsed.append(diagram)
    return parsed


def _load_domain_context() -> str | None:
    """Full text of governance/domain-context.md, or None when absent (D9)."""
    path = config.PROJECT_ROOT / "governance" / "domain-context.md"
    if not path.exists():
        logger.info("OSI export: no domain context at %s — model ai_context omitted", path)
        return None
    return path.read_text()


def _load_metric_hints() -> list[dict]:
    """Load domain metric definitions from ``DomainHints.metrics`` if declared.

    No manifest / no ``metrics`` hint / hint file absent → empty list.
    A hint file that exists but doesn't parse into a list of
    ``{name, expression|sql}`` entries → :class:`OSIExportError` (D7).
    """
    from brightsmith.domain_loader import load_manifest

    try:
        manifest = load_manifest()
    except FileNotFoundError:
        return []
    metrics_path = manifest.hints.metrics
    if metrics_path is None:
        return []
    metrics_path = Path(metrics_path)
    if not metrics_path.exists():
        logger.info("OSI export: metrics hint declared but file absent: %s — metrics omitted", metrics_path)
        return []

    try:
        raw = yaml.safe_load(metrics_path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise OSIExportError(f"Metrics hint file is unreadable or invalid YAML/JSON: {metrics_path}: {e}") from e

    if isinstance(raw, dict):
        raw = raw.get("metrics")
    if not isinstance(raw, list):
        raise OSIExportError(
            f"Metrics hint file {metrics_path} must be a list of metric entries "
            f"(or a mapping with a 'metrics:' list)."
        )

    metrics: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise OSIExportError(f"Metrics hint file {metrics_path}: every entry needs a 'name', got: {entry!r}")
        expression = entry.get("expression") or entry.get("sql")
        if not expression:
            raise OSIExportError(
                f"Metrics hint file {metrics_path}: metric '{entry['name']}' needs an 'expression' (or 'sql')."
            )
        metrics.append({"name": str(entry["name"]), "expression": str(expression),
                        "description": str(entry.get("description", ""))})

    names = [m["name"] for m in metrics]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise OSIExportError(
            f"Metrics hint file {metrics_path}: duplicate metric name(s) {duplicates} — "
            f"OSI metric names must be unique within the model."
        )
    return metrics


# ---------------------------------------------------------------------------
# OSI construct builders
# ---------------------------------------------------------------------------


def _expression(expr: str) -> dict:
    """OSI multi-dialect expression carrying our single ANSI_SQL dialect."""
    return {"dialects": [{"dialect": _DIALECT, "expression": expr}]}


def _extension(payload: dict) -> list[dict]:
    """OSI custom_extensions block: vendor name + JSON-string payload."""
    return [{"vendor_name": "brightsmith", "data": json.dumps(payload, sort_keys=True)}]


def _field_from_column(column: dict, glossary: dict[str, dict]) -> dict:
    """Map one contract column to an OSI field."""
    name = column["name"]
    term = None
    term_id = column.get("business_term_id")
    if term_id:
        term = glossary.get(str(term_id))
    if term is None:
        term = glossary.get(name.lower())

    field: dict[str, Any] = {"name": name, "expression": _expression(name)}

    description = column.get("description") or (term.get("definition", "") if term else "")
    if description:
        field["description"] = description

    synonyms = sorted(term.get("synonyms", [])) if term else []
    if synonyms:
        field["ai_context"] = {"synonyms": synonyms}

    ext: dict[str, Any] = {"type": column.get("type", "")}
    if column.get("required"):
        ext["required"] = True
    if term_id:
        ext["business_term_id"] = term_id
    if column.get("is_cde"):
        ext["is_cde"] = True
        if column.get("cde_rationale"):
            ext["cde_rationale"] = column["cde_rationale"]
    if column.get("is_pii"):
        ext["is_pii"] = True
        if column.get("pii_rationale"):
            ext["pii_rationale"] = column["pii_rationale"]
    field["custom_extensions"] = _extension(ext)
    return field


def _dataset_from_contract(contract: dict, glossary: dict[str, dict]) -> dict:
    """Map one data contract to an OSI dataset."""
    schema = contract.get("schema", {})
    meta = contract.get("metadata", {})
    table = schema.get("table", "")
    namespace, _, tbl = table.partition(".")
    grain = schema.get("grain", {}) or {}
    grain_columns = grain.get("columns") or []

    dataset: dict[str, Any] = {
        "name": tbl or meta.get("name", "unknown"),
        "source": f"{config.PROJECT_NAME}.{namespace}.{tbl}" if tbl else table,
    }
    if grain_columns:
        dataset["primary_key"] = list(grain_columns)
    if grain.get("description"):
        dataset["description"] = grain["description"]

    dataset["fields"] = [_field_from_column(c, glossary) for c in schema.get("columns", [])]

    ext: dict[str, Any] = {
        "contract_name": meta.get("name", ""),
        "contract_version": meta.get("version", ""),
        "contract_status": meta.get("status", ""),
    }
    lineage_inputs = (contract.get("lineage") or {}).get("inputs")
    if lineage_inputs:
        ext["lineage_inputs"] = lineage_inputs
    dataset["custom_extensions"] = _extension(ext)
    return dataset


def _relationships_from_models(er_models: list[dict], datasets: list[dict]) -> list[dict]:
    """Resolve Mermaid ER relationships into OSI relationships (D5).

    A relationship is emitted only when its source/target entities both match
    exported datasets (case-insensitive) AND its join columns can be resolved:
    the target's key columns (Mermaid PK columns, else the target dataset's
    primary_key/grain) must all exist as fields on the source dataset.
    Anything unresolvable is logged and skipped — OSI requires
    ``from_columns``/``to_columns`` and we never invent join keys.
    """
    by_name = {d["name"].lower(): d for d in datasets}
    field_names = {
        d["name"].lower(): {f["name"] for f in d.get("fields", [])} for d in datasets
    }

    def _resolve(from_side: str, to_side: str, entity_pk: dict[str, list[str]]) -> list[str] | None:
        """Join columns for from_side -> to_side: the to-side's key columns
        (Mermaid PK, else its dataset primary_key/grain) — resolvable only when
        every one of them exists as a field on the from-side."""
        keys = entity_pk.get(to_side) or by_name[to_side].get("primary_key", [])
        if keys and set(keys) <= field_names[from_side]:
            return list(keys)
        return None

    relationships: list[dict] = []
    seen: set[str] = set()
    for model in er_models:
        entity_pk: dict[str, list[str]] = {}
        for entity in model.get("entities", []):
            pks = [c["column_name"] for c in entity.get("columns", []) if c.get("is_pk")]
            entity_pk[entity["name"].lower()] = pks

        for rel in model.get("relationships", []):
            source = rel["source_entity"].lower()
            target = rel["target_entity"].lower()
            if source not in by_name or target not in by_name:
                logger.info(
                    "OSI export: skipping relationship %s — entity not among exported datasets",
                    rel.get("relationship_id"),
                )
                continue

            # The FK lives on the referencing ("many") side, which Mermaid may
            # write on either end of the arrow — try both directions and keep
            # the one whose join columns actually resolve.
            columns = _resolve(source, target, entity_pk)
            from_side, to_side = source, target
            if columns is None:
                columns = _resolve(target, source, entity_pk)
                from_side, to_side = target, source
            if columns is None:
                logger.info(
                    "OSI export: skipping relationship %s — join columns unresolvable (D5)",
                    rel.get("relationship_id"),
                )
                continue

            name = f"{by_name[from_side]['name']}__{by_name[to_side]['name']}"
            if name in seen:
                continue
            seen.add(name)
            relationships.append({
                "name": name,
                "from": by_name[from_side]["name"],
                "to": by_name[to_side]["name"],
                "from_columns": columns,
                "to_columns": columns,
                "custom_extensions": _extension({
                    "source_cardinality": rel.get("source_cardinality"),
                    "target_cardinality": rel.get("target_cardinality"),
                    "label": rel.get("label"),
                }),
            })
    return sorted(relationships, key=lambda r: r["name"])


def _metrics_from_hints(hints: list[dict]) -> list[dict]:
    metrics = []
    for m in sorted(hints, key=lambda m: m["name"]):
        metric: dict[str, Any] = {"name": m["name"], "expression": _expression(m["expression"])}
        if m.get("description"):
            metric["description"] = m["description"]
        metrics.append(metric)
    return metrics


# ---------------------------------------------------------------------------
# Model assembly / export / drift check
# ---------------------------------------------------------------------------


def build_semantic_model(
    zones: tuple[str, ...] | list[str] = DEFAULT_ZONES,
    contracts_dir: Path | None = None,
) -> dict:
    """Compose the OSI semantic-model document from governance artifacts.

    Pure composer — reads artifacts, returns the document dict, writes nothing.

    Raises:
        OSIExportError: A malformed artifact was encountered, or no contract
            exists in the requested zones (D7 — an empty semantic model must
            never be silently published).
    """
    from brightsmith.infra.contract import list_contracts, load_contract

    wanted = {normalize_zone(z) or z for z in zones}

    glossary = _load_glossary_terms()

    datasets: list[dict] = []
    for summary in list_contracts(contracts_dir):
        if summary.get("status") == "error":
            raise OSIExportError(
                f"Contract file is unreadable: {summary.get('path')} — fix or remove it before exporting."
            )
        table = summary.get("table", "")
        if "." not in table:
            continue
        namespace = table.split(".", 1)[0]
        if (normalize_zone(namespace) or namespace) not in wanted:
            continue
        contract = load_contract(summary["name"], contracts_dir)
        if contract is None:
            raise OSIExportError(f"Contract '{summary['name']}' listed but not loadable.")
        datasets.append(_dataset_from_contract(contract, glossary))

    if not datasets:
        raise OSIExportError(
            f"No data contracts found for zone(s) {sorted(wanted)} — generate contracts "
            f"(python -m brightsmith.infra.contract generate <table>) before exporting an "
            f"OSI semantic model."
        )
    datasets.sort(key=lambda d: d["name"])

    semantic_model: dict[str, Any] = {
        "name": config.PROJECT_NAME,
        "description": f"Brightsmith gold-zone semantic model for project '{config.PROJECT_NAME}'.",
    }

    domain_context = _load_domain_context()
    if domain_context:
        semantic_model["ai_context"] = {"instructions": domain_context}

    semantic_model["datasets"] = datasets

    relationships = _relationships_from_models(_load_er_models(), datasets)
    if relationships:
        semantic_model["relationships"] = relationships

    metrics = _metrics_from_hints(_load_metric_hints())
    if metrics:
        semantic_model["metrics"] = metrics

    semantic_model["custom_extensions"] = _extension({
        "generated_by": "brightsmith.infra.osi",
        "zones": sorted(wanted),
    })

    return {"version": OSI_SPEC_VERSION, "semantic_model": semantic_model}


def render_semantic_model(model: dict) -> str:
    """Serialize the model dict to deterministic YAML (D4)."""
    return yaml.safe_dump(model, sort_keys=False, allow_unicode=True, width=100)


def export_semantic_model(
    output_path: Path | None = None,
    zones: tuple[str, ...] | list[str] = DEFAULT_ZONES,
    contracts_dir: Path | None = None,
) -> Path:
    """Build and write the OSI semantic model. Returns the written path."""
    path = output_path or _default_output_path()
    model = build_semantic_model(zones=zones, contracts_dir=contracts_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_semantic_model(model))
    logger.info("OSI semantic model written: %s (%d dataset(s))", path, len(model["semantic_model"]["datasets"]))
    return path


def check_drift(
    output_path: Path | None = None,
    zones: tuple[str, ...] | list[str] = DEFAULT_ZONES,
    contracts_dir: Path | None = None,
) -> list[str]:
    """Compare the on-disk OSI document against a fresh in-memory build.

    Returns a list of human-readable differences (empty = in sync). The
    emitted YAML is deterministic (D4), so a byte comparison is exact: any
    difference means either the source artifacts changed since the last
    ``generate`` or the file was hand-edited — both are drift.

    ``zones`` must mirror the zones used at ``generate`` time: checking with
    a different zone set regenerates a different document and reports drift
    that isn't there. Both CLI subcommands default to the same
    ``DEFAULT_ZONES``, so this only matters when ``--zones`` was customized.
    """
    path = output_path or _default_output_path()
    if not path.exists():
        return [f"OSI semantic model not found: {path} — run `python -m brightsmith.infra.osi generate`."]

    expected = render_semantic_model(build_semantic_model(zones=zones, contracts_dir=contracts_dir))
    actual = path.read_text()
    if actual == expected:
        return []

    diff = list(difflib.unified_diff(
        actual.splitlines(), expected.splitlines(),
        fromfile=str(path), tofile="regenerated", lineterm="", n=1,
    ))
    excerpt = diff[:40]
    return [
        f"OSI semantic model is stale or hand-edited: {path} differs from a fresh export. "
        f"Regenerate with `python -m brightsmith.infra.osi generate`.",
        *excerpt,
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_zones(raw: str) -> tuple[str, ...]:
    return tuple(z.strip() for z in raw.split(",") if z.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Brightsmith OSI semantic-model exporter")
    subparsers = parser.add_subparsers(dest="command")

    gen_p = subparsers.add_parser("generate", help="Export governance artifacts as an OSI semantic model")
    gen_p.add_argument("--zones", default=",".join(DEFAULT_ZONES), help="Comma-separated zones (default: gold)")
    gen_p.add_argument("--output", help="Output path (default: governance/semantic-model.osi.yaml)")

    check_p = subparsers.add_parser("check", help="Fail if the OSI document is missing, stale, or hand-edited")
    check_p.add_argument("--zones", default=",".join(DEFAULT_ZONES), help="Comma-separated zones (default: gold)")
    check_p.add_argument("--output", help="Path to check (default: governance/semantic-model.osi.yaml)")

    args = parser.parse_args()

    if args.command == "generate":
        try:
            path = export_semantic_model(
                output_path=Path(args.output) if args.output else None,
                zones=_parse_zones(args.zones),
            )
        except OSIExportError as e:
            print(f"OSI export FAILED: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"OSI semantic model written: {path}")
    elif args.command == "check":
        try:
            problems = check_drift(
                output_path=Path(args.output) if args.output else None,
                zones=_parse_zones(args.zones),
            )
        except OSIExportError as e:
            print(f"OSI check FAILED: {e}", file=sys.stderr)
            sys.exit(1)
        if problems:
            for p in problems:
                print(p, file=sys.stderr)
            sys.exit(1)
        print("OSI semantic model is in sync with governance artifacts.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
