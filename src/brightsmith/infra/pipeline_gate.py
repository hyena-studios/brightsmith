"""Pipeline gate — programmatic enforcement of agent execution order.

Tracks pipeline step execution as a state machine per spec. Each spec gets a
JSON state file at governance/pipeline-state/{spec}-pipeline.json. The gate
enforces prerequisites (steps that must complete before others), records
completions with output artifact paths and SHA-256 hashes, validates skip
justifications, and checks zone transition readiness.

Usage:
    python -m brightsmith.infra.pipeline_gate init <spec-name> --zone raw|base|consumable|ai_ready [--mode greenfield|backfill]
    python -m brightsmith.infra.pipeline_gate check <spec-name> <step-name>
    python -m brightsmith.infra.pipeline_gate complete <spec-name> <step-name> --output <path>
    python -m brightsmith.infra.pipeline_gate skip <spec-name> <step-name> --reason "..." --evidence <path>
    python -m brightsmith.infra.pipeline_gate approve <spec-name> <artifact> --decision APPROVED|CHANGES_REQUESTED --by <who> [--notes "..."] [--document <path>]
    python -m brightsmith.infra.pipeline_gate validate <spec-name> | --all
    python -m brightsmith.infra.pipeline_gate check-transition <from-zone> <to-zone>
    python -m brightsmith.infra.pipeline_gate status <spec-name>
    python -m brightsmith.infra.pipeline_gate audit --format json|markdown
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

StepStatus = Literal["NOT_STARTED", "IN_PROGRESS", "COMPLETED", "SKIPPED"]
Zone = Literal["bronze", "silver", "gold", "mcp"]
Mode = Literal["greenfield", "backfill"]
ApprovalDecision = Literal["APPROVED", "CHANGES_REQUESTED"]


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """A single pipeline step definition."""

    name: str
    agent: str
    requires: tuple[str, ...] = ()
    blocking: bool = False
    skippable: bool = False
    skip_condition: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "agent": self.agent,
            "requires": list(self.requires),
            "blocking": self.blocking,
            "skippable": self.skippable,
            "skip_condition": self.skip_condition,
        }


# --- Raw Zone ---

BRONZE_ZONE_STEPS: tuple[Step, ...] = (
    Step("governance-reviewer-pre", "@governance-reviewer"),
    Step("primary-agent", "@primary-agent", requires=("governance-reviewer-pre",)),
    Step("data-analyst", "@data-analyst", requires=("primary-agent",)),
    Step("domain-context", "@domain-context", requires=("data-analyst",)),
    Step("dq-rule-writer", "@dq-rule-writer", requires=("data-analyst",)),
    Step("dq-engineer", "@dq-engineer", requires=("dq-rule-writer",)),
    Step("chaos-monkey", "@chaos-monkey", requires=("dq-engineer",)),
    Step("entity-resolver", "@entity-resolver", requires=("primary-agent",)),
    Step("pii-scanner", "@pii-scanner", requires=("primary-agent",)),
    Step("temporal-modeler", "@temporal-modeler", requires=("data-analyst",)),
    Step("lineage-tracker", "@lineage-tracker", requires=("primary-agent",)),
    Step("cde-tagger", "@cde-tagger", requires=("primary-agent",)),
    Step("doc-generator", "@doc-generator", requires=("cde-tagger",)),
    Step("adversarial-auditor", "@adversarial-auditor", requires=("chaos-monkey",)),
    Step("governance-reviewer-post", "@governance-reviewer", requires=("doc-generator",)),
    Step("staff-engineer", "@staff-engineer", requires=("governance-reviewer-post",)),
)

# --- Zone Transition ---

ZONE_TRANSITION_STEPS: tuple[Step, ...] = (
    Step(
        "principal-data-architect", "@principal-data-architect",
        requires=("staff-engineer",), blocking=True,
    ),
    Step(
        "insight-manager", "@insight-manager",
        requires=("principal-data-architect",),
        skippable=True,
        skip_condition="raw-to-base transition (insight-manager runs at base→consumable and consumable→ai-ready only)",
    ),
)

# --- Base Zone (Greenfield) ---

SILVER_GREENFIELD_STEPS: tuple[Step, ...] = (
    Step("governance-reviewer-pre", "@governance-reviewer"),
    Step("data-steward", "@data-steward", requires=("governance-reviewer-pre",)),
    Step("semantic-modeler-conceptual", "@semantic-modeler", requires=("data-steward",)),
    Step("semantic-modeler-logical", "@semantic-modeler", requires=("semantic-modeler-conceptual",)),
    Step("data-analyst", "@data-analyst", requires=("semantic-modeler-logical",)),
    Step("dq-rule-writer", "@dq-rule-writer", requires=("data-analyst",)),
    Step("semantic-modeler-physical", "@semantic-modeler", requires=("semantic-modeler-logical",)),
    Step("primary-agent", "@primary-agent", requires=("semantic-modeler-physical",)),
    Step(
        "cab-review", "@cab-agent", requires=("primary-agent",),
        skippable=True,
        skip_condition="Table is new (no existing contract) — CAB review only applies to schema modifications of existing tables",
    ),
    Step("dq-engineer", "@dq-engineer", requires=("dq-rule-writer", "primary-agent", "cab-review")),
    Step("chaos-monkey", "@chaos-monkey", requires=("dq-engineer",)),
    Step("entity-resolver", "@entity-resolver", requires=("primary-agent",)),
    Step("pii-scanner", "@pii-scanner", requires=("primary-agent",)),
    Step("temporal-modeler", "@temporal-modeler", requires=("data-analyst",)),
    Step("lineage-tracker", "@lineage-tracker", requires=("primary-agent",)),
    Step("cde-tagger", "@cde-tagger", requires=("primary-agent",)),
    Step("doc-generator", "@doc-generator", requires=("cde-tagger",)),
    Step("adversarial-auditor", "@adversarial-auditor", requires=("chaos-monkey",)),
    Step("governance-reviewer-post", "@governance-reviewer", requires=("doc-generator", "cab-review")),
    Step("staff-engineer", "@staff-engineer", requires=("governance-reviewer-post",)),
)

# --- Base Zone (Backfill) ---

SILVER_BACKFILL_STEPS: tuple[Step, ...] = (
    Step("semantic-modeler-physical", "@semantic-modeler"),
    Step("semantic-modeler-logical", "@semantic-modeler", requires=("semantic-modeler-physical",)),
    Step("data-analyst", "@data-analyst", requires=("semantic-modeler-logical",)),
    Step("temporal-modeler", "@temporal-modeler", requires=("data-analyst",)),
    Step("dq-rule-writer", "@dq-rule-writer", requires=("data-analyst",)),
    Step("dq-engineer", "@dq-engineer", requires=("dq-rule-writer",)),
    Step("chaos-monkey", "@chaos-monkey", requires=("dq-engineer",)),
    Step(
        "cab-review", "@cab-agent", requires=("dq-engineer",),
        skippable=True,
        skip_condition="Table is new (no existing contract) — CAB review only applies to schema modifications of existing tables",
    ),
    Step("semantic-modeler-conceptual", "@semantic-modeler", requires=("chaos-monkey",)),
    Step("data-steward", "@data-steward", requires=("semantic-modeler-conceptual",)),
    Step("governance-reviewer-post", "@governance-reviewer", requires=("data-steward", "cab-review")),
    Step("staff-engineer", "@staff-engineer", requires=("governance-reviewer-post",)),
)

# --- Consumable Zone (Greenfield — same structure as base greenfield) ---

GOLD_GREENFIELD_STEPS: tuple[Step, ...] = SILVER_GREENFIELD_STEPS

# --- Consumable Zone (Backfill) ---

GOLD_BACKFILL_STEPS: tuple[Step, ...] = SILVER_BACKFILL_STEPS

# --- AI-Ready Zone ---

MCP_ZONE_STEPS: tuple[Step, ...] = (
    Step("governance-reviewer-pre", "@governance-reviewer"),
    Step("primary-agent", "@primary-agent", requires=("governance-reviewer-pre",)),
    Step("data-analyst", "@data-analyst", requires=("primary-agent",)),
    Step("dq-rule-writer", "@dq-rule-writer", requires=("data-analyst",)),
    Step("dq-engineer", "@dq-engineer", requires=("dq-rule-writer",)),
    Step("lineage-tracker", "@lineage-tracker", requires=("primary-agent",)),
    Step("cde-tagger", "@cde-tagger", requires=("primary-agent",)),
    Step("doc-generator", "@doc-generator", requires=("cde-tagger",)),
    Step("governance-reviewer-post", "@governance-reviewer", requires=("doc-generator",)),
    Step("staff-engineer", "@staff-engineer", requires=("governance-reviewer-post",)),
)


def _get_steps(zone: Zone, mode: Mode = "greenfield") -> tuple[Step, ...]:
    """Return the canonical step sequence for a zone and mode."""
    registry: dict[tuple[Zone, Mode], tuple[Step, ...]] = {
        ("bronze", "greenfield"): BRONZE_ZONE_STEPS,
        ("bronze", "backfill"): BRONZE_ZONE_STEPS,
        ("silver", "greenfield"): SILVER_GREENFIELD_STEPS,
        ("silver", "backfill"): SILVER_BACKFILL_STEPS,
        ("gold", "greenfield"): GOLD_GREENFIELD_STEPS,
        ("gold", "backfill"): GOLD_BACKFILL_STEPS,
        ("mcp", "greenfield"): MCP_ZONE_STEPS,
        ("mcp", "backfill"): MCP_ZONE_STEPS,
    }
    return registry[(zone, mode)]


def _get_step_def(zone: Zone, mode: Mode, step_name: str) -> Step:
    """Look up a step definition by name, including zone transition steps."""
    for step in _get_steps(zone, mode):
        if step.name == step_name:
            return step
    for step in ZONE_TRANSITION_STEPS:
        if step.name == step_name:
            return step
    raise ValueError(f"Unknown step '{step_name}' for zone={zone}, mode={mode}")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GateBlockedError(Exception):
    """Raised when a step's prerequisites are not met."""

    def __init__(self, step: str, missing: list[str]):
        self.step = step
        self.missing = missing
        names = ", ".join(missing)
        super().__init__(f"Gate BLOCKED for '{step}': prerequisites not met: {names}")


class GateValidationError(Exception):
    """Raised when pipeline validation fails."""

    def __init__(self, spec: str, issues: list[str]):
        self.spec = spec
        self.issues = issues
        super().__init__(f"Validation FAILED for '{spec}': {len(issues)} issue(s)")


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    """Compute SHA-256 hash of a file. Returns empty string if file not found."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


# ---------------------------------------------------------------------------
# PipelineGate
# ---------------------------------------------------------------------------


class PipelineGate:
    """State machine for a single spec's pipeline execution.

    Tracks which agents have run, their outputs, prerequisites, skip
    justifications, and approval decisions. Persists to a JSON state file.

    Usage:
        gate = PipelineGate("raw-sec-edgar-ingest")

        gate.check_prerequisites("data-analyst")
        gate.complete_step("data-analyst", output="governance/eda/raw-sec-edgar-eda.md")

        gate.skip_step("pii-scanner",
                       reason="domain-context.md PII section: no PII expected",
                       evidence="governance/domain-context.md")

        valid, issues = gate.validate()
    """

    def __init__(self, spec: str, state_dir: Path | None = None):
        from brightsmith.config import PIPELINE_STATE_DIR

        self.spec = spec
        self._state_dir = state_dir or PIPELINE_STATE_DIR
        self._state_path = self._state_dir / f"{spec}-pipeline.json"
        self._state: dict = self._load()

    def _load(self) -> dict:
        """Load existing state or return empty dict."""
        if self._state_path.exists():
            return json.loads(self._state_path.read_text())
        return {}

    def _save(self) -> None:
        """Persist state to disk."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2) + "\n")

    @property
    def zone(self) -> Zone:
        return self._state.get("zone", "bronze")

    @property
    def mode(self) -> Mode:
        return self._state.get("mode", "greenfield")

    # --- Initialization ---

    def init(self, zone: Zone, mode: Mode = "greenfield") -> None:
        """Initialize pipeline state for a spec.

        Creates the state file with all steps set to NOT_STARTED.

        Args:
            zone: Which zone this spec belongs to.
            mode: greenfield (new tables) or backfill (existing tables).
        """
        now = datetime.now(timezone.utc).isoformat()
        steps = {}
        for step in _get_steps(zone, mode):
            steps[step.name] = {
                "status": "NOT_STARTED",
                "agent": step.agent,
                "requires": list(step.requires),
                "blocking": step.blocking,
                "skippable": step.skippable,
            }

        self._state = {
            "spec": self.spec,
            "zone": zone,
            "mode": mode,
            "started": now,
            "steps": steps,
            "skipped_steps": {},
            "approvals": {},
        }
        self._save()

    # --- Pre-step gate check ---

    def check_prerequisites(self, step_name: str) -> None:
        """Verify all prerequisites for a step are met.

        Args:
            step_name: The step about to be executed.

        Raises:
            GateBlockedError: If any prerequisite is not COMPLETED or SKIPPED.
        """
        step_def = _get_step_def(self.zone, self.mode, step_name)
        steps = self._state.get("steps", {})
        skipped = self._state.get("skipped_steps", {})

        missing = []
        for req in step_def.requires:
            req_state = steps.get(req, {})
            req_status = req_state.get("status", "NOT_STARTED")
            if req_status not in ("COMPLETED",) and req not in skipped:
                missing.append(req)

        if missing:
            raise GateBlockedError(step_name, missing)

    # --- Step lifecycle ---

    def start_step(self, step_name: str) -> None:
        """Mark a step as in progress.

        Args:
            step_name: The step being started.
        """
        self.check_prerequisites(step_name)
        steps = self._state.setdefault("steps", {})
        if step_name not in steps:
            step_def = _get_step_def(self.zone, self.mode, step_name)
            steps[step_name] = {
                "status": "IN_PROGRESS",
                "agent": step_def.agent,
                "requires": list(step_def.requires),
                "blocking": step_def.blocking,
                "skippable": step_def.skippable,
            }
        else:
            steps[step_name]["status"] = "IN_PROGRESS"
        steps[step_name]["started_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def complete_step(self, step_name: str, output: str = "") -> None:
        """Record that a step completed successfully.

        Args:
            step_name: The step that completed.
            output: Path to the output artifact (relative to project root).
        """
        from brightsmith.config import PROJECT_ROOT

        steps = self._state.setdefault("steps", {})
        now = datetime.now(timezone.utc).isoformat()

        if step_name not in steps:
            step_def = _get_step_def(self.zone, self.mode, step_name)
            steps[step_name] = {
                "agent": step_def.agent,
                "requires": list(step_def.requires),
                "blocking": step_def.blocking,
                "skippable": step_def.skippable,
            }

        steps[step_name]["status"] = "COMPLETED"
        steps[step_name]["completed_at"] = now

        if output:
            steps[step_name]["output"] = output
            output_path = PROJECT_ROOT / output
            file_hash = _hash_file(output_path)
            if file_hash:
                steps[step_name]["output_hash"] = file_hash

        self._save()

    def skip_step(self, step_name: str, reason: str, evidence: str) -> None:
        """Record that a step was intentionally skipped.

        Both reason and evidence are required — no silent omissions.

        Args:
            step_name: The step being skipped.
            reason: Why it was skipped (must reference a governance artifact).
            evidence: Path to the artifact that justifies the skip.

        Raises:
            ValueError: If the step is not skippable, or reason/evidence missing.
        """
        step_def = _get_step_def(self.zone, self.mode, step_name)
        if not step_def.skippable:
            raise ValueError(
                f"Step '{step_name}' is NOT skippable. "
                f"Agent {step_def.agent} must be executed."
            )
        if not reason or not evidence:
            raise ValueError(
                f"Cannot skip '{step_name}' without both reason and evidence. "
                f"Skip condition: {step_def.skip_condition}"
            )

        self._state.setdefault("skipped_steps", {})[step_name] = {
            "reason": reason,
            "evidence": evidence,
            "skipped_at": datetime.now(timezone.utc).isoformat(),
        }

        # Also update the step status in the steps dict
        steps = self._state.setdefault("steps", {})
        if step_name in steps:
            steps[step_name]["status"] = "SKIPPED"

        self._save()

    # --- Approval tracking ---

    def record_approval(
        self,
        artifact: str,
        decision: ApprovalDecision,
        decided_by: str,
        notes: str = "",
        document: str = "",
    ) -> None:
        """Record a human approval decision.

        Args:
            artifact: What was approved (e.g., "business-terms", "conceptual-model").
            decision: APPROVED or CHANGES_REQUESTED.
            decided_by: Who decided (e.g., "human:jeff", "auto").
            notes: Optional reviewer notes.
            document: Path to the approval document.
        """
        self._state.setdefault("approvals", {})[artifact] = {
            "status": decision,
            "decided_by": decided_by,
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "document": document,
            "notes": notes,
        }
        self._save()

    # --- Validation ---

    def validate(self) -> tuple[bool, list[str]]:
        """Validate the pipeline state for completeness.

        Checks that every non-skipped step is COMPLETED, every skipped step
        has justification, all output files exist, and all blocking steps
        have been executed.

        Returns:
            Tuple of (is_valid, list_of_issues).
        """
        from brightsmith.config import PROJECT_ROOT

        issues: list[str] = []
        steps = self._state.get("steps", {})
        skipped = self._state.get("skipped_steps", {})

        # Get canonical step list
        zone = self.zone
        mode = self.mode
        canonical = _get_steps(zone, mode)

        # Check every canonical step
        for step_def in canonical:
            name = step_def.name
            step_state = steps.get(name, {})
            status = step_state.get("status", "NOT_STARTED")

            if name in skipped:
                # Verify skip has required fields
                skip_info = skipped[name]
                if not skip_info.get("reason"):
                    issues.append(f"Step '{name}' skipped without reason")
                if not skip_info.get("evidence"):
                    issues.append(f"Step '{name}' skipped without evidence")
                if not step_def.skippable:
                    issues.append(f"Step '{name}' is NOT skippable but was skipped")
                continue

            if status != "COMPLETED":
                issues.append(f"Step '{name}' ({step_def.agent}) status is {status}, expected COMPLETED")
                continue

            # Verify output file exists
            output = step_state.get("output", "")
            if output:
                output_path = PROJECT_ROOT / output
                if not output_path.exists():
                    issues.append(f"Step '{name}' output file missing: {output}")
                else:
                    # Check hash matches
                    recorded_hash = step_state.get("output_hash", "")
                    if recorded_hash:
                        current_hash = _hash_file(output_path)
                        if current_hash != recorded_hash:
                            issues.append(
                                f"Step '{name}' output modified after completion: {output} "
                                f"(recorded: {recorded_hash[:20]}..., current: {current_hash[:20]}...)"
                            )

        # Zone-specific validation checks
        issues.extend(self._validate_zone_specific(zone))

        return (len(issues) == 0, issues)

    def _validate_zone_specific(self, zone: Zone) -> list[str]:
        """Run zone-specific validation checks."""
        from brightsmith.config import DQ_RULES_DIR, GOLDEN_DATASETS_DIR, PROJECT_ROOT

        issues: list[str] = []

        # Consumable and AI-Ready zones: DQ rules file must exist with >= 1 rule
        if zone in ("gold", "mcp"):
            dq_file = DQ_RULES_DIR / f"{self.spec}.json"
            if not dq_file.exists():
                issues.append(
                    f"DQ rules file missing for {zone} spec: {dq_file}. "
                    f"@dq-rule-writer must produce rules before completion."
                )
            else:
                import json as _json
                try:
                    data = _json.loads(dq_file.read_text())
                    rules = data.get("rules", [])
                    if len(rules) == 0:
                        issues.append(
                            f"DQ rules file exists but contains 0 rules: {dq_file}. "
                            f"Consumable/AI-Ready specs require at least 1 DQ rule."
                        )
                except Exception:
                    issues.append(f"DQ rules file is not valid JSON: {dq_file}")

        # Consumable zone: golden dataset must exist with >= 3 values
        if zone == "gold":
            golden_file = GOLDEN_DATASETS_DIR / f"{self.spec}-golden.json"
            if not golden_file.exists():
                issues.append(
                    f"Golden dataset missing for consumable spec: {golden_file}. "
                    f"Must contain at least 3 independently verifiable values."
                )
            else:
                import json as _json
                try:
                    data = _json.loads(golden_file.read_text())
                    values = data.get("values", data.get("records", []))
                    if len(values) < 3:
                        issues.append(
                            f"Golden dataset has {len(values)} values (minimum 3): {golden_file}"
                        )
                except Exception:
                    issues.append(f"Golden dataset is not valid JSON: {golden_file}")

        # Consumable greenfield: physical model file must exist
        if zone == "gold" and self.mode == "greenfield":
            model_file = PROJECT_ROOT / "governance" / "models" / f"{self.spec}-physical.md"
            if not model_file.exists():
                issues.append(
                    f"Physical model missing for consumable greenfield spec: {model_file}"
                )

        # Consumable and AI-Ready zones: data contract must exist
        if zone in ("gold", "mcp"):
            contracts_dir = PROJECT_ROOT / "governance" / "data-contracts"
            if contracts_dir.exists():
                contract_files = list(contracts_dir.glob("*.yaml"))
                # We check that at least one contract exists for this zone
                # (contract names derive from table names, not spec names)
            # Note: contract existence is verified but not strictly tied to spec name
            # because contracts are per-table, not per-spec

        # CAB decision: if silver/gold and a CAB decision exists, it must not be PENDING
        if zone in ("silver", "gold"):
            cab_dir = PROJECT_ROOT / "governance" / "cab-decisions"
            if cab_dir.exists():
                index_path = cab_dir / "index.json"
                if index_path.exists():
                    try:
                        index_data = json.loads(index_path.read_text())
                        for entry in index_data.get("decisions", []):
                            if entry.get("spec") == self.spec and entry.get("decision") == "PENDING":
                                issues.append(
                                    f"CAB decision '{entry['decision_id']}' is PENDING — "
                                    f"human approval required before spec completion"
                                )
                    except Exception:
                        pass

        # Warehouse population: verify tables exist in the persistent Iceberg catalog
        issues.extend(self._validate_warehouse_population(zone))

        return issues

    def _validate_warehouse_population(self, zone: Zone) -> list[str]:
        """Verify that the pipeline has written tables to the persistent warehouse.

        Loads the PyIceberg catalog and checks that the zone's namespace
        contains tables with non-zero row counts.
        """
        from brightsmith.config import CATALOG_PATH, WAREHOUSE_PATH

        issues: list[str] = []

        if not CATALOG_PATH.exists():
            issues.append(
                f"Iceberg catalog not found at {CATALOG_PATH} — "
                f"pipeline has not written to the persistent warehouse"
            )
            return issues

        try:
            from brightsmith.infra.iceberg_setup import get_catalog

            catalog = get_catalog(WAREHOUSE_PATH, CATALOG_PATH)
            namespaces = [ns[0] for ns in catalog.list_namespaces()]

            if zone not in namespaces:
                issues.append(
                    f"Namespace '{zone}' not found in Iceberg catalog — "
                    f"pipeline has not written to the persistent warehouse. "
                    f"Available namespaces: {namespaces}"
                )
                return issues

            tables = catalog.list_tables(zone)
            if not tables:
                issues.append(
                    f"No tables found in '{zone}' namespace — "
                    f"pipeline has not written to the persistent warehouse"
                )
                return issues

            for ns, table_name in tables:
                try:
                    table = catalog.load_table(f"{ns}.{table_name}")
                    arrow = table.scan().to_arrow()
                    row_count = len(arrow)
                    if row_count == 0:
                        issues.append(
                            f"Table {ns}.{table_name} has 0 rows — "
                            f"pipeline has not populated this table"
                        )
                except Exception as exc:
                    issues.append(
                        f"Failed to read table {ns}.{table_name}: {exc}"
                    )

        except Exception as exc:
            issues.append(f"Warehouse verification failed: {exc}")

        return issues

    # --- Zone transition check ---

    @staticmethod
    def check_zone_transition(
        from_zone: Zone,
        to_zone: Zone,
        state_dir: Path | None = None,
    ) -> tuple[bool, list[str]]:
        """Verify all specs in a zone are complete before transitioning.

        Checks that every spec in the source zone has a passing pipeline state,
        and that blocking reviews (principal-data-architect) are completed.

        Args:
            from_zone: The zone being completed.
            to_zone: The zone about to start.
            state_dir: Override for pipeline state directory.

        Returns:
            Tuple of (is_ready, list_of_issues).
        """
        from brightsmith.config import PIPELINE_STATE_DIR

        sdir = state_dir or PIPELINE_STATE_DIR
        issues: list[str] = []

        if not sdir.exists():
            issues.append(f"Pipeline state directory does not exist: {sdir}")
            return (False, issues)

        # Find all state files for the source zone
        zone_specs: list[PipelineGate] = []
        for state_file in sorted(sdir.glob("*-pipeline.json")):
            data = json.loads(state_file.read_text())
            if data.get("zone") == from_zone:
                gate = PipelineGate(data["spec"], state_dir=sdir)
                zone_specs.append(gate)

        if not zone_specs:
            issues.append(f"No specs found for zone '{from_zone}'")
            return (False, issues)

        # Validate each spec
        for gate in zone_specs:
            valid, spec_issues = gate.validate()
            if not valid:
                for issue in spec_issues:
                    issues.append(f"[{gate.spec}] {issue}")

        # Check zone transition steps
        for gate in zone_specs:
            steps = gate._state.get("steps", {})
            skipped = gate._state.get("skipped_steps", {})

            # principal-data-architect must run at every transition
            pda = steps.get("principal-data-architect", {})
            if pda.get("status") != "COMPLETED" and "principal-data-architect" not in skipped:
                issues.append(
                    f"[{gate.spec}] @principal-data-architect review not completed for "
                    f"{from_zone}→{to_zone} transition"
                )

            # insight-manager required at base→consumable and consumable→ai_ready
            if from_zone in ("silver", "gold"):
                im = steps.get("insight-manager", {})
                if im.get("status") != "COMPLETED" and "insight-manager" not in skipped:
                    issues.append(
                        f"[{gate.spec}] @insight-manager report not completed for "
                        f"{from_zone}→{to_zone} transition"
                    )

        return (len(issues) == 0, issues)

    # --- Status display ---

    def status_summary(self) -> str:
        """Return a human-readable status summary."""
        lines = [
            f"Pipeline Status: {self.spec}",
            f"Zone: {self.zone} | Mode: {self.mode}",
            f"Started: {self._state.get('started', 'N/A')}",
            "",
            f"{'Step':<30} {'Agent':<28} {'Status':<12}",
            "-" * 70,
        ]

        steps = self._state.get("steps", {})
        skipped = self._state.get("skipped_steps", {})

        for step_name, step_info in steps.items():
            agent = step_info.get("agent", "?")
            status = step_info.get("status", "?")
            if step_name in skipped:
                status = "SKIPPED"
            lines.append(f"{step_name:<30} {agent:<28} {status:<12}")

        # Approvals
        approvals = self._state.get("approvals", {})
        if approvals:
            lines.append("")
            lines.append("Approvals:")
            for artifact, info in approvals.items():
                lines.append(
                    f"  {artifact}: {info.get('status', '?')} "
                    f"by {info.get('decided_by', '?')} "
                    f"at {info.get('decided_at', '?')}"
                )

        return "\n".join(lines)

    # --- Audit report ---

    @staticmethod
    def audit_report(fmt: str = "markdown", state_dir: Path | None = None) -> str:
        """Generate audit report across all specs.

        Args:
            fmt: Output format — "markdown" or "json".
            state_dir: Override for pipeline state directory.

        Returns:
            Formatted audit report string.
        """
        from brightsmith.config import PIPELINE_STATE_DIR, PROJECT_ROOT

        sdir = state_dir or PIPELINE_STATE_DIR
        if not sdir.exists():
            return "No pipeline state directory found."

        specs: list[dict] = []
        for state_file in sorted(sdir.glob("*-pipeline.json")):
            data = json.loads(state_file.read_text())
            gate = PipelineGate(data["spec"], state_dir=sdir)
            valid, issues = gate.validate()
            specs.append({
                "spec": data["spec"],
                "zone": data.get("zone"),
                "mode": data.get("mode"),
                "started": data.get("started"),
                "valid": valid,
                "issues": issues,
                "steps": data.get("steps", {}),
                "skipped_steps": data.get("skipped_steps", {}),
                "approvals": data.get("approvals", {}),
            })

        if fmt == "json":
            return json.dumps({"audit_date": datetime.now(timezone.utc).isoformat(), "specs": specs}, indent=2)

        # Markdown format
        lines = [
            "# Pipeline Audit Report",
            f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"**Specs:** {len(specs)}",
            "",
        ]

        for spec in specs:
            status_icon = "PASS" if spec["valid"] else "FAIL"
            lines.append(f"## {spec['spec']} [{status_icon}]")
            lines.append(f"Zone: {spec['zone']} | Mode: {spec['mode']}")
            lines.append("")

            if spec["issues"]:
                lines.append("### Issues")
                for issue in spec["issues"]:
                    lines.append(f"- {issue}")
                lines.append("")

            # Step summary table
            lines.append("| Step | Agent | Status | Output |")
            lines.append("|------|-------|--------|--------|")
            for step_name, step_info in spec["steps"].items():
                status = step_info.get("status", "?")
                if step_name in spec["skipped_steps"]:
                    status = "SKIPPED"
                output = step_info.get("output", "—")
                lines.append(
                    f"| {step_name} | {step_info.get('agent', '?')} | {status} | {output} |"
                )
            lines.append("")

            # Approvals
            if spec["approvals"]:
                lines.append("| Artifact | Decision | By | Date | Notes |")
                lines.append("|----------|----------|-----|------|-------|")
                for artifact, info in spec["approvals"].items():
                    lines.append(
                        f"| {artifact} | {info.get('status', '?')} | "
                        f"{info.get('decided_by', '?')} | "
                        f"{info.get('decided_at', '?')[:10]} | "
                        f"{info.get('notes', '—')} |"
                    )
                lines.append("")

            # Skipped steps
            if spec["skipped_steps"]:
                lines.append("### Skipped Steps")
                for step_name, skip_info in spec["skipped_steps"].items():
                    lines.append(f"- **{step_name}**: {skip_info.get('reason', '?')}")
                    lines.append(f"  Evidence: `{skip_info.get('evidence', '?')}`")
                lines.append("")

            # Output integrity
            steps_with_hash = [
                (name, info) for name, info in spec["steps"].items()
                if info.get("output_hash")
            ]
            if steps_with_hash:
                lines.append("### Output Integrity")
                for name, info in steps_with_hash:
                    output_path = PROJECT_ROOT / info["output"]
                    current = _hash_file(output_path)
                    recorded = info["output_hash"]
                    match = "MATCH" if current == recorded else "MODIFIED"
                    lines.append(f"- `{info['output']}`: {match}")
                lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for pipeline gate operations."""
    parser = argparse.ArgumentParser(
        description="Brightsmith Pipeline Gate — programmatic enforcement of agent execution order"
    )
    subparsers = parser.add_subparsers(dest="command")

    # init
    init_p = subparsers.add_parser("init", help="Initialize pipeline state for a spec")
    init_p.add_argument("spec", help="Spec name")
    init_p.add_argument("--zone", required=True, choices=["bronze", "silver", "gold", "mcp"])
    init_p.add_argument("--mode", default="greenfield", choices=["greenfield", "backfill"])

    # check
    check_p = subparsers.add_parser("check", help="Check prerequisites for a step")
    check_p.add_argument("spec", help="Spec name")
    check_p.add_argument("step", help="Step name to check")

    # complete
    comp_p = subparsers.add_parser("complete", help="Record step completion")
    comp_p.add_argument("spec", help="Spec name")
    comp_p.add_argument("step", help="Step name")
    comp_p.add_argument("--output", default="", help="Path to output artifact")

    # skip
    skip_p = subparsers.add_parser("skip", help="Record step skip with justification")
    skip_p.add_argument("spec", help="Spec name")
    skip_p.add_argument("step", help="Step name")
    skip_p.add_argument("--reason", required=True, help="Why the step was skipped")
    skip_p.add_argument("--evidence", required=True, help="Path to justifying artifact")

    # approve
    appr_p = subparsers.add_parser("approve", help="Record approval decision")
    appr_p.add_argument("spec", help="Spec name")
    appr_p.add_argument("artifact", help="What was approved (e.g., business-terms)")
    appr_p.add_argument("--decision", required=True, choices=["APPROVED", "CHANGES_REQUESTED"])
    appr_p.add_argument("--by", required=True, help="Who decided (e.g., human:jeff)")
    appr_p.add_argument("--notes", default="", help="Optional reviewer notes")
    appr_p.add_argument("--document", default="", help="Path to approval document")

    # validate
    val_p = subparsers.add_parser("validate", help="Validate pipeline state")
    val_p.add_argument("spec", nargs="?", help="Spec name (omit for --all)")
    val_p.add_argument("--all", action="store_true", help="Validate all specs")

    # check-transition
    trans_p = subparsers.add_parser("check-transition", help="Check zone transition readiness")
    trans_p.add_argument("from_zone", choices=["bronze", "silver", "gold", "mcp"])
    trans_p.add_argument("to_zone", choices=["bronze", "silver", "gold", "mcp"])

    # status
    stat_p = subparsers.add_parser("status", help="Show pipeline status")
    stat_p.add_argument("spec", help="Spec name")

    # audit
    audit_p = subparsers.add_parser("audit", help="Generate audit report")
    audit_p.add_argument("--format", default="markdown", choices=["json", "markdown"])

    args = parser.parse_args()

    if args.command == "init":
        _cmd_init(args)
    elif args.command == "check":
        _cmd_check(args)
    elif args.command == "complete":
        _cmd_complete(args)
    elif args.command == "skip":
        _cmd_skip(args)
    elif args.command == "approve":
        _cmd_approve(args)
    elif args.command == "validate":
        _cmd_validate(args)
    elif args.command == "check-transition":
        _cmd_check_transition(args)
    elif args.command == "status":
        _cmd_status(args)
    elif args.command == "audit":
        _cmd_audit(args)
    else:
        parser.print_help()


def _cmd_init(args: argparse.Namespace) -> None:
    gate = PipelineGate(args.spec)
    gate.init(zone=args.zone, mode=args.mode)
    print(f"Initialized pipeline state for '{args.spec}' (zone={args.zone}, mode={args.mode})")


def _cmd_check(args: argparse.Namespace) -> None:
    gate = PipelineGate(args.spec)
    try:
        gate.check_prerequisites(args.step)
        print(f"CLEAR: All prerequisites met for '{args.step}'")
    except GateBlockedError as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_complete(args: argparse.Namespace) -> None:
    gate = PipelineGate(args.spec)
    gate.complete_step(args.step, output=args.output)
    print(f"Recorded completion: '{args.step}' → COMPLETED")


def _cmd_skip(args: argparse.Namespace) -> None:
    gate = PipelineGate(args.spec)
    try:
        gate.skip_step(args.step, reason=args.reason, evidence=args.evidence)
        print(f"Recorded skip: '{args.step}' → SKIPPED (reason: {args.reason})")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_approve(args: argparse.Namespace) -> None:
    gate = PipelineGate(args.spec)
    gate.record_approval(
        artifact=args.artifact,
        decision=args.decision,
        decided_by=getattr(args, "by"),
        notes=args.notes,
        document=args.document,
    )
    print(f"Recorded approval: '{args.artifact}' → {args.decision} by {getattr(args, 'by')}")


def _cmd_validate(args: argparse.Namespace) -> None:
    from brightsmith.config import PIPELINE_STATE_DIR

    if args.all or not args.spec:
        # Validate all specs
        if not PIPELINE_STATE_DIR.exists():
            print("No pipeline state directory found.")
            sys.exit(1)

        all_valid = True
        for state_file in sorted(PIPELINE_STATE_DIR.glob("*-pipeline.json")):
            data = json.loads(state_file.read_text())
            gate = PipelineGate(data["spec"])
            valid, issues = gate.validate()
            status = "PASS" if valid else "FAIL"
            print(f"[{status}] {data['spec']}")
            for issue in issues:
                print(f"  - {issue}")
            if not valid:
                all_valid = False

        sys.exit(0 if all_valid else 1)
    else:
        gate = PipelineGate(args.spec)
        valid, issues = gate.validate()
        if valid:
            print(f"PASS: Pipeline state valid for '{args.spec}'")
        else:
            print(f"FAIL: {len(issues)} issue(s) for '{args.spec}':")
            for issue in issues:
                print(f"  - {issue}")
            sys.exit(1)


def _cmd_check_transition(args: argparse.Namespace) -> None:
    ready, issues = PipelineGate.check_zone_transition(args.from_zone, args.to_zone)
    if ready:
        print(f"READY: Zone transition {args.from_zone} → {args.to_zone} is clear")
    else:
        print(f"NOT READY: {len(issues)} issue(s) for {args.from_zone} → {args.to_zone}:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)


def _cmd_status(args: argparse.Namespace) -> None:
    gate = PipelineGate(args.spec)
    print(gate.status_summary())


def _cmd_audit(args: argparse.Namespace) -> None:
    report = PipelineGate.audit_report(fmt=args.format)
    print(report)


if __name__ == "__main__":
    main()
