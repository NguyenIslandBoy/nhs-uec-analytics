# NHS Urgent & Emergency Care — Analytics Engineering Warehouse

A dbt-modelled warehouse over published NHS provider-level urgent-care returns, engineered to
survive organisational mergers, mid-series metric introductions, definition changes and
post-publication restatement — producing probabilistic 14-day attendance forecasts and a
capacity-gap mart that quantifies, per trust, the clinical hours required for the four-hour
standard to be arithmetically attainable at P90 demand.

> **Status:** Phase 0 (scaffold). Nothing below is built yet.

## Why this exists

The interesting question is not whether A&E attendances can be forecast. It is whether the
four-hour standard is attainable at plausible demand, given each trust's own historically
demonstrated throughput. Answering that requires a warehouse that does not quietly corrupt a
15-year series when a trust merges, when a metric is introduced mid-collection, or when a
published figure is revised weeks later.

## Architecture

Bronze (raw, append-only) -> Silver (conformed, long-format) -> Gold (star schema) -> marts.
See `docs/` for the full plan.

## Design decisions

Recorded as ADRs in `docs/adr/`.

## Data

All sources are published aggregate statistics. **Raw source files are not committed** —
`ingest/` contains the code to retrieve them and `tests/fixtures/` contains small samples for
testing. See `docs/sources.md` for the source register and licences.

## Limitations

This project uses published aggregate data only. Capacity is inferred from each provider's own
historically demonstrated throughput, not from staffing establishment data, which is not public.
The capacity gap should be read as a lower bound on structural infeasibility, not an operational
staffing recommendation. Forecast intervals are empirical and their calibration is reported
alongside them.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[transform,dev]"

$env:DBT_PROFILES_DIR = "$PWD\transform"
cd transform
dbt deps
dbt build
```
