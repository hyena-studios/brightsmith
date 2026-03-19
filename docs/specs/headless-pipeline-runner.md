# Framework Spec: Headless Pipeline Runner

**Status:** DRAFT
**Zone:** Infrastructure (cross-cutting)
**Primary Agent:** @primary-agent
**Created:** 2026-03-19

## Problem Statement

Grist pipelines are developed by AI agents but the resulting code is pure Python — no LLM calls in the data path. However, there's no single command to run the full pipeline headlessly. Each zone has its own `run_*.py` script, DQ validation is separate, contract verification is separate, and there's no orchestration, error handling, or notification.

Once the AI agents have built and hardened a pipeline, it should run on a schedule (cron, Airflow, CI) with:
- One command to execute all zones in order
- DQ gates between zones (stop if P0 fails)
- Contract verification after each zone
- Structured output (JSON) for monitoring
- Non-zero exit code on failure
- No AI agent or LLM dependency

## The Two Modes

| Mode | When | Who | LLM Required |
|------|------|-----|-------------|
| **Development** | Building the pipeline | AI agents + human | Yes (Claude Code) |
| **Headless** | Running the pipeline | cron / scheduler / CI | No |

The AI agents are the **development team**. They write code, DQ rules, data contracts, and governance artifacts. Once the pipeline is approved by @staff-engineer and all contracts pass verification, it's ready for headless mode.

```
DEVELOPMENT MODE (AI agents)              HEADLESS MODE (scheduled)
┌──────────────────────────┐              ┌─────────────────────────┐
│ @setup scaffolds project │              │                         │
│ @primary-agent codes     │              │  python -m grist.run    │
│ @data-analyst profiles   │              │    --zone all           │
│ @dq-rule-writer writes   │   handoff   │    --validate           │
│ @chaos-monkey hardens    │ ──────────►  │    --notify             │
│ @staff-engineer approves │              │                         │
│ @doc-generator contracts │              │  (pure Python, no LLM)  │
└──────────────────────────┘              └─────────────────────────┘
```

## Success Criteria

- [ ] `python -m grist.run` executes the full pipeline (raw → base → consumable)
- [ ] DQ gates between zones: if P0 fails after raw, base doesn't run
- [ ] Contract verification after each zone write
- [ ] `--zone {raw|base|consumable|all}` flag to run specific zones
- [ ] `--dry-run` flag to check contracts and DQ without writing data
- [ ] `--validate-only` flag to run DQ + contracts without ingesting
- [ ] JSON output for monitoring integration
- [ ] Non-zero exit code on any failure
- [ ] No imports from `anthropic` or any LLM library
- [ ] Runnable via cron, Airflow, GitHub Actions, or any scheduler

## Technical Design

### 1. Pipeline Runner

**File:** `src/grist/run.py` (new)

```python
"""Headless pipeline runner.

Executes the full data pipeline without AI agents. All zone
transformations, DQ checks, and contract validations run as
pure Python code.

Usage:
    python -m grist.run                          # Full pipeline
    python -m grist.run --zone raw               # Raw zone only
    python -m grist.run --zone base              # Base zone only
    python -m grist.run --validate-only          # DQ + contracts, no data writes
    python -m grist.run --dry-run                # Check readiness, no execution
"""
```

### 2. Execution Flow

```python
def run_pipeline(zones: list[str], validate_only: bool = False) -> PipelineResult:
    results = PipelineResult()

    for zone in zones:
        # 1. Pre-flight: verify source zone contracts pass
        if zone != "raw":
            source_contracts = get_contracts_for_zone(previous_zone(zone))
            for contract in source_contracts:
                check = verify_contract(contract)
                if not check.passed:
                    results.add_failure(zone, f"Source contract failed: {contract.name}")
                    return results  # Stop — source data is not trustworthy

        # 2. Execute zone transformation (if not validate-only)
        if not validate_only:
            try:
                zone_result = execute_zone(zone)
                results.add_zone_result(zone, zone_result)
            except Exception as e:
                results.add_failure(zone, str(e))
                return results  # Stop — transformation failed

        # 3. Post-write: run DQ rules for this zone
        dq_result = run_dq_rules(zone)
        results.add_dq_result(zone, dq_result)
        if not dq_result.p0_passed:
            results.add_failure(zone, f"DQ P0 gate failed: {dq_result.p0_failures}")
            return results  # Stop — data quality breach

        # 4. Post-write: verify output contracts
        output_contracts = get_contracts_for_zone(zone)
        for contract in output_contracts:
            check = verify_contract(contract)
            results.add_contract_result(zone, contract.name, check)
            if not check.passed:
                results.add_warning(zone, f"Contract violation: {contract.name}")
                # Warning, not failure — contract violations are tracked but non-blocking
                # (per data-contracts.md: runtime violations log, don't block)

    # 5. Run golden dataset verification
    golden_result = verify_golden_datasets()
    results.add_golden_result(golden_result)

    return results
```

### 3. Zone Execution Registry

Domain projects register their zone transformers:

```python
# In the domain project's src/pipeline.py or similar:
from grist.run import register_zone

register_zone("raw", "raw.run_ingest:main")
register_zone("base", "base.run_transform:main")
register_zone("consumable", "consumable.run_transform:main")
```

Or via configuration in `domain/manifest.yaml`:

```yaml
pipeline:
  zones:
    raw:
      module: raw.run_ingest
      function: main
    base:
      module: base.run_transform
      function: main
    consumable:
      module: consumable.run_transform
      function: main
```

The framework discovers these at startup and executes them in order.

### 4. Structured Output

Every run produces a JSON result for monitoring:

```json
{
  "run_id": "a1b2c3d4",
  "started_at": "2026-03-19T06:00:00Z",
  "completed_at": "2026-03-19T06:03:42Z",
  "duration_seconds": 222,
  "status": "SUCCESS",
  "zones": {
    "raw": {
      "status": "SUCCESS",
      "rows_promoted": 150,
      "rows_skipped": 122097,
      "dq_rules_passed": 19,
      "dq_rules_failed": 0,
      "contracts_valid": 1,
      "contracts_violated": 0
    },
    "base": {
      "status": "SUCCESS",
      "rows_promoted": 75,
      "rows_skipped": 64043,
      "dq_rules_passed": 12,
      "dq_rules_failed": 0,
      "contracts_valid": 2,
      "contracts_violated": 0
    },
    "consumable": {
      "status": "SUCCESS",
      "rows_promoted": 30,
      "rows_skipped": 63554,
      "dq_rules_passed": 0,
      "dq_rules_failed": 0,
      "contracts_valid": 5,
      "contracts_violated": 0
    }
  },
  "golden_datasets": {
    "checked": 12,
    "passed": 12,
    "failed": 0,
    "pass_rate": 100.0
  }
}
```

Written to `governance/run-history/{timestamp}.json` for audit trail.

### 5. Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All zones succeeded, all DQ passed, all contracts valid |
| 1 | DQ P0 failure (pipeline stopped) |
| 2 | Transformation error (exception during zone execution) |
| 3 | Contract violation (warning-level, pipeline completed) |
| 4 | Configuration error (missing manifest, invalid zone registration) |

### 6. CLI

```bash
# Full pipeline
python -m grist.run

# Specific zone
python -m grist.run --zone raw
python -m grist.run --zone base
python -m grist.run --zone consumable

# Validation only (no data writes)
python -m grist.run --validate-only

# Dry run (check config and contracts, no execution)
python -m grist.run --dry-run

# Output format
python -m grist.run --output json          # JSON to stdout
python -m grist.run --output summary       # Human-readable summary (default)

# Notification (future: webhook, email, Slack)
python -m grist.run --notify webhook:https://hooks.example.com/pipeline
```

### 7. Scheduling Examples

#### Cron (daily at 6am)
```bash
0 6 * * * cd /path/to/project && GRIST_PROJECT_ROOT=. uv run python -m grist.run --output json >> /var/log/grist/runs.jsonl 2>&1
```

#### GitHub Actions
```yaml
name: Pipeline Run
on:
  schedule:
    - cron: '0 6 * * *'
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv sync
      - run: uv run python -m grist.run --output json
```

#### Airflow
```python
from airflow.operators.bash import BashOperator

run_pipeline = BashOperator(
    task_id='grist_pipeline',
    bash_command='cd /path/to/project && uv run python -m grist.run --output json',
)
```

### 8. What Stays AI-Only

| Capability | Headless | Why AI-Only |
|-----------|----------|-------------|
| Writing new DQ rules | No | Requires domain reasoning |
| Updating data contracts | No | Requires schema review |
| Chaos monkey hardening | No | One-time development activity |
| Adding new entities/sources | No | Requires domain pack changes |
| Handling new edge cases | No | Requires investigation + code changes |
| Concept normalization updates | No | Requires domain expertise for new mappings |
| Chat agent queries | Requires LLM | AI-Ready zone is the one LLM dependency |

The AI-Ready chat agent is the **only runtime LLM dependency**. Everything else is pure Python. If you don't need the chat agent, you don't need an API key at all.

### 9. Readiness Gate: When Is a Pipeline Ready for Headless?

Add to `pipeline_gate.py`:

```bash
python -m grist.infra.pipeline_gate headless-ready
```

Checks:
- [ ] All specs in all zones are COMPLETE
- [ ] All pipeline validations PASS
- [ ] All active contracts PASS verification
- [ ] All golden datasets PASS
- [ ] Zone transformation modules are registered in manifest
- [ ] No `anthropic` imports in any zone transformation code
- [ ] DQ rules exist for every consumable table

Output:
```
Headless readiness check:
  Specs complete:        9/9  ✓
  Pipeline validations:  9/9  ✓
  Contracts valid:       8/8  ✓
  Golden datasets:       12/12 ✓
  Zone modules:          3/3  ✓
  No LLM imports:        ✓
  DQ coverage:           31 rules across 8 tables ✓

READY for headless execution.
```

## Tests

- `tests/infra/test_pipeline_runner.py`:
  - `test_full_pipeline_runs_all_zones_in_order`
  - `test_dq_p0_failure_stops_pipeline`
  - `test_contract_violation_warns_but_continues`
  - `test_validate_only_skips_data_writes`
  - `test_dry_run_checks_config_only`
  - `test_zone_filter_runs_only_specified_zone`
  - `test_json_output_format_valid`
  - `test_exit_code_0_on_success`
  - `test_exit_code_1_on_dq_failure`
  - `test_run_history_written_to_governance`
  - `test_no_anthropic_imports_in_pipeline_code`

- `tests/infra/test_headless_readiness.py`:
  - `test_incomplete_specs_block_readiness`
  - `test_missing_contracts_block_readiness`
  - `test_failing_golden_datasets_block_readiness`
  - `test_llm_imports_in_zone_code_block_readiness`
  - `test_all_checks_pass_reports_ready`

## Relationship to Other Specs

- **idempotent-promote-pattern.md**: Headless re-runs depend on idempotent promotes. Without grain-based dedup, re-running doubles data.
- **data-contracts.md**: Headless runner verifies contracts between zones and after writes.
- **framework-quality-parity.md (Change 6-7)**: Golden dataset verification runs as part of headless pipeline.
- **adversarial-dq-hardening.md**: The DQ rules written during development are what the headless runner enforces at runtime.

## Implementation Order

1. `PipelineResult` dataclass and zone execution registry
2. `run_pipeline()` orchestrator with DQ gates
3. Contract verification integration between zones
4. CLI with `--zone`, `--validate-only`, `--dry-run`, `--output`
5. Run history logging to `governance/run-history/`
6. `headless-ready` gate check
7. Scheduling examples in docs
