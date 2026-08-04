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
from typing import Any

import requests

from ingest._io import utc_now_iso, write_raw_json

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

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict, str]:
        """GET a JSON endpoint. Returns (payload, resolved_url)."""
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
                    return response.json(), response.url
                if response.status_code not in RETRY_STATUSES:
                    raise OdsFetchError(f"HTTP {response.status_code} for {response.url}")
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
        max_pages: int = 20,
    ) -> list[dict]:
        """Paginate the search endpoint, returning organisation summaries.

        status: "Active" or "Inactive". Inactive organisations matter here -- a merged
        trust is inactive but still owns years of historic attendance data.
        """
        results: list[dict] = []
        for page in range(max_pages):
            params: dict[str, Any] = {"Limit": page_size, "Offset": page * page_size}
            if primary_role_id:
                params["PrimaryRoleId"] = primary_role_id
            if status:
                params["Status"] = status

            payload, url = self.get("organisations", params=params)
            batch = payload.get("Organisations", [])
            LOG.info("search page %s returned %s organisations (%s)", page, len(batch), url)
            results.extend(batch)
            if len(batch) < page_size:
                break
        else:
            LOG.warning("hit max_pages=%s; results may be truncated", max_pages)
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="ODS ORD API ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="fetch roles metadata and one organisation")
    probe.add_argument("--ods-code", default="RJY", help="ODS code to fetch (default: RJY)")
    probe.set_defaults(func=_cmd_probe)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
