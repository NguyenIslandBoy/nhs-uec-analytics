# ADR-0002: Deriving provider SCD2 and merger lineage from ODS

- **Status:** Accepted
- **Date:** 2026-08-04

## Context

`dim_provider` must be a Type 2 slowly changing dimension covering the full analysis window,
and must resolve trust mergers so multi-year attendance series survive reorganisation.

The original plan specified a `dbt snapshot` over an ODS reference feed. That mechanism does
not work here. ODS has moved away from periodic snapshot files; the current Data Search and
Export reports are dynamic and update nightly, and no archive of historical extracts is
published. A snapshot started today records changes only from today, leaving the preceding
15 years empty.

## Investigation

544 NHS Trust records (primary role RO197) were retrieved: 247 Active, 297 Inactive.
Full records were profiled for section coverage.

| Section | Active (247) | Inactive (297) |
|---|---|---|
| `Succs` | 89.9% | 8.8% |
| `Rels` | 99.6% | 9.1% |
| `refOnly` | 0% | 90.9% |
| `Date` | 100% | 100% |

Four findings:

1. **`refOnly` fully explains missing sections.** Across all 544 records, no `refOnly=true`
   record carries `Succs` (270 / 0 split). These are stripped legacy stubs holding dates only.
2. **Succession is recorded bidirectionally.** Both `Predecessor` and `Successor` edge types
   occur on both active and inactive records (406 / 43 / 40 / 25). A single merger can appear
   as two edges from opposite ends, with different `uniqueSuccId` values.
3. **Succession crosses primary-role boundaries.** An NHS Trust may have a Care Trust (RO107)
   predecessor, so a role-scoped extract contains dangling references. Transitive closure from
   the RO197 set required 121 additional organisations in wave 1 and 3 in wave 2, converging at
   668 records with zero unresolved codes.
4. **Operational dates are equivalent to status.** Every record has an Operational date; the
   presence of an `End` matches `Status='Inactive'` exactly (297/297 and 247/247).

## Decision

- **Validity intervals** derive from the Operational `Date` entry: `valid_from = Start`,
  `valid_to = End` where present, otherwise `9999-12-31`. `Legal` dates are a separate,
  optional concept (94 records) and are carried through but not used for validity.
- **Lineage** derives from `Succs`, normalised to a canonical direction before use:
  a `Predecessor` edge on X targeting Y becomes `(predecessor=Y, successor=X)`; a `Successor`
  edge on X targeting Y becomes `(predecessor=X, successor=Y)`. Edges are deduplicated on
  `(predecessor, successor, legal_start_date)`. `uniqueSuccId` is not a valid dedup key,
  since the two directions of one merger carry different IDs.
- **Corpus scope** is the transitive closure of succession references, not the RO197 search
  result. Closure is asserted, not assumed.
- **A `dbt snapshot`** runs over the raw pull to capture future undated drift. It is
  supplementary; it contributes nothing historical.
- **The curated lineage seed** contemplated as a fallback is not required. Succession coverage
  on active records (89.9%) is sufficient, and the low inactive coverage is explained by
  `refOnly` rather than by missing history.

## Consequences

- Validity is derived from source effective-dating rather than observed over time, so the full
  history is available immediately and is reproducible from a cold start.
- The bidirectional edge normalisation is essential; a naive parse double-counts every merger.
- `Rels` is deprioritised. It is dominated by RE5 "in the geography of" (1,688 of 2,028
  relationships), which ODS advises against in favour of dynamic postcode-based mapping,
  and it is not lineage.
- The ORD API is under review for deprecation; the stated successor is the Organisation Data
  Terminology FHIR R4 API. Ingestion is isolated in `ingest/ods.py` so the parser and warehouse
  are unaffected by a future source migration.
---

## Addendum: demergers and the many-to-many bridge

**Date:** 2026-08-04

The decision above assumed lineage collapses to a single terminal successor per organisation.
Profiling the parsed graph showed it does not.

### Finding

23 predecessors have more than one terminal successor. Examples:

| Predecessor | Successors |
|---|---|
| RAV (The Guys and Lewisham NHS Trust) | RJ1, RJ2 |
| RA5 (East Gloucestershire NHS Trust) | RTE, RTQ |
| REX (Oldham NHS Trust) | RT2, RW6 |
| REZ (Rochdale Healthcare NHS Trust) | RT2, RW6 |

REX and REZ split across an identical successor pair, so the graph is many-to-many in both
directions. Chains reach depth 3 (352 at depth 1, 95 at depth 2, 6 at depth 3).

### Consequence for the original options

The plan offered two designs:

- **Option A** - a successor rollup bridge giving continuous series
- **Option B** - explicit non-comparability flags, leaving series separate

Neither survives contact with a demerger. Option A requires a single reporting entity per
provider; assigning one would either duplicate historic activity across both successors,
inflating national totals, or pick one arbitrarily. Option B alone discards the 400-odd
lineage relationships that are unambiguous.

### Revised decision

`bridge_provider_lineage` is many-to-many with an `is_ambiguous` flag:

- every provider maps to itself at `hops = 0`, so marts join unconditionally rather than
  coalescing between "has lineage" and "does not"
- unambiguous lineage (1,072 bridge rows) resolves to a terminal successor
- ambiguous lineage (49 rows across 23 predecessors) is retained and flagged; marts producing
  national totals must filter `is_ambiguous = false`
- `dim_provider.terminal_successor_code` is null where lineage is ambiguous, and
  `has_ambiguous_lineage` records why, so the two cases are distinguishable without a join

### Consequences

- Correctness is chosen over convenience: an ambiguous rollup is refused rather than guessed.
  The alternative would silently double-count activity for 23 organisations.
- Resolution requires a recursive CTE with a depth guard and cycle detection, not a self-join.
  No cycles were observed; the guard is retained as a regression protection.
- The bridge grain is `(provider_sk, reporting_entity_code)`, asserted by a dbt test.
  Bridge row count reconciles exactly: 668 self-edges + 453 lineage edges = 1,121.
