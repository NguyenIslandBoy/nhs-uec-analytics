"""Ingest NHS Organisation Data Service (ODS) reference data via the ORD API.

Source:  https://directory.spineservices.nhs.uk/ORD/2-0-0/
Access:  open (no authentication, no onboarding)
Limits:  guidance is to stay below 5 requests/second
Status:  the ORD API is under review for deprecation; the stated successor is the
         Organisation Data Terminology FHIR R4 API. See docs/adr/0002.

Why this API rather than the CSV downloads or a dbt snapshot: ODS no longer publishes
historical point-in-time extracts, so SCD2 validity cannot be observed over time. The
ORD payload carries effective dates and legal succession records, which lets validity
intervals be derived for the full history of each organisation.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from ingest._io import utc_now_iso, write_raw_json
from ingest.parse_ods import _succession_targets

LOG = logging.getLogger(__name__)

BASE_URL = "https://directory.spineservices.nhs.uk/ORD/2-0-0"
USER_AGENT = "nhs-uec-analytics/0.1 (portfolio project; +https://github.com/NguyenIslandBoy/nhs-uec-analytics)"

# Guidance is <5 req/s. 0.3s between calls leaves generous headroom.
MIN_REQUEST_INTERVAL_S = 0.3
SEARCH_PAGE_SIZE = 1000
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class OdsFetchError(RuntimeError):
    """Raised when the ORD API cannot be read after all retries."""


class OdsClient:
    """Thin, rate-limited, retrying JSON client for the ORD API."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        min_interval_s: float = MIN_REQUEST_INTERVAL_S,
        max_attempts: int = 4,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = min_interval_s
        self.max_attempts = max_attempts
        self.timeout_s = timeout_s
        self._last_request_at = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_request_at = time.monotonic()

    def _request(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        """Throttled, retrying GET. Retries transient statuses only."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            try:
                response = self._session.get(url, params=params, timeout=self.timeout_s)
            except requests.RequestException as exc:
                last_error = exc
                LOG.warning("attempt %s/%s failed for %s: %s", attempt, self.max_attempts, url, exc)
            else:
                if response.status_code == 200:
                    return response
                if response.status_code not in RETRY_STATUSES:
                    raise OdsFetchError(
                        f"HTTP {response.status_code} for {response.url}: {response.text[:200]}"
                    )
                last_error = OdsFetchError(f"HTTP {response.status_code} for {response.url}")
                LOG.warning(
                    "attempt %s/%s got HTTP %s",
                    attempt,
                    self.max_attempts,
                    response.status_code,
                )

            if attempt < self.max_attempts:
                time.sleep(2 ** (attempt - 1))

        raise OdsFetchError(f"exhausted {self.max_attempts} attempts for {url}") from last_error

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict, str]:
        response = self._request(path, params)
        return response.json(), response.url

    def get_with_headers(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[dict, str, dict[str, str]]:
        response = self._request(path, params)
        return response.json(), response.url, dict(response.headers)

    def fetch_roles(self) -> tuple[dict, str]:
        """All role codes and descriptions (needed to identify the NHS Trust role)."""
        return self.get("roles")

    def fetch_organisation(self, ods_code: str) -> tuple[dict, str]:
        """Full record for one organisation, including dates, relationships, succession."""
        return self.get(f"organisations/{ods_code.upper()}")

    def search_organisations(
        self,
        *,
        primary_role_id: str | None = None,
        status: str | None = None,
        page_size: int = SEARCH_PAGE_SIZE,
        max_pages: int = 50,
    ) -> list[dict]:
        """Paginate the search endpoint, asserting completeness against X-Total-Count.

        The API's Offset is 1-based and rejects Offset=0 outright (HTTP 406), so the
        first page omits it. Rather than inferring the end from a short page, the total
        is read from the X-Total-Count header and the final count is asserted against it:
        silent under-fetching would corrupt the provider dimension without any error.
        """
        results: list[dict] = []
        expected_total: int | None = None
        offset: int | None = None

        for _ in range(max_pages):
            params: dict[str, Any] = {"Limit": page_size}
            if primary_role_id:
                params["PrimaryRoleId"] = primary_role_id
            if status:
                params["Status"] = status
            if offset is not None:
                params["Offset"] = offset

            payload, url, headers = self.get_with_headers("organisations", params=params)

            if expected_total is None:
                raw_total = headers.get("X-Total-Count")
                expected_total = int(raw_total) if raw_total and raw_total.isdigit() else None
                LOG.info("X-Total-Count=%s for %s", expected_total, url)

            batch = payload.get("Organisations", [])
            results.extend(batch)
            LOG.info("fetched %s (running total %s)", len(batch), len(results))

            if not batch:
                break
            if expected_total is not None and len(results) >= expected_total:
                break
            offset = len(results) + 1
        else:
            LOG.warning("hit max_pages=%s; results may be truncated", max_pages)

        if expected_total is not None and len(results) != expected_total:
            raise OdsFetchError(
                f"pagination incomplete: retrieved {len(results)}, "
                f"X-Total-Count reported {expected_total}"
            )
        return results


def _cmd_probe(args: argparse.Namespace) -> None:
    """Fetch the roles metadata and one known organisation, and land both in raw."""
    client = OdsClient()
    fetched_at = utc_now_iso()

    roles, roles_url = client.fetch_roles()
    artifact = write_raw_json("ods", "roles.json", roles, roles_url, fetched_at)
    print(f"roles      -> {artifact.path}  ({artifact.byte_size:,} bytes)")

    code = args.ods_code.upper()
    org, org_url = client.fetch_organisation(code)
    artifact = write_raw_json("ods", f"organisation_{code}.json", org, org_url, fetched_at)
    print(f"{code:<10} -> {artifact.path}  ({artifact.byte_size:,} bytes)")

    print("\n--- top-level keys ---")
    print(json.dumps({k: type(v).__name__ for k, v in org.items()}, indent=2))
    inner = org.get("Organisation", {})
    if isinstance(inner, dict):
        print("\n--- Organisation keys ---")
        print(json.dumps({k: type(v).__name__ for k, v in inner.items()}, indent=2))


def _cmd_search(args: argparse.Namespace) -> None:
    """Search organisations by primary role and status; land the summaries in raw."""
    client = OdsClient()
    fetched_at = utc_now_iso()

    orgs = client.search_organisations(
        primary_role_id=args.primary_role_id,
        status=args.status,
    )
    label = f"{args.primary_role_id or 'allroles'}_{args.status or 'anystatus'}".lower()
    artifact = write_raw_json(
        "ods",
        f"search_{label}.json",
        {"Organisations": orgs},
        f"{BASE_URL}/organisations",
        fetched_at,
    )
    print(f"{len(orgs)} organisations -> {artifact.path}")

    if orgs:
        print("\n--- summary record keys ---")
        print(json.dumps({k: type(v).__name__ for k, v in orgs[0].items()}, indent=2))
        print("\n--- first record ---")
        print(json.dumps(orgs[0], indent=2))


def _cmd_backfill(args: argparse.Namespace) -> None:
    """Search for a role, then fetch every full record, reporting section coverage."""
    client = OdsClient()
    fetched_at = utc_now_iso()

    summaries = client.search_organisations(
        primary_role_id=args.primary_role_id, status=args.status
    )
    codes = sorted({s["OrgId"] for s in summaries if "OrgId" in s})
    print(f"{len(summaries)} summaries -> {len(codes)} distinct ODS codes")

    coverage = {"Succs": 0, "Rels": 0, "refOnly": 0, "Date": 0}
    failures: list[str] = []

    for n, code in enumerate(codes, start=1):
        try:
            payload, url = client.fetch_organisation(code)
        except OdsFetchError as exc:
            failures.append(f"{code}: {exc}")
            continue

        write_raw_json("ods_organisations", f"{code}.json", payload, url, fetched_at)
        org = payload.get("Organisation", {})
        for key in ("Succs", "Rels", "Date"):
            if org.get(key):
                coverage[key] += 1
        if org.get("refOnly"):
            coverage["refOnly"] += 1

        if n % 25 == 0 or n == len(codes):
            print(f"  fetched {n}/{len(codes)}")

    print("\n--- section coverage ---")
    for key, count in coverage.items():
        pct = 100 * count / len(codes) if codes else 0
        print(f"  {key:<10} {count:>4}/{len(codes)}  ({pct:.1f}%)")
    if failures:
        print(f"\n{len(failures)} failures:")
        for line in failures[:10]:
            print(f"  {line}")


def _cmd_closure(args: argparse.Namespace) -> None:
    """Fetch succession targets missing from the corpus, until the graph is closed.

    Predecessors are not confined to the searched primary role -- a Care Trust (RO107)
    can be the predecessor of an NHS Trust (RO197) -- so an RO197-only corpus contains
    dangling lineage references. This walks outward until every referenced code is held.
    """
    corpus_dir: Path = args.corpus
    dataset = args.dataset
    fetched_at = utc_now_iso()
    client = OdsClient()

    def held_codes() -> set[str]:
        return {
            p.stem.upper()
            for p in corpus_dir.glob("*.json")
            if not p.name.endswith(".manifest.json")
        }

    def referenced_codes() -> set[str]:
        codes: set[str] = set()
        for path in corpus_dir.glob("*.json"):
            if path.name.endswith(".manifest.json"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            codes |= _succession_targets(payload.get("Organisation", {}))
        return codes

    if not corpus_dir.is_dir():
        raise SystemExit(f"corpus directory not found: {corpus_dir}")

    unresolved: list[str] = []
    for wave in range(1, args.max_waves + 1):
        before = len(held_codes())
        missing = sorted(referenced_codes() - held_codes() - set(unresolved))
        if not missing:
            print(f"closure reached after {wave - 1} wave(s)")
            break

        print(f"wave {wave}: fetching {len(missing)} missing organisations")
        for code in missing:
            try:
                payload, url = client.fetch_organisation(code)
            except OdsFetchError as exc:
                unresolved.append(code)
                print(f"  unresolved {code}: {exc}")
                continue
            write_raw_json(dataset, f"{code}.json", payload, url, fetched_at)

        after = len(held_codes())
        if after == before and len(unresolved) < len(missing):
            raise SystemExit(
                f"wave {wave} fetched {len(missing)} records but the corpus did not grow "
                f"({before} -> {after}). Writes are not landing in {corpus_dir}."
            )
        print(f"  corpus {before} -> {after}")
    else:
        print(f"stopped at max_waves={args.max_waves}; closure not proven")

    print(f"\ncorpus now holds {len(held_codes())} organisations")
    if unresolved:
        print(f"{len(unresolved)} unresolved codes: {', '.join(unresolved)}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="ODS ORD API ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="fetch roles metadata and one organisation")
    probe.add_argument("--ods-code", default="RJY", help="ODS code to fetch (default: RJY)")
    probe.set_defaults(func=_cmd_probe)

    search = sub.add_parser("search", help="search organisations by role and status")
    search.add_argument("--primary-role-id", default=None, help="e.g. the NHS Trust role id")
    search.add_argument("--status", default=None, choices=["Active", "Inactive"])
    search.set_defaults(func=_cmd_search)

    backfill = sub.add_parser("backfill", help="fetch full records for a role and report coverage")
    backfill.add_argument("--primary-role-id", default="RO197")
    backfill.add_argument("--status", default=None, choices=["Active", "Inactive"])
    backfill.set_defaults(func=_cmd_backfill)

    closure = sub.add_parser("closure", help="fetch succession targets missing from the corpus")
    closure.add_argument("--dataset", default="ods_organisations")
    closure.add_argument(
        "--corpus",
        type=Path,
        default=Path("data/raw/ods_organisations") / f"ingest_date={utc_now_iso()[:10]}",
    )
    closure.add_argument("--max-waves", type=int, default=5)
    closure.set_defaults(func=_cmd_closure)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
