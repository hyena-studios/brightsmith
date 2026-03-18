---
description: Show Grist pipeline progress — which specs exist, their status, which agents have run, DQ pass rates, and what's next. Use to get a quick overview of project state.
allowed-tools: Read, Bash, Glob, Grep
context: fork
---

Show pipeline status for this Grist project.

Check and report:

1. **Specs** — List all specs in `docs/specs/`, their status (DRAFT/IN_PROGRESS/COMPLETE), and zone
2. **DQ Rules** — Run `python -m grist.infra.dq_runner status` to show rule counts by status
3. **DQ Results** — Check `governance/dq-results/` for latest run results and P0 gate status
4. **Governance Artifacts** — Check which artifacts exist:
   - `governance/domain-context.md` (exists?)
   - `governance/models/` (which specs have models?)
   - `governance/eda/` (which EDA reports exist?)
   - `governance/insights/` (which insight reports exist?)
   - `governance/golden-datasets/` (which golden datasets exist?)
5. **Data** — Check what Iceberg tables exist in `data/`
6. **Next Steps** — Based on what's complete, recommend what to do next

Format as a concise dashboard, not a wall of text.
