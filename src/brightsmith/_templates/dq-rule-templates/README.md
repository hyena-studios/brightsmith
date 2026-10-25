# DQ rule templates (packaged copy)

This is the copy of the mandatory gold-zone DQ rule pattern templates that
actually ships in the pip-installable wheel — `pyproject.toml`'s
`[tool.hatch.build.targets.wheel] packages = ["src/brightsmith"]` only
packages this directory tree, so `governance/dq-rule-templates/` at the repo
root (a sibling of `src/`) is invisible to a `pip install` consumer.

`brightsmith.setup._copy_dq_templates` reads from here, not from the repo
root, so it works identically whether run from a source checkout or an
installed wheel (audit finding H4a).

**Source of truth:** these files are a copy of `governance/dq-rule-templates/`
at the repo root. The framework repo's own docs/specs reference the repo-root
copy; if you're editing template content, update both locations (or script
the copy) so they don't drift.
