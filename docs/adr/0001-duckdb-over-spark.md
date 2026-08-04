# ADR-0001: DuckDB and dbt-duckdb as the warehouse

- **Status:** Accepted
- **Date:** 2026-08-04

## Context

The dataset is 15 years of provider-level daily and monthly returns across roughly 200-250
reporting organisations. Upper bound on the largest fact table is in the low tens of millions of
rows in long format. The project is positioned as an analytics-engineering piece: the value is in
dimensional modelling, data contracts and restatement handling, not in distributed compute.

## Options considered

| Option | Assessment |
|---|---|
| DuckDB + dbt-duckdb | Single file, zero infrastructure, full dbt feature set, sub-second queries at this volume |
| Databricks / Delta Lake | Correct at 1000x this volume; here it adds cluster setup, cost and latency for no analytical benefit |
| BigQuery / Snowflake | Cloud credentials and cost for a public-data project; slower local iteration |
| Postgres | Viable, but row-store performance on analytical scans is worse and it adds a service to run |

## Decision

DuckDB via `dbt-duckdb`, with the warehouse file gitignored and rebuildable from `ingest/`.
A `ci` target is defined in the same profile so continuous integration builds against a separate
database file.

## Consequences

- The entire warehouse rebuilds from empty in seconds, which makes aggressive `--full-refresh`
  iteration cheap and encourages frequent full rebuilds rather than incremental patching.
- No concurrency story. Acceptable: this is a single-author analytical warehouse, not a
  multi-tenant platform.
- Portability is not free but is cheap: dbt models are written against ANSI-leaning SQL and a
  second `dbt` target can be added later to demonstrate migration.
- **Choosing not to use Spark is itself the decision worth defending.** Reaching for distributed
  compute at this volume would be a signal of poor judgement, not of capability. The streaming
  companion project is where scale genuinely forces different choices.
