"""Discover UEC and Winter daily situation report timeseries workbooks.

Download URLs carry a random suffix (e.g. ...-UEC-Daily-SitRep-2526-ed389pw.xlsx) and
cannot be constructed from a pattern, so each season page is scraped. Each page also
publishes several unrelated timeseries workbooks (NHS111, ambulance, COVID, discharge)
alongside roughly 15 redundant weekly files, so link selection is deliberate rather
than "every spreadsheet on the page".

The series spans two separately published collections:

    Urgent and Emergency Care Daily Situation Reports   2020-21 -> present
    (Discontinued) Winter Daily Situation Reports       2012-13 -> 2019-20

Output is a manifest of (season, collection, url, link_text) written to the raw layer.
Fetching the workbooks themselves is a separate step, so discovery stays cheap and
re-runnable.
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from ingest._io import utc_now_iso, write_raw_json

LOG = logging.getLogger(__name__)

BASE = "https://www.england.nhs.uk/statistics/statistical-work-areas"

UEC_SEASON_URL = BASE + "/uec-sitrep/urgent-and-emergency-care-daily-situation-reports-{slug}/"
WINTER_SEASON_URL = BASE + "/winter-daily-sitreps/{slug}/"

# Season -> URL slug. UEC slugs are the season label; Winter slugs are irregular and
# were read from the collection index page, so they are listed explicitly.
UEC_SEASONS = {
    season: season for season in ["2025-26", "2024-25", "2023-24", "2022-23", "2021-22", "2020-21"]
}

WINTER_SEASONS = {
    "2019-20": "winter-daily-sitrep-2019-20-data",
    "2018-19": "winter-daily-sitrep-2018-19-data",
    "2017-18": "winter-daily-sitrep-2017-18-data",
    "2016-17": "winter-daily-sitrep-2016-17-data",
    "2015-16": "winter-daily-sitrep-2015-16-data",
    "2014-15": "winter-daily-sitrep-2014-15-data",
    "2013-14": "winter-daily-sitrep-2013-14-data-2",
    "2012-13": "winter-sitrep-data-2012-13",
}

COLLECTIONS = {
    "uec": (UEC_SEASONS, UEC_SEASON_URL),
    "winter": (WINTER_SEASONS, WINTER_SEASON_URL),
}

HEADERS = {
    "User-Agent": (
        "nhs-uec-analytics/0.1 (portfolio project; "
        "+https://github.com/NguyenIslandBoy/nhs-uec-analytics)"
    )
}

# Acute timeseries link naming varies across six vocabularies:
#   2023-24 onward   "Web File Timeseries - UEC Daily SitRep"
#   2021-22, 2022-23 "UEC Daily SitRep - Web File Timeseries"
#   2020-21          "UEC Daily SitRep - Acute Time Series"
#   2017-18..2019-20 "Winter SitRep - Acute Time series"
#   2013-14..2016-17 "Winter SitRep Part A: Acute Time Series"
#   2012-13          "Daily SR - Timeseries"
#
# Every season also publishes roughly 15 weekly files, redundant given the season
# timeseries. Those say "Web File" or carry a bare date range and never a timeseries
# marker, so requiring one excludes them. NHS111 and the other sibling collections do
# carry the marker and are excluded by name. "Part B" is the NHS111 half of the
# 2013-14..2016-17 naming.
COLLECTION_PATTERN = re.compile(
    r"uec[-\s]*daily[-\s]*sitrep|winter[-\s]*sitrep|daily[-\s]*sr",
    re.IGNORECASE,
)
TIMESERIES_PATTERN = re.compile(r"time[-\s]*series", re.IGNORECASE)
EXCLUDE_PATTERN = re.compile(
    r"111|ambulance|covid|discharge|part[-\s]*b",
    re.IGNORECASE,
)

SPREADSHEET_SUFFIXES = (".xlsx", ".xls", ".xlsm")
REQUEST_INTERVAL_S = 1.0


class SitrepDiscoveryError(RuntimeError):
    """Raised when a season page does not have the expected structure."""


@dataclass(frozen=True)
class SitrepFile:
    season: str
    collection: str
    url: str
    link_text: str
    file_suffix: str


def fetch_html(url: str, timeout_s: float = 30.0) -> str:
    time.sleep(REQUEST_INTERVAL_S)
    response = requests.get(url, headers=HEADERS, timeout=timeout_s)
    if response.status_code != 200:
        raise SitrepDiscoveryError(f"HTTP {response.status_code} for {url}")
    return response.text


def spreadsheet_links(html: str) -> list[tuple[str, str]]:
    """Return (href, link_text) for every spreadsheet link on the page."""
    soup = BeautifulSoup(html, "html.parser")
    return [
        (a["href"], " ".join(a.get_text().split()))
        for a in soup.find_all("a", href=True)
        if a["href"].lower().endswith(SPREADSHEET_SUFFIXES)
    ]


def select_timeseries_workbook(
    links: list[tuple[str, str]], season: str, collection: str
) -> SitrepFile:
    """Pick the acute timeseries workbook, failing loudly on zero or ambiguous matches.

    Failing loudly matters here: a "first match wins" selector would silently return a
    single week of data for seasons that publish weekly files alongside the timeseries.
    """
    matches = [
        (href, text)
        for href, text in links
        if COLLECTION_PATTERN.search(f"{text} {href}")
        and TIMESERIES_PATTERN.search(text)
        and not EXCLUDE_PATTERN.search(f"{text} {href}")
    ]
    if not matches:
        raise SitrepDiscoveryError(
            f"{collection} {season}: no acute timeseries workbook among "
            f"{len(links)} spreadsheet links. Link texts: {[t for _, t in links][:8]}"
        )
    if len(matches) > 1:
        raise SitrepDiscoveryError(
            f"{collection} {season}: {len(matches)} candidate workbooks, expected 1. "
            f"Candidates: {[t for _, t in matches]}"
        )

    href, text = matches[0]
    return SitrepFile(
        season=season,
        collection=collection,
        url=href,
        link_text=text,
        file_suffix=href[href.rfind(".") :].lower(),
    )


def discover_collection(
    collection: str, seasons: list[str] | None = None
) -> tuple[list[SitrepFile], list[str]]:
    """Discover the timeseries workbook for each season in one collection."""
    known, url_template = COLLECTIONS[collection]
    wanted = seasons or list(known)

    found: list[SitrepFile] = []
    failures: list[str] = []

    for season in wanted:
        if season not in known:
            failures.append(f"{collection}: unknown season {season}")
            continue

        url = url_template.format(slug=known[season])
        try:
            links = spreadsheet_links(fetch_html(url))
            workbook = select_timeseries_workbook(links, season, collection)
        except SitrepDiscoveryError as exc:
            failures.append(str(exc))
            LOG.warning("%s", exc)
            continue

        LOG.info("%s %s: %s", collection, season, workbook.url)
        found.append(workbook)

    return found, failures


def probe_season(collection: str, season: str) -> list[tuple[str, str]]:
    """List every spreadsheet link on one season page, without selecting."""
    known, url_template = COLLECTIONS[collection]
    return spreadsheet_links(fetch_html(url_template.format(slug=known[season])))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Discover sitrep timeseries workbooks")
    parser.add_argument(
        "--collection",
        choices=["uec", "winter", "all"],
        default="all",
        help="which collection to discover (default: all)",
    )
    parser.add_argument(
        "--seasons",
        nargs="+",
        default=None,
        help="restrict to specific seasons, e.g. 2025-26 2024-25",
    )
    parser.add_argument(
        "--probe",
        nargs=2,
        metavar=("COLLECTION", "SEASON"),
        default=None,
        help="list all spreadsheet links on one season page without selecting",
    )
    args = parser.parse_args()

    if args.probe:
        collection, season = args.probe
        links = probe_season(collection, season)
        print(f"--- {collection} {season}: {len(links)} spreadsheet links ---")
        for href, text in links:
            print(f"  {text}\n      {href}")
        return

    collections = ["uec", "winter"] if args.collection == "all" else [args.collection]
    found: list[SitrepFile] = []
    failures: list[str] = []
    for collection in collections:
        batch, errors = discover_collection(collection, args.seasons)
        found.extend(batch)
        failures.extend(errors)

    fetched_at = utc_now_iso()
    artifact = write_raw_json(
        "sitrep_manifest",
        "timeseries_workbooks.json",
        {"files": [asdict(f) for f in found], "failures": failures},
        BASE,
        fetched_at,
    )

    print(f"\n{len(found)} workbooks discovered -> {artifact.path}\n")
    print(f"  {'season':<9} {'collection':<11} {'fmt':<6} link text")
    print("  " + "-" * 86)
    for f in sorted(found, key=lambda x: x.season, reverse=True):
        print(f"  {f.season:<9} {f.collection:<11} {f.file_suffix:<6} {f.link_text[:60]}")

    suffixes: dict[str, int] = {}
    for f in found:
        suffixes[f.file_suffix] = suffixes.get(f.file_suffix, 0) + 1
    print(f"\n  formats: {suffixes}")

    if failures:
        print(f"\n{len(failures)} failures:")
        for line in failures:
            print(f"  {line}")


if __name__ == "__main__":
    main()
