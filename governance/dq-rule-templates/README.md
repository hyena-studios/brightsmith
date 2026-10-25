# DQ rule templates (framework source of truth)

Mandatory gold-zone DQ rule patterns (`docs/` and specs in this repo
reference this directory directly). This is the canonical, human-edited
copy.

A second copy lives at `src/brightsmith/_templates/dq-rule-templates/` —
that's the one that actually ships inside the pip-installable wheel (this
`governance/` directory is a sibling of `src/`, so it is invisible to a
`pip install` consumer; see `pyproject.toml`'s
`[tool.hatch.build.targets.wheel] packages = ["src/brightsmith"]`).
`brightsmith.setup._copy_dq_templates` reads from the packaged copy, not
this one (audit finding H4a).

If you edit a template here, update the packaged copy too (or script the
sync) so they don't drift.
