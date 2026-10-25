# End-to-end proof

Brightsmith includes an executable proof of its documented consumer journey. It exercises the built package from outside the source checkout, which catches packaging and onboarding failures that in-repository unit tests cannot.

## Run it

Requirements: Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/hyena-studios/brightsmith.git
cd brightsmith
bash scripts/consumer_journey_smoke.sh
```

The script uses a temporary directory and removes it when finished. It does not modify a consumer project or require credentials.

## What it proves

The script performs seven independently checked stages:

1. Builds a Brightsmith wheel.
2. Installs that wheel into a clean virtual environment outside the repository.
3. Scaffolds a new domain project through the public setup module.
4. Verifies that required DQ templates and warehouse directories were packaged correctly.
5. Installs a small fixture domain pack.
6. Runs bronze ingestion and verifies real rows in an Apache Iceberg table.
7. Queries those rows through both MCP-facing query APIs.

A successful run ends with:

```text
verified: rows in bronze.stub_facts via read_with_duckdb
verified: query_iceberg returned real rows from a live Iceberg table
verified: query_iceberg_simple returned real rows
== Consumer-journey smoke test PASSED ==
```

Exact row counts are intentionally omitted here because the fixture may evolve. Every assertion runs against the live table created during that invocation.

## Two-minute screen-recording outline

For a product demo or portfolio post:

1. Show the raw fixture ingestor and its three input rows.
2. Run `bash scripts/consumer_journey_smoke.sh`.
3. While it runs, show the architecture diagram in the README and explain that the test installs the wheel outside the checkout.
4. Highlight the successful Iceberg read and MCP query lines.
5. End on the governance features: DQ rules, contracts, lineage, and metadata-enriched responses.

This is a functional correctness demonstration, not a throughput or production-scale benchmark.
