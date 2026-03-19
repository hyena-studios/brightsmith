---
description: Generate and verify data contracts — the hallmark of authenticity. Use to stamp data products with quality guarantees.
argument-hint: "[contract-name or --all]"
allowed-tools: Read, Write, Bash, Glob, Grep
context: fork
---

Generate and verify data contracts for "$ARGUMENTS".

This is the "stamp" step — marking the finished product with a hallmark of authenticity.

1. **List contracts** — `python3 -m brightsmith.infra.contract list`
2. **Generate** (if needed) — `python3 -m brightsmith.infra.contract generate --table {table} --spec {spec}`
3. **Verify** — `python3 -m brightsmith.infra.contract verify "$ARGUMENTS"` (or `--all`)
4. **Diff** — `python3 -m brightsmith.infra.contract diff "$ARGUMENTS"` (detect schema drift)
5. **Report** — Show contract status, version, and any violations

If breaking changes detected, report the required version bump.
