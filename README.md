# NHS Urgent & Emergency Care — Analytics Engineering Warehouse

A dbt-modelled warehouse over published NHS provider-level urgent-care returns, engineered to
survive organisational mergers, mid-series metric introductions, definition changes and
post-publication restatement — producing probabilistic 14-day attendance forecasts and a
capacity-gap mart that quantifies, per trust, the clinical hours required for the four-hour
standard to be arithmetically attainable at P90 demand.

> **Status:** Phase 1 complete — provider dimension and merger lineage.
> Phases 2–6 (situation reports, restatement vintages, forecasting, capacity gap) in progress.

## Why this exists

The interesting question is not whether A&E attendances can be forecast. It is whether the
four-hour standard is attainable at plausible demand, given each trust's own historically
demonstrated throughput. Answering that requires a warehouse that does not quietly corrupt a
multi-year series when a trust merges, when a metric is introduced mid-collection, or when a
published figure is revised weeks later.

## Phase 1 result — provider dimension

The NHS Organisation Data Service publishes no historical extracts. Its Data Search and Export
reports update nightly and no periodic snapshot archive exists, so a `dbt snapshot` started
today would capture no history at all. Validity is therefore **derived** from the effective
dates and legal succession records the ORD API carries, rather than observed over time.

| | |
|---|---|
| Organisations in corpus | **668** |
| — from NHS Trust search (RO197) | 544 (247 active, 297 inactive) |
| — pulled in by succession closure | 124 |
| Raw succession edges | 521 |
| Canonical edges after normalisation | **453** |
| Demergers flagged, not resolved | 23 |
| dbt models / tests | 5 / 41 |

Four findings drove the design, each recorded in `docs/known-issues.md`:

**Succession is recorded bidirectionally.** Both `Predecessor` and `Successor` edge types
appear on both active and inactive records, with different `uniqueSuccId` values for the two
directions of one merger. Edges are normalised to a canonical direction and deduplicated on the
code pair — 68 of 521 raw edges were duplicates. Without this the dimension would carry phantom
lineage no downstream test would catch.

**Succession crosses primary-role boundaries.** An NHS Trust may have a Care Trust (RO107) or
Directly Managed Unit (RO111) predecessor, so a role-scoped extract has dangling references.
`ingest.ods closure` walks the graph outward and asserts convergence: 121 organisations in wave
one, 3 in wave two, zero unresolved. Two dbt `relationships` tests enforce closure at the
warehouse level.

**Demergers make lineage many-to-many.** 23 predecessors have multiple terminal successors, and
two split across an identical successor pair. No single-parent assignment is correct, so
`bridge_provider_lineage` is many-to-many with an `is_ambiguous` flag and marts must exclude
ambiguous lineage from national totals rather than guess.

**Foundation Trust is a non-primary role.** 161 of 544 trusts hold RO57 alongside RO197, and are
invisible to a `PrimaryRoleId`-based classification. `org_type` derives from the full role set.

Three succession pairs carry dates one day apart across a boundary (e.g. 2018-05-31 /
2018-06-01) — the predecessor's final operational day and the successor's first, recorded
separately. The later date is retained, since it is the correct attribution boundary for daily
activity data.

## Architecture

```
ingest/          ODS ORD API client (rate-limited, retrying, provenance-stamped)
  |
data/raw/        Bronze: immutable JSON + sha256 manifests (gitignored)
  |
data/staging/    Flattened Parquet extracts
  |
transform/       dbt: staging -> intermediate -> marts
                   stg_ods__organisation, stg_ods__succession
                   int_provider_lineage (recursive CTE, depth-guarded)
                   dim_provider (SCD2), bridge_provider_lineage
```

## Design decisions

Recorded as ADRs in `docs/adr/`. Data anomalies and their handling are in
`docs/known-issues.md` (ODS-01 … ODS-16).

## Data

All sources are published aggregate statistics. **Raw source files are not committed** —
`ingest/` retrieves them and `tests/fixtures/ods/` holds a closed 12-record subgraph for
testing. The ORD API is open-access and requires no authentication. It is under review for
deprecation; the stated successor is the Organisation Data Terminology FHIR R4 API, and
ingestion is isolated so the warehouse is unaffected by that migration.

## Limitations

This project uses published aggregate data only. Capacity will be inferred from each provider's
own historically demonstrated throughput, not from staffing establishment data, which is not
public. The capacity gap should be read as a lower bound on structural infeasibility, not an
operational staffing recommendation. Forecast intervals will be empirical and their calibration
reported alongside them.

## Local setup

Requires Python 3.11–3.14.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[transform,dev]"

python -m ingest.ods backfill --primary-role-id RO197 --status Active
python -m ingest.ods backfill --primary-role-id RO197 --status Inactive
python -m ingest.ods closure
python -m ingest.parse_ods --corpus "data/raw/ods_organisations/ingest_date=YYYY-MM-DD"

$env:DBT_PROFILES_DIR = "$PWD\transform"
cd transform
dbt deps
dbt build
```

CI builds against the committed fixtures instead of the live API:

```bash
python tests/build_ci_fixtures.py
cd transform && dbt build --target ci --vars '{staging_dir: staging-ci}'
```