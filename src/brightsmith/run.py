"""Headless pipeline runner.

Executes the full data pipeline without AI agents. All zone
transformations, DQ checks, and contract validations run as
pure Python code. Designed for cron, Airflow, GitHub Actions,
or any scheduler.

Usage:
    python -m brightsmith.run                          # Full pipeline
    python -m brightsmith.run --zone bronze            # Bronze zone only
    python -m brightsmith.run --zone silver            # Silver zone only
    python -m brightsmith.run --validate-only          # DQ + contracts, no data writes
    python -m brightsmith.run --dry-run                # Check readiness, no execution
    python -m brightsmith.run --output json            # JSON to stdout
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_DQ_FAILURE = 1
EXIT_TRANSFORM_ERROR = 2
EXIT_CONTRACT_VIOLATION = 3
EXIT_CONFIG_ERROR = 4


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ZoneNotRegisteredError(Exception):
    """Raised when a zone has no transformation module registered.

    Deliberately distinct from ``ValueError`` (audit finding H2): the domain
    transform a zone module calls into can itself raise ``ValueError`` for
    legitimate reasons (e.g. ``append_data``'s strict-mode misspelled-column
    error, ``compute_grain_id``'s missing-grain-field error). Catching bare
    ``ValueError`` in ``run_pipeline`` to detect "no module registered" also
    caught those real transform failures and silently reclassified them as
    SKIPPED with exit 0. Raising this dedicated type lets the caller catch
    only the "not registered" case.
    """


class PipelineManifestShapeError(Exception):
    """Raised when ``domain/manifest.yaml``'s ``pipeline:`` section matches
    neither the flat nor the nested shape (audit finding H5.2).

    Previously an unrecognized shape left ``_ZONE_REGISTRY`` silently empty —
    the headless runner would report every zone SKIPPED with no indication
    that the manifest was actually malformed. This type ensures a bad
    manifest fails loudly instead.
    """


# ---------------------------------------------------------------------------
# Zone ordering
# ---------------------------------------------------------------------------

ZONE_ORDER = ["bronze", "silver", "gold", "mcp"]


def previous_zone(zone: str) -> str | None:
    """Return the zone that precedes the given one."""
    idx = ZONE_ORDER.index(zone) if zone in ZONE_ORDER else -1
    return ZONE_ORDER[idx - 1] if idx > 0 else None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ZoneResult:
    """Result of executing one zone."""

    zone: str
    status: str = "PENDING"  # PENDING, SUCCESS, FAILED, SKIPPED
    rows_promoted: int = 0
    rows_skipped: int = 0
    dq_rules_passed: int = 0
    dq_rules_failed: int = 0
    dq_p0_passed: bool = True
    dq_p0_failures: list[str] = field(default_factory=list)
    contracts_valid: int = 0
    contracts_violated: int = 0
    error: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class GoldenResult:
    """Result of golden dataset verification."""

    checked: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0


@dataclass
class PipelineResult:
    """Complete result of a pipeline run."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str = ""
    duration_seconds: float = 0.0
    status: str = "PENDING"
    zones: dict[str, ZoneResult] = field(default_factory=dict)
    golden_datasets: GoldenResult = field(default_factory=GoldenResult)
    exit_code: int = EXIT_SUCCESS

    def add_zone_result(self, zone: str, result: ZoneResult) -> None:
        self.zones[zone] = result

    def finalize(self) -> None:
        """Set final status and timing."""
        self.completed_at = datetime.now(UTC).isoformat()
        started = datetime.fromisoformat(self.started_at)
        completed = datetime.fromisoformat(self.completed_at)
        self.duration_seconds = (completed - started).total_seconds()

        # Preserve non-default statuses (DRY_RUN, CONFIG_ERROR)
        if self.status not in ("PENDING",):
            return

        if any(z.status == "FAILED" for z in self.zones.values()):
            failed = [z for z in self.zones.values() if z.status == "FAILED"][0]
            if not failed.dq_p0_passed:
                self.status = "DQ_FAILURE"
                self.exit_code = EXIT_DQ_FAILURE
            elif failed.error:
                self.status = "TRANSFORM_ERROR"
                self.exit_code = EXIT_TRANSFORM_ERROR
            else:
                self.status = "FAILED"
                self.exit_code = EXIT_DQ_FAILURE
        elif any(z.contracts_violated > 0 for z in self.zones.values()):
            self.status = "SUCCESS_WITH_WARNINGS"
            self.exit_code = EXIT_CONTRACT_VIOLATION
        else:
            self.status = "SUCCESS"
            self.exit_code = EXIT_SUCCESS

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "zones": {
                name: {
                    "status": zr.status,
                    "rows_promoted": zr.rows_promoted,
                    "rows_skipped": zr.rows_skipped,
                    "dq_rules_passed": zr.dq_rules_passed,
                    "dq_rules_failed": zr.dq_rules_failed,
                    "contracts_valid": zr.contracts_valid,
                    "contracts_violated": zr.contracts_violated,
                    "error": zr.error,
                    "warnings": zr.warnings,
                }
                for name, zr in self.zones.items()
            },
            "golden_datasets": {
                "checked": self.golden_datasets.checked,
                "passed": self.golden_datasets.passed,
                "failed": self.golden_datasets.failed,
                "pass_rate": self.golden_datasets.pass_rate,
            },
        }


# ---------------------------------------------------------------------------
# Zone execution registry
# ---------------------------------------------------------------------------
#
# A zone maps to an ORDERED LIST of steps (audit finding H5.2). Historically
# a zone was a single "module:function" string; multi-source domains
# naturally produce several transform steps per zone (one per source), so
# the registry now holds a list of `_ZoneStep`s per zone and executes them
# in order, aggregating rows_promoted/rows_skipped across all of them.


@dataclass(frozen=True)
class _ZoneStep:
    """One executable transform step within a zone."""

    module: str
    function: str = "main"
    # True when `module` is a filesystem path (e.g. "src/silver/foo.py", as
    # field manifests use) rather than a dotted import path — determines
    # whether we import via `importlib.import_module` or
    # `importlib.util.spec_from_file_location`.
    file_path: bool = False


_ZONE_REGISTRY: dict[str, list[_ZoneStep]] = {}


def _parse_step_string(module_path: str) -> _ZoneStep:
    """Parse a legacy "module:function" (or bare "module") string into a step."""
    if ":" in module_path:
        mod_name, func_name = module_path.rsplit(":", 1)
    else:
        mod_name, func_name = module_path, "main"
    return _ZoneStep(module=mod_name, function=func_name, file_path=False)


def register_zone(zone: str, module_path: str) -> None:
    """Register a zone's transformation module (single-step, dotted path).

    Args:
        zone: Zone name (bronze, silver, gold, or mcp).  Alias names
              (raw/base/consumable/ai_ready) are also accepted and normalized
              to the canonical name before storage.
        module_path: Dotted module path with function (e.g., "bronze.run_ingest:main").
    """
    from brightsmith.infra.governance.serializers import normalize_zone
    canonical = normalize_zone(zone) or zone
    _ZONE_REGISTRY[canonical] = [_parse_step_string(module_path)]


def _is_file_path_module(module: str) -> bool:
    """True when a manifest ``module:`` value looks like a file path rather
    than a dotted import path (e.g. "src/silver/foo.py" vs "silver.foo")."""
    return module.endswith(".py") or "/" in module


def _load_flat_zone_registry(pipeline: dict, normalize_zone) -> None:
    """Parse the flat manifest shape: ``pipeline: {zone: {module, function}}``.

    One step per zone. A zone entry with no ``module`` key is a legitimate
    stub (e.g. a spec'd-but-not-yet-implemented zone) and is silently
    skipped, matching the framework's pre-existing behavior for this shape.
    """
    for zone_name, zone_config in pipeline.items():
        if not isinstance(zone_config, dict):
            continue
        module = zone_config.get("module", "")
        function = zone_config.get("function", "main")
        if module:
            canonical = normalize_zone(zone_name) or zone_name
            step = _ZoneStep(module=module, function=function, file_path=_is_file_path_module(module))
            _ZONE_REGISTRY[canonical] = [step]


def _load_nested_zone_registry(zones_block: dict, normalize_zone) -> None:
    """Parse the nested manifest shape: ``pipeline: {zones: {zone: [steps]}}``.

    This is the shape multi-source domains produce naturally (field evidence:
    futureproof-data's manifest) — an ordered list of step dicts per zone,
    each with a ``module`` (often a file path) and ``function``. A single
    dict (rather than a list) per zone is also accepted for the shape
    documented in docs/specs/headless-pipeline-runner.md.

    The ``mcp`` zone commonly declares a server via a ``class:`` key instead
    of a callable ``function:`` (see the field manifest's ``pipeline.zones.mcp``
    entry) — that's an MCP server registration parsed by ``serve.py``, not a
    callable transform step, so it is skipped here rather than registered.
    """
    if not isinstance(zones_block, dict):
        raise PipelineManifestShapeError(
            "domain/manifest.yaml 'pipeline.zones' must be a mapping of "
            f"zone name -> step(s), got {type(zones_block).__name__}"
        )

    for zone_name, steps in zones_block.items():
        if isinstance(steps, dict):
            steps = [steps]
        if not isinstance(steps, list):
            raise PipelineManifestShapeError(
                f"domain/manifest.yaml 'pipeline.zones.{zone_name}' must be a list "
                f"of step mappings (or a single mapping), got {type(steps).__name__}"
            )

        parsed_steps: list[_ZoneStep] = []
        for step in steps:
            if not isinstance(step, dict):
                raise PipelineManifestShapeError(
                    f"domain/manifest.yaml 'pipeline.zones.{zone_name}' step must be "
                    f"a mapping, got {type(step).__name__}"
                )
            if "class" in step and "function" not in step:
                # MCP server declaration, not a callable transform step.
                continue
            module = step.get("module", "")
            function = step.get("function", "main")
            if module:
                parsed_steps.append(
                    _ZoneStep(module=module, function=function, file_path=_is_file_path_module(module))
                )

        if parsed_steps:
            canonical = normalize_zone(zone_name) or zone_name
            _ZONE_REGISTRY[canonical] = parsed_steps


def _load_zone_registry() -> None:
    """Load zone registrations from domain manifest if not already registered.

    Zone names are normalized to canonical medallion names (bronze/silver/gold/mcp)
    at this boundary so that manifests using legacy aliases (raw/base/consumable/
    ai_ready) continue to work transparently.

    Supports both the flat shape (``pipeline: {zone: {module, function}}``)
    and the nested shape (``pipeline: {zones: {zone: [steps]}}``, H5.2). If
    the ``pipeline`` block is present but matches neither shape, raises
    :class:`PipelineManifestShapeError` rather than leaving the registry
    silently empty (the pre-fix failure mode).
    """
    if _ZONE_REGISTRY:
        return

    from brightsmith.domain_loader import load_manifest
    from brightsmith.infra.governance.serializers import normalize_zone
    try:
        manifest = load_manifest()
    except FileNotFoundError:
        # No domain manifest yet (fresh project) — legitimately leaves the
        # registry empty. A malformed manifest (yaml/parse error) is NOT caught
        # here: it must propagate so a broken config fails loudly.
        return

    pipeline = getattr(manifest, "pipeline", None)
    if not pipeline:
        return  # No pipeline section at all — legitimately empty.

    if not isinstance(pipeline, dict):
        raise PipelineManifestShapeError(
            f"domain/manifest.yaml 'pipeline' section must be a mapping, got {type(pipeline).__name__}"
        )

    if "zones" in pipeline:
        _load_nested_zone_registry(pipeline["zones"], normalize_zone)
    elif all(isinstance(v, dict) for v in pipeline.values()):
        _load_flat_zone_registry(pipeline, normalize_zone)
    else:
        raise PipelineManifestShapeError(
            "domain/manifest.yaml 'pipeline' section did not match a recognized "
            "shape: flat 'pipeline: {zone: {module, function}}' or nested "
            "'pipeline: {zones: {zone: [...steps]}}'. Fix the manifest — a zone "
            "registry left silently empty is the failure this error replaces."
        )


def _import_step_module(step: _ZoneStep):
    """Import a zone step's module, dotted-path or file-path."""
    if not step.file_path:
        return importlib.import_module(step.module)

    from brightsmith.config import PROJECT_ROOT

    file_path = Path(step.module)
    if not file_path.is_absolute():
        file_path = PROJECT_ROOT / file_path
    if not file_path.exists():
        raise ImportError(f"Zone step module file not found: {file_path}")

    # A synthetic, unique module name avoids collisions between same-named
    # step files in different directories (e.g. two "transformer.py" files).
    module_name = f"_brightsmith_zone_step__{file_path.stem}__{abs(hash(str(file_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load zone step module from file: {file_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _execute_zone_module(zone: str) -> dict:
    """Execute every registered step for a zone, in order.

    Returns:
        Dict with aggregated ``rows_promoted``/``rows_skipped`` across all
        steps registered for the zone.

    Raises:
        ZoneNotRegisteredError: No steps registered for this zone.
    """
    steps = _ZONE_REGISTRY.get(zone)
    if not steps:
        raise ZoneNotRegisteredError(f"No transformation module registered for zone '{zone}'")

    total_promoted = 0
    total_skipped = 0
    for step in steps:
        mod = _import_step_module(step)
        func = getattr(mod, step.function)
        result = func()
        if isinstance(result, dict):
            total_promoted += result.get("rows_promoted", result.get("promoted", 0))
            total_skipped += result.get("rows_skipped", result.get("skipped", 0))

    return {"rows_promoted": total_promoted, "rows_skipped": total_skipped}


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


def _preflight_relocation(zones: list[str]) -> None:
    """Loud-fail if any table in the requested zones is unreadable because the
    warehouse was moved/cloned (baked-in foreign absolute paths).

    Re-raises only :class:`WarehouseRelocationError`; any other table-load error
    is left for the per-zone execution to handle.
    """
    if not zones:
        return
    from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH
    from brightsmith.infra.iceberg_setup import WarehouseRelocationError, get_catalog

    if not Path(CATALOG_PATH).exists():
        return  # fresh project — nothing baked in yet

    from brightsmith.infra.governance.serializers import ZONE_ALIASES

    catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
    namespaces = {ns[0] for ns in catalog.list_namespaces()}
    for zone in zones:
        # zone plus any alias namespace that canonicalizes to it (raw→bronze, …)
        aliases = {ns for ns, canon in ZONE_ALIASES.items() if canon == zone}
        for candidate in {zone, *aliases}:
            if candidate not in namespaces:
                continue
            for ident in catalog.list_tables(candidate):
                try:
                    catalog.load_table(ident)
                except WarehouseRelocationError:
                    raise
                except Exception:
                    # Not a relocation problem — leave it for zone execution.
                    pass


def run_pipeline(
    zones: list[str] | None = None,
    validate_only: bool = False,
    dry_run: bool = False,
) -> PipelineResult:
    """Execute the pipeline with DQ gates and contract verification.

    Args:
        zones: Zones to run (default: all registered zones in order).
        validate_only: Run DQ + contracts without writing data.
        dry_run: Check configuration only, no execution.

    Returns:
        PipelineResult with per-zone status and overall outcome.
    """
    _load_zone_registry()
    result = PipelineResult()

    if zones is None:
        zones = [z for z in ZONE_ORDER if z in _ZONE_REGISTRY]

    if not zones and not validate_only:
        result.status = "CONFIG_ERROR"
        result.exit_code = EXIT_CONFIG_ERROR
        result.finalize()
        return result

    # Preflight: a moved/cloned warehouse would read silently empty. Fail loudly
    # with the exact repair command instead of producing a bogus all-green run.
    # Scoped to the tables in the zones we are about to touch — a stale legacy
    # table in some other namespace must not block an unrelated run.
    if not dry_run:
        from brightsmith.infra.iceberg_setup import WarehouseRelocationError

        try:
            _preflight_relocation(zones)
        except WarehouseRelocationError as exc:
            print(str(exc), file=sys.stderr)
            result.status = "CONFIG_ERROR"
            result.exit_code = EXIT_CONFIG_ERROR
            result.finalize()
            return result

    if dry_run:
        result.status = "DRY_RUN"
        for zone in zones:
            zr = ZoneResult(zone=zone, status="SKIPPED")
            result.add_zone_result(zone, zr)
        result.finalize()
        return result

    for zone in zones:
        zr = ZoneResult(zone=zone)

        # 1. Pre-flight: verify source zone contracts
        prev = previous_zone(zone)
        if prev:
            contract_ok = _check_contracts_for_zone(prev)
            if not contract_ok:
                zr.status = "FAILED"
                zr.error = f"Source zone '{prev}' contracts failed verification"
                result.add_zone_result(zone, zr)
                result.finalize()
                return result

        # 2. Execute zone transformation
        if not validate_only:
            try:
                exec_result = _execute_zone_module(zone)
                zr.rows_promoted = exec_result.get("rows_promoted", exec_result.get("promoted", 0))
                zr.rows_skipped = exec_result.get("rows_skipped", exec_result.get("skipped", 0))
            except ZoneNotRegisteredError as e:
                # No module registered — skip if validate-only would apply.
                # A ValueError raised by the domain transform itself is NOT
                # caught here (H2 fix) — it falls through to the generic
                # Exception handler below and is reported as FAILED.
                zr.status = "SKIPPED"
                zr.warnings.append(str(e))
                result.add_zone_result(zone, zr)
                continue
            except Exception as e:
                zr.status = "FAILED"
                zr.error = str(e)
                result.add_zone_result(zone, zr)
                result.finalize()
                return result

        # 3. Post-write: run DQ rules against the real warehouse.
        #    Rule-level failures (including errored P0 rules) come back as
        #    p0_failures and become a DQ_FAILURE. A failure to EXECUTE DQ at
        #    all (catalog/engine/governance-write error) is infrastructure —
        #    it must surface as TRANSFORM_ERROR, never be swallowed into a pass.
        try:
            dq_ok, dq_passed, dq_failed, p0_failures = _run_dq_for_zone(zone)
        except Exception as e:
            zr.status = "FAILED"
            zr.error = f"DQ execution failed for zone '{zone}': {e}"
            result.add_zone_result(zone, zr)
            result.finalize()
            return result
        zr.dq_rules_passed = dq_passed
        zr.dq_rules_failed = dq_failed
        zr.dq_p0_passed = dq_ok
        zr.dq_p0_failures = p0_failures

        if not dq_ok:
            zr.status = "FAILED"
            zr.error = f"DQ P0 gate failed: {p0_failures}"
            result.add_zone_result(zone, zr)
            result.finalize()
            return result

        # 4. Post-write: verify output contracts
        valid, violated = _verify_contracts_for_zone(zone)
        zr.contracts_valid = valid
        zr.contracts_violated = violated
        if violated > 0:
            zr.warnings.append(f"{violated} contract(s) violated")

        zr.status = "SUCCESS"
        result.add_zone_result(zone, zr)

    # 5. Golden dataset verification
    result.golden_datasets = _verify_golden_datasets()

    result.finalize()
    return result


# ---------------------------------------------------------------------------
# Helper functions (DQ, contracts, golden datasets)
# ---------------------------------------------------------------------------


def _run_dq_for_zone(zone: str) -> tuple[bool, int, int, list[str]]:
    """Run DQ rules for a zone against the real Iceberg warehouse.

    Returns (p0_ok, passed, failed, p0_failures).

    Execution goes through ``dq_runner.run_rules()``, which runs every
    approved/active SQL rule against real Iceberg data and records the run in
    the governance warehouse. We then filter its per-rule results down to the
    rules that touch this zone and derive the gate outcome.

    An errored P0 rule counts as a FAILURE (decision D3 — a gate that cannot
    fail is worse than no gate). ``run_rules`` already sets ``passed=False`` on
    errored rules, so no special-casing of ``error`` is needed here.

    Raises:
        Whatever ``run_rules`` raises on an infrastructure failure (missing
        catalog, engine error, governance-write error). The caller maps that
        to TRANSFORM_ERROR — it is never swallowed into a passing gate.
    """
    from brightsmith.infra.dq_runner import load_rules, run_rules

    zone_rules = [r for r in load_rules() if _rule_matches_zone(r, zone)]
    if not zone_rules:
        return (True, 0, 0, [])

    zone_rule_ids = {r["rule_id"] for r in zone_rules}
    priorities = {r["rule_id"]: str(r.get("priority", "P3")).upper() for r in zone_rules}

    result = run_rules()

    passed = 0
    failed = 0
    p0_failures: list[str] = []
    for r in result["results"]:
        rule_id = r["rule_id"]
        if rule_id not in zone_rule_ids:
            continue
        if r["passed"]:
            passed += 1
        else:
            failed += 1
            if priorities.get(rule_id) == "P0":
                p0_failures.append(rule_id)

    return (len(p0_failures) == 0, passed, failed, p0_failures)


def _rule_matches_zone(rule: dict, zone: str) -> bool:
    """Check if a DQ rule applies to a canonical zone.

    Matches on namespace, normalizing aliases via ``ZONE_ALIASES`` so a rule
    declared against ``raw.foo`` matches the canonical ``bronze`` zone and vice
    versa. Considers both the rule's declared ``tables`` and the table
    references parsed from its SQL.
    """
    from brightsmith.infra.dq_runner import _extract_table_refs
    from brightsmith.infra.governance.serializers import ZONE_ALIASES

    namespaces: set[str] = set()
    for table in rule.get("tables", []):
        if isinstance(table, str) and "." in table:
            namespaces.add(table.split(".", 1)[0])
    sql = rule.get("sql")
    if isinstance(sql, str):
        for ns, _tbl in _extract_table_refs(sql):
            namespaces.add(ns)
    return any(ZONE_ALIASES.get(ns, ns) == zone for ns in namespaces)


def _check_contracts_for_zone(zone: str) -> bool:
    """Verify a source zone's contracts before consuming it.

    Returns True if every contract passes OR there are no contracts to check
    (a warning is logged in the latter case — absence of a contract is not a
    failure). Returns False if any contract fails verification.

    Verification/infrastructure errors are NOT swallowed into a pass: if
    ``list_contracts``/``verify_contract`` raise, the exception propagates and
    fails the pipeline rather than masquerading as a clean gate.
    """
    from brightsmith.infra.contract import list_contracts, verify_contract

    contracts = list_contracts()
    zone_contracts = [c for c in contracts if _contract_matches_zone(c, zone)]
    if not zone_contracts:
        logger.warning("No contracts found for zone '%s'; skipping contract pre-check", zone)
        return True

    for c in zone_contracts:
        results = verify_contract(c["name"])
        if any(r.status == "FAIL" for r in results):
            return False
    return True


def _contract_matches_zone(contract: dict, zone: str) -> bool:
    """Check if a contract's table belongs to a canonical zone (alias-aware)."""
    from brightsmith.infra.governance.serializers import ZONE_ALIASES

    table = contract.get("table", "")
    if not isinstance(table, str) or "." not in table:
        return False
    ns = table.split(".", 1)[0]
    return ZONE_ALIASES.get(ns, ns) == zone


def _verify_contracts_for_zone(zone: str) -> tuple[int, int]:
    """Verify contracts for a zone. Returns (valid_count, violated_count).

    Verification/infrastructure errors are NOT swallowed into ``(0, 0)`` — that
    would make a broken verifier indistinguishable from "no contracts to check".
    If ``list_contracts``/``verify_contract`` raise, the exception propagates
    (consistent with the ``_check_contracts_for_zone`` pre-check).

    Uses alias-aware matching via ``_contract_matches_zone`` so contracts
    declared under legacy namespaces (e.g. ``raw.``, ``consumable.``) are
    correctly associated with their canonical zones.
    """
    from brightsmith.infra.contract import list_contracts, verify_contract
    contracts = list_contracts()
    zone_contracts = [c for c in contracts if _contract_matches_zone(c, zone)]
    valid = 0
    violated = 0
    for c in zone_contracts:
        results = verify_contract(c["name"])
        if any(r.status == "FAIL" for r in results):
            violated += 1
        else:
            valid += 1
    return (valid, violated)


def _verify_golden_datasets() -> GoldenResult:
    """Run golden dataset verification across all specs.

    A verification/infrastructure error is NOT swallowed into an empty
    ``GoldenResult()`` (which would look like "all clear / nothing to check").
    If golden-dataset loading or verification raises, the exception propagates.
    """
    from brightsmith.infra.golden_dataset import list_golden_datasets, verify_golden_dataset
    datasets = list_golden_datasets()
    if not datasets:
        return GoldenResult()

    total_checked = 0
    total_passed = 0
    for ds in datasets:
        results = verify_golden_dataset(ds["spec"])
        total_checked += len(results)
        total_passed += sum(1 for r in results if r.status in ("MATCH", "CLOSE"))

    rate = (total_passed / total_checked * 100.0) if total_checked > 0 else 0.0
    return GoldenResult(
        checked=total_checked,
        passed=total_passed,
        failed=total_checked - total_passed,
        pass_rate=rate,
    )


# ---------------------------------------------------------------------------
# Headless readiness check
# ---------------------------------------------------------------------------


def check_headless_ready() -> tuple[bool, list[str]]:
    """Check if the pipeline is ready for headless execution.

    Verifies: specs complete, pipeline validations pass, contracts valid,
    golden datasets pass, zone modules registered, no LLM imports.

    Returns:
        (is_ready, list_of_issues).
    """
    from brightsmith.config import PROJECT_ROOT

    issues: list[str] = []
    _load_zone_registry()

    # Zone modules registered
    if not _ZONE_REGISTRY:
        issues.append("No zone transformation modules registered in manifest")
    else:
        for zone in ZONE_ORDER:
            for step in _ZONE_REGISTRY.get(zone, []):
                try:
                    _import_step_module(step)
                except (ImportError, OSError) as e:
                    issues.append(f"Zone '{zone}' module '{step.module}' not importable: {e}")

    # No anthropic imports in zone code
    src_dir = PROJECT_ROOT / "src"
    if src_dir.exists():
        for py_file in src_dir.rglob("*.py"):
            # Skip test files and __pycache__
            if "__pycache__" in str(py_file):
                continue
            try:
                content = py_file.read_text()
                if "import anthropic" in content or "from anthropic" in content:
                    issues.append(f"LLM import found in {py_file.relative_to(PROJECT_ROOT)}")
            except (OSError, UnicodeDecodeError) as e:
                # Can't read a source file to verify it has no LLM imports —
                # report it as an issue rather than silently assuming it's clean.
                issues.append(
                    f"Could not read {py_file.relative_to(PROJECT_ROOT)} to check "
                    f"for LLM imports: {e}"
                )

    # Contracts exist and pass
    try:
        from brightsmith.infra.contract import list_contracts, verify_contract
        contracts = list_contracts()
        if not contracts:
            issues.append("No data contracts found")
        for c in contracts:
            if c.get("status") == "active":
                results = verify_contract(c["name"])
                if any(r.status == "FAIL" for r in results):
                    issues.append(f"Contract '{c['name']}' verification FAILED")
    except Exception as e:
        # Readiness diagnostic: a broken contract layer must surface as a
        # blocking readiness issue, never a silent "ready". Broad by intent —
        # any failure to load/verify contracts means we cannot certify ready.
        # (Allowlisted in tests/infra/test_no_swallowed_exceptions.py.)
        issues.append(f"Contract verification could not be completed: {e}")

    # DQ rules exist
    from brightsmith.config import DQ_RULES_DIR
    if DQ_RULES_DIR.exists():
        rule_files = list(DQ_RULES_DIR.glob("*.json"))
        if not rule_files:
            issues.append("No DQ rules files found")
    else:
        issues.append(f"DQ rules directory missing: {DQ_RULES_DIR}")

    return (len(issues) == 0, issues)


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------


def _save_run_history(result: PipelineResult) -> Path:
    """Save run result to governance/run-history/."""
    from brightsmith.config import PROJECT_ROOT

    history_dir = PROJECT_ROOT / "governance" / "run-history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = history_dir / f"{timestamp}.json"
    path.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for headless pipeline execution."""
    parser = argparse.ArgumentParser(description="Brightsmith Headless Pipeline Runner")
    parser.add_argument("--zone", choices=["bronze", "silver", "gold", "mcp", "all"], default="all")
    parser.add_argument("--validate-only", action="store_true", help="Run DQ + contracts, no data writes")
    parser.add_argument("--dry-run", action="store_true", help="Check config only, no execution")
    parser.add_argument("--output", choices=["json", "summary"], default="summary")
    parser.add_argument("--headless-ready", action="store_true", help="Check headless readiness")

    args = parser.parse_args()

    if args.headless_ready:
        _cmd_headless_ready()
        return

    zones = None if args.zone == "all" else [args.zone]

    result = run_pipeline(
        zones=zones,
        validate_only=args.validate_only,
        dry_run=args.dry_run,
    )

    # Save run history
    _save_run_history(result)

    # Output
    if args.output == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        _print_summary(result)

    sys.exit(result.exit_code)


def _print_summary(result: PipelineResult) -> None:
    """Print human-readable pipeline summary."""
    print(f"Pipeline Run: {result.run_id}")
    print(f"Status: {result.status}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print()

    for zone_name, zr in result.zones.items():
        icon = "PASS" if zr.status == "SUCCESS" else "FAIL" if zr.status == "FAILED" else zr.status
        print(f"  {zone_name:<12} [{icon}]")
        if zr.rows_promoted or zr.rows_skipped:
            print(f"    Rows: {zr.rows_promoted} promoted, {zr.rows_skipped} skipped")
        if zr.dq_rules_passed or zr.dq_rules_failed:
            print(f"    DQ:   {zr.dq_rules_passed} passed, {zr.dq_rules_failed} failed")
        if zr.contracts_valid or zr.contracts_violated:
            print(f"    Contracts: {zr.contracts_valid} valid, {zr.contracts_violated} violated")
        if zr.error:
            print(f"    Error: {zr.error}")
        for w in zr.warnings:
            print(f"    Warning: {w}")

    if result.golden_datasets.checked:
        gr = result.golden_datasets
        print(f"\n  Golden datasets: {gr.passed}/{gr.checked} ({gr.pass_rate:.0f}%)")


def _cmd_headless_ready() -> None:
    """Check and report headless readiness."""
    ready, issues = check_headless_ready()
    if ready:
        print("READY for headless execution.")
    else:
        print(f"NOT READY: {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(EXIT_CONFIG_ERROR)


if __name__ == "__main__":
    main()
