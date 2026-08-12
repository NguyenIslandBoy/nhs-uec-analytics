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

### ODS-12 - Paired succession dates one day apart
Three predecessor-successor pairs carry two succession records dated on consecutive days
across a boundary: R1E->RRE (2018-05-31 / 2018-06-01), RJF->RTG (2018-06-30 / 2018-07-01),
RY1->RW4 (2018-03-31 / 2018-04-01). These are one transaction recorded from both ends - the
predecessor's final operational day and the successor's first. **Handling:** second
deduplication pass on ``(predecessor_code, successor_code)`` retaining the **later** date,
which is the successor's operational start and therefore the correct attribution boundary for
daily activity. Retaining the earlier date would attribute the predecessor's final day of
activity to the successor. Conflicts are counted and logged, not silently resolved.

### ODS-13 - Demergers: 26 predecessors have multiple successors
e.g. `RAV` (The Guys and Lewisham NHS Trust) -> `RJ1`, `RJ2`. Historic activity cannot be
attributed to a single successor without an arbitrary choice. **Handling:** the lineage bridge
is many-to-many with an `is_ambiguous` flag; marts exclude ambiguous lineage from rollups by
default. See ADR-0002 addendum.

### ODS-14 - Succession chains reach depth 3
Hops to terminal successor: 339 at depth 1, 82 at depth 2, 6 at depth 3. **Handling:** lineage
resolution is a recursive CTE with a depth guard and cycle detection, not a self-join.

### ODS-15 - Nine primary roles participate in trust succession
RO197 (484), RO111 Directly Managed Unit (106), RO189 (6), RO179 PCT (4), RO191 (3), RO107 Care
Trust (2), RO106 (1), RO198 (1), RO114 (1). Confirms ODS-10 quantitatively: 124 of 668 corpus
organisations exist only because closure pulled them in.

### ODS-16 - Lineage depth differs between single-branch and exhaustive traversal
`scripts/profile_lineage.py` reports 339 / 82 / 6 at depths 1-3; `int_provider_lineage`
reports 352 / 95 / 6. The profiler follows one branch per node (`sorted(nxt)[0]`) to keep
cycle detection simple, whereas the dbt model enumerates every terminal successor. Demerged
organisations therefore contribute multiple paths in the model and one in the profiler.
**Handling:** the model is authoritative. The profiler is retained as an exploratory aid and
the discrepancy is expected, not a defect. Maximum depth agrees at 3 in both.

## Situation reports

### SITREP-01 - Weekly extract uploaded under the timeseries filename template
The Winter 2014-15 page links a weekly file whose href is
`DailySR-Timeseries-WE-14.12.141.xlsx` while its link text correctly reads
`Winter SitRep: Acute Web File 8 to 14 December 2014`. All 18 sibling weekly files use
`DailySR-Web-file-WE-*.xlsx`. **Handling:** the timeseries marker is matched on link text
only. Link text is editorially maintained; uploaded filenames are ad hoc. A selector keyed on
the href returns two candidates for this season.

### SITREP-02 - Season timeseries link naming spans six vocabularies
`Web File Timeseries - UEC Daily SitRep` (2023-24+), `UEC Daily SitRep - Web File
Timeseries` (2021-22, 2022-23), `UEC Daily SitRep - Acute Time Series` (2020-21),
`Winter SitRep - Acute Time series` (2017-18 to 2019-20), `Winter SitRep Part A: Acute Time
Series` (2014-15 to 2016-17), `Daily SR - Timeseries` (2012-13, 2013-14). Note 2012-13
contains neither "Winter" nor "SitRep". **Handling:** a positive timeseries requirement plus a
collection-name alternation; a negative "Web File" exclusion cannot be used because three
modern seasons are named "Web File Timeseries".

### SITREP-03 - Download URLs carry unguessable suffixes
e.g. `Web-File-Timeseries-UEC-Daily-SitRep-2526-ed389pw.xlsx`. **Handling:** URLs are
scraped per season, never constructed. The discovery manifest is written to the raw layer so
fetching is reproducible without re-scraping.

### SITREP-04 - Collection split across two publications
Urgent and Emergency Care Daily Situation Reports covers 2020-21 onward; the discontinued
Winter Daily Situation Reports covers 2012-13 to 2019-20. The index claims data back to
November 2010 but surfaces only 2012-13 onward. **Handling:** both collections are discovered
through one code path; the 2010-11 and 2011-12 seasons are out of scope pending a source.

### SITREP-05 - One legacy .xls file
12 of 13 season workbooks are `.xlsx`; 2012-13 is `.xls` (legacy BIFF). **Handling:**
`pandas.read_excel` dispatches on extension with both `openpyxl` and `xlrd` installed.
If 2012-13 fails to parse, the engine is the first thing to check.

## Situation reports

### SITREP-01 - Weekly extract uploaded under the timeseries filename template
The Winter 2014-15 page links a weekly file whose href is
`DailySR-Timeseries-WE-14.12.141.xlsx` while its link text correctly reads
`Winter SitRep: Acute Web File 8 to 14 December 2014`. All 18 sibling weekly files use
`DailySR-Web-file-WE-*.xlsx`. **Handling:** the timeseries marker is matched on link text
only. Link text is editorially maintained; uploaded filenames are ad hoc. A selector keyed on
the href returns two candidates for this season.

### SITREP-02 - Season timeseries link naming spans six vocabularies
`Web File Timeseries - UEC Daily SitRep` (2023-24+), `UEC Daily SitRep - Web File
Timeseries` (2021-22, 2022-23), `UEC Daily SitRep - Acute Time Series` (2020-21),
`Winter SitRep - Acute Time series` (2017-18 to 2019-20), `Winter SitRep Part A: Acute Time
Series` (2014-15 to 2016-17), `Daily SR - Timeseries` (2012-13, 2013-14). Note 2012-13
contains neither "Winter" nor "SitRep". **Handling:** a positive timeseries requirement plus a
collection-name alternation; a negative "Web File" exclusion cannot be used because three
modern seasons are named "Web File Timeseries".

### SITREP-03 - Download URLs carry unguessable suffixes
e.g. `Web-File-Timeseries-UEC-Daily-SitRep-2526-ed389pw.xlsx`. **Handling:** URLs are
scraped per season, never constructed. The discovery manifest is written to the raw layer so
fetching is reproducible without re-scraping.

### SITREP-04 - Collection split across two publications
Urgent and Emergency Care Daily Situation Reports covers 2020-21 onward; the discontinued
Winter Daily Situation Reports covers 2012-13 to 2019-20. The index claims data back to
November 2010 but surfaces only 2012-13 onward. **Handling:** both collections are discovered
through one code path; the 2010-11 and 2011-12 seasons are out of scope pending a source.

### SITREP-05 - One legacy .xls file
12 of 13 season workbooks are `.xlsx`; 2012-13 is `.xls` (legacy BIFF). **Handling:**
`pandas.read_excel` dispatches on extension with both `openpyxl` and `xlrd` installed.
If 2012-13 fails to parse, the engine is the first thing to check.

## Situation reports

### SITREP-01 - Weekly extract uploaded under the timeseries filename template
The Winter 2014-15 page links a weekly file whose href is
`DailySR-Timeseries-WE-14.12.141.xlsx` while its link text correctly reads
`Winter SitRep: Acute Web File 8 to 14 December 2014`. All 18 sibling weekly files use
`DailySR-Web-file-WE-*.xlsx`. **Handling:** the timeseries marker is matched on link text
only. Link text is editorially maintained; uploaded filenames are ad hoc. A selector keyed on
the href returns two candidates for this season.

### SITREP-02 - Season timeseries link naming spans six vocabularies
`Web File Timeseries - UEC Daily SitRep` (2023-24+), `UEC Daily SitRep - Web File
Timeseries` (2021-22, 2022-23), `UEC Daily SitRep - Acute Time Series` (2020-21),
`Winter SitRep - Acute Time series` (2017-18 to 2019-20), `Winter SitRep Part A: Acute Time
Series` (2014-15 to 2016-17), `Daily SR - Timeseries` (2012-13, 2013-14). Note 2012-13
contains neither "Winter" nor "SitRep". **Handling:** a positive timeseries requirement plus a
collection-name alternation; a negative "Web File" exclusion cannot be used because three
modern seasons are named "Web File Timeseries".

### SITREP-03 - Download URLs carry unguessable suffixes
e.g. `Web-File-Timeseries-UEC-Daily-SitRep-2526-ed389pw.xlsx`. **Handling:** URLs are
scraped per season, never constructed. The discovery manifest is written to the raw layer so
fetching is reproducible without re-scraping.

### SITREP-04 - Collection split across two publications
Urgent and Emergency Care Daily Situation Reports covers 2020-21 onward; the discontinued
Winter Daily Situation Reports covers 2012-13 to 2019-20. The index claims data back to
November 2010 but surfaces only 2012-13 onward. **Handling:** both collections are discovered
through one code path; the 2010-11 and 2011-12 seasons are out of scope pending a source.

### SITREP-05 - One legacy .xls file
12 of 13 season workbooks are `.xlsx`; 2012-13 is `.xls` (legacy BIFF). **Handling:**
`pandas.read_excel` dispatches on extension with both `openpyxl` and `xlrd` installed.
If 2012-13 fails to parse, the engine is the first thing to check.
