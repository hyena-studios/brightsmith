# Contributing to Brightsmith

Brightsmith is alpha software. Bug reports, focused fixes, documentation improvements, and small framework extensions are welcome.

## Before opening a change

- Search the issue tracker for related work.
- Open an issue before a large architectural change so the design can be discussed before implementation.
- Keep domain-specific sources, business rules, and governance artifacts in the consuming domain project. This repository contains only domain-agnostic framework behavior and examples.
- Never include production data, credentials, customer names, or proprietary schemas in an issue, fixture, or pull request.

## Development setup

Requirements: Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/hyena-studios/brightsmith.git
cd brightsmith
uv sync --dev
```

Run the same checks enforced by CI:

```bash
uv run ruff check src tests
uv run pyright
uv run python -m pytest tests/ -q --cov=brightsmith --cov-fail-under=75
bash scripts/consumer_journey_smoke.sh
```

The consumer-journey test takes longer because it builds and installs a wheel into an isolated environment.

## Pull requests

- Explain the user-visible problem and the chosen solution.
- Add a regression test for fixes and tests for new behavior.
- Update the README or changelog when interfaces or documented behavior change.
- Preserve loud-failure behavior for governance, contracts, and quality gates.
- Keep commits free of generated warehouses, local runtime artifacts, and secrets.

By submitting a contribution, you agree that it is licensed under the repository's [Apache License 2.0](LICENSE).
