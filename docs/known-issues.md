# Known issues

Data anomalies encountered, the handling decision, and the reasoning. Append as found.

## ODS ORD API

### ODS-01 - `Roles` container type varies by endpoint
`GET /roles` returns `{"Roles": [ ... ]}` (a bare list). An organisation record returns
`{"Roles": {"Role": [ ... ]}}` (a dict wrapping a list). Same key, different container.
**Handling:** separate parsers for metadata and organisation payloads; never share an accessor.

### ODS-02 - `primaryRole` is a string in metadata, a boolean in records
`/roles` gives `"primaryRole": "true"`; an organisation record gives `"primaryRole": true`.
**Handling:** coerce explicitly at the parse boundary; no truthiness checks, since the
string `"false"` is truthy in Python.

### ODS-03 - Empty sections are omitted, not returned empty
RJY has no `Succs` or `Rels` keys at all rather than empty containers.
**Handling:** all accessors default via `.get(...)`. A missing key means "none recorded",
which is not the same as "none exists" - see ODS-04.

### ODS-04 - Succession history is incompletely populated
RJY (Wigan and Leigh Health Services NHS Trust) ceased operation on 2001-03-31 but carries
no succession record. NHS England documents relationships as being returned "including
history where it was captured by ODS" - the qualifier implies partial coverage.
**Handling:** TBD pending the coverage measurement in `scripts/`. If coverage is materially
incomplete across trusts active during the analysis window, lineage falls back to a
hand-curated seed, clearly labelled as manually compiled. See ADR-0002.

### ODS-05 - Foundation Trust is a non-primary role
`RO197 NHS TRUST` is primary; `RO57 FOUNDATION TRUST` is non-primary. Foundation Trusts hold
both. **Handling:** `org_type` is derived from the full role set, not from `PrimaryRoleId`.

### ODS-06 - `refOnly` flag
Present on RJY as `true`. Meaning not yet established; may indicate a reference-only record
with reduced detail, which would partly explain ODS-04. **Handling:** carry through to
staging unchanged and revisit once coverage across the trust population is measured.
### ODS-07 - Search endpoint rejects some documented parameter combinations
`organisations?PrimaryRoleId=RO197&Status=Inactive&Limit=1000&Offset=0` returns HTTP 406
Not Acceptable. The documented examples pass `PrimaryRoleId` alone. Other consumers have
reported 406 specifically when `PrimaryRoleId` is combined with additional parameters.
**Handling:** parameter matrix bisected in `scripts/diagnose_ods_search.py`; the accepted
combination is recorded below and encoded in `OdsClient.search_organisations`.

**Resolved.** The 406 was caused by `Offset=0`, not by parameter combination.
The API returns `{"errorCode":406,"errorText":"Suppied Offset must be greater than 1"}`
(sic). Offset is 1-based; the first page must omit it. `PrimaryRoleId` and `Status`
combine without issue. `Status` is case-insensitive.

### ODS-08 - Use X-Total-Count rather than short-page detection
The search endpoint returns `X-Total-Count`. For `PrimaryRoleId=RO197`: 544 total,
247 Active, 297 Inactive (247 + 297 = 544, so status is exhaustive and non-overlapping).
**Handling:** pagination asserts the final count against `X-Total-Count` and raises on
mismatch. Inferring completion from a short page would fail silently and under-populate
the provider dimension.

### ODS-06 - RESOLVED: `refOnly` explains absent sections
`refOnly=true` records are stripped legacy stubs carrying dates only. Across 544 NHS Trust
records, no `refOnly=true` record carries `Succs` (270 / 0). 90.9% of inactive trusts are
`refOnly`; 0% of active trusts are. **Handling:** `refOnly` is carried into staging as a
flag and asserted with a dbt test.

### ODS-09 - Succession is recorded bidirectionally
Both `Predecessor` and `Successor` edge types occur on both active and inactive records
(406 / 43 / 40 / 25). One merger can appear as two edges from opposite ends with different
`uniqueSuccId` values. **Handling:** normalise to canonical (predecessor, successor)
direction at the parse boundary; deduplicate on the code pair plus legal date, never on
`uniqueSuccId`.

### ODS-10 - Succession crosses primary-role boundaries
NHS Trust (RO197) records reference predecessors with other primary roles, e.g. Care Trust
(RO107). A role-scoped extract is structurally incomplete for lineage. Transitive closure from
the 544 RO197 records required 121 further organisations in wave 1 and 3 in wave 2, converging
at 668 with zero unresolved. **Handling:** `ingest.ods closure` walks the graph and asserts
convergence; the corpus is the closure, not the search result.

### ODS-11 - ODS codes are not fixed width
Both legacy three-character codes (RJY, R1G) and newer five-character ANANA codes (G6V2S)
occur. **Handling:** never infer organisation type or validity from code length or format.
