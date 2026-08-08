"""Parse raw ODS organisation records into flat staging tables.

Produces two Parquet outputs for dbt to read:

  stg_ods_organisation  one row per ODS code, with SCD2 validity derived from the
                        Operational date entry (see ADR-0002)
  stg_ods_succession    one row per canonical merger edge, deduplicated across the
                        bidirectional records ODS publishes

Design decisions and the evidence behind them are in docs/adr/0002 and
docs/known-issues.md (ODS-01 through ODS-11). The two that most affect this module:

  ODS-09  succession is recorded from both ends, so edges must be normalised to a
          canonical (predecessor, successor) direction and deduplicated on the code
          pair plus legal date -- never on uniqueSuccId, which differs per direction
  ODS-03  absent sections are omitted rather than returned empty, so every accessor
          defaults and a missing key means "none recorded"
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)

OPEN_ENDED_DATE = "9999-12-31"
FOUNDATION_TRUST_ROLE_ID = "RO57"
NHS_TRUST_ROLE_ID = "RO197"


class OdsParseError(ValueError):
    """Raised when a record violates an invariant established during profiling."""


@dataclass(frozen=True)
class SuccessionEdge:
    """A canonical merger edge: predecessor_code was succeeded by successor_code."""

    predecessor_code: str
    successor_code: str
    legal_start_date: str | None
    source_code: str
    source_direction: str

    @property
    def dedup_key(self) -> tuple[str, str, str | None]:
        return (self.predecessor_code, self.successor_code, self.legal_start_date)


def as_list(node: Any) -> list:
    """ODS returns a bare object where a list has one element. Normalise to a list."""
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def unwrap(node: Any, key: str) -> list:
    """Unwrap the {"Succs": {"Succ": [...]}} / {"Roles": {"Role": [...]}} idiom.

    Handles the container inconsistency in ODS-01: the same logical collection appears
    as a bare list in some payloads and a single-key dict wrapper in others.
    """
    if node is None:
        return []
    if isinstance(node, dict):
        return as_list(node.get(key))
    return as_list(node)


def _succession_targets(org: dict) -> set[str]:
    """Every ODS code referenced by an organisation's succession records."""
    codes = set()
    for entry in unwrap(org.get("Succs"), "Succ"):
        extension = ((entry.get("Target") or {}).get("OrgId") or {}).get("extension")
        if extension:
            codes.add(extension.upper())
    return codes


def coerce_bool(value: Any) -> bool:
    """Coerce ODS booleans, which are sometimes the strings "true"/"false" (ODS-02)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def operational_validity(org: dict) -> tuple[str | None, str]:
    """Return (valid_from, valid_to) from the Operational date entry.

    Profiling established that every record carries an Operational date, and that the
    presence of an End matches Status='Inactive' exactly (ADR-0002). Legal dates are a
    separate concept and are not used for validity.
    """
    for entry in as_list(org.get("Date")):
        if entry.get("Type") == "Operational":
            return entry.get("Start"), entry.get("End") or OPEN_ENDED_DATE
    return None, OPEN_ENDED_DATE


def legal_validity(org: dict) -> tuple[str | None, str | None]:
    for entry in as_list(org.get("Date")):
        if entry.get("Type") == "Legal":
            return entry.get("Start"), entry.get("End")
    return None, None


def role_ids(org: dict) -> list[str]:
    return [r.get("id") for r in unwrap(org.get("Roles"), "Role") if r.get("id")]


def primary_role_id(org: dict) -> str | None:
    for role in unwrap(org.get("Roles"), "Role"):
        if coerce_bool(role.get("primaryRole")):
            return role.get("id")
    return None


def parse_organisation(payload: dict, source_path: str | None = None) -> dict:
    """Flatten one organisation record into a staging row."""
    org = payload.get("Organisation")
    if not org:
        raise OdsParseError(f"no Organisation key in payload from {source_path}")

    ods_code = (org.get("OrgId") or {}).get("extension")
    if not ods_code:
        raise OdsParseError(f"no OrgId.extension in payload from {source_path}")

    valid_from, valid_to = operational_validity(org)
    legal_from, legal_to = legal_validity(org)
    roles = role_ids(org)
    location = (org.get("GeoLoc") or {}).get("Location") or {}
    status = org.get("Status")

    return {
        "ods_code": ods_code.upper(),
        "org_name": org.get("Name"),
        "status": status,
        "org_record_class": org.get("orgRecordClass"),
        "is_ref_only": coerce_bool(org.get("refOnly")),
        "primary_role_id": primary_role_id(org),
        "role_ids": ",".join(sorted(roles)),
        "is_nhs_trust": NHS_TRUST_ROLE_ID in roles,
        "is_foundation_trust": FOUNDATION_TRUST_ROLE_ID in roles,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "is_current": valid_to == OPEN_ENDED_DATE,
        "legal_start_date": legal_from,
        "legal_end_date": legal_to,
        "post_code": location.get("PostCode"),
        "town": location.get("Town"),
        "county": location.get("County"),
        "country": location.get("Country"),
        "last_change_date": org.get("LastChangeDate"),
        "source_path": source_path,
    }


def parse_succession(payload: dict) -> list[SuccessionEdge]:
    """Extract succession edges, normalised to a canonical direction (ODS-09).

    On a record for X:
      Type='Predecessor' targeting Y  ->  (predecessor=Y, successor=X)
      Type='Successor'   targeting Y  ->  (predecessor=X, successor=Y)
    """
    org = payload.get("Organisation") or {}
    source_code = (org.get("OrgId") or {}).get("extension")
    if not source_code:
        return []
    source_code = source_code.upper()

    edges: list[SuccessionEdge] = []
    for succ in unwrap(org.get("Succs"), "Succ"):
        target_code = ((succ.get("Target") or {}).get("OrgId") or {}).get("extension")
        if not target_code:
            continue
        target_code = target_code.upper()

        legal_start = None
        for entry in as_list(succ.get("Date")):
            if entry.get("Type") == "Legal":
                legal_start = entry.get("Start")
                break

        direction = succ.get("Type")
        if direction == "Predecessor":
            predecessor, successor = target_code, source_code
        elif direction == "Successor":
            predecessor, successor = source_code, target_code
        else:
            LOG.warning("unknown Succ Type %r on %s; skipping", direction, source_code)
            continue

        edges.append(
            SuccessionEdge(
                predecessor_code=predecessor,
                successor_code=successor,
                legal_start_date=legal_start,
                source_code=source_code,
                source_direction=direction,
            )
        )
    return edges


def deduplicate_edges(edges: list[SuccessionEdge]) -> list[SuccessionEdge]:
    """Collapse the two directions ODS records for a single merger.

    Keeps the first occurrence per (predecessor, successor, legal_start_date). Sorting
    by source_code first makes the retained row deterministic across runs.
    """
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[SuccessionEdge] = []
    for edge in sorted(edges, key=lambda e: (e.predecessor_code, e.successor_code, e.source_code)):
        if edge.dedup_key in seen:
            continue
        seen.add(edge.dedup_key)
        unique.append(edge)
    return unique


def collapse_conflicting_dates(edges: list[SuccessionEdge]) -> tuple[list[SuccessionEdge], int]:
    """Collapse edges differing only in legal_start_date, keeping the later (ODS-12).

    ODS holds two succession records for some events, dated one day apart across a
    boundary (e.g. 2018-05-31 / 2018-06-01): the predecessor's final operational day and
    the successor's first. These are one transaction recorded from both ends, and they
    survive the primary deduplication because legal_start_date is part of its key.

    The later date is retained, because it is the successor's operational start and
    therefore the correct attribution boundary for daily activity data. Retaining the
    earlier date would attribute the predecessor's final day to the successor.

    Returns the collapsed edges and the number of conflicts, so the count is reported
    rather than silently absorbed.
    """
    by_pair: dict[tuple[str, str], list[SuccessionEdge]] = {}
    for edge in edges:
        by_pair.setdefault((edge.predecessor_code, edge.successor_code), []).append(edge)

    collapsed: list[SuccessionEdge] = []
    conflicts = 0
    for group in by_pair.values():
        if len(group) > 1:
            conflicts += 1
            LOG.warning(
                "conflicting legal dates for %s -> %s: %s",
                group[0].predecessor_code,
                group[0].successor_code,
                sorted(e.legal_start_date or "" for e in group),
            )
        collapsed.append(max(group, key=lambda e: e.legal_start_date or ""))
    return collapsed, conflicts


def load_corpus(corpus_dir: Path) -> list[tuple[Path, dict]]:
    records = []
    for path in sorted(corpus_dir.glob("*.json")):
        if path.name.endswith(".manifest.json"):
            continue
        records.append((path, json.loads(path.read_text(encoding="utf-8"))))
    if not records:
        raise OdsParseError(f"no organisation records found in {corpus_dir}")
    return records


def build_tables(corpus_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    records = load_corpus(corpus_dir)

    organisations = [parse_organisation(payload, path.name) for path, payload in records]
    raw_edges = [edge for _, payload in records for edge in parse_succession(payload)]
    edges = deduplicate_edges(raw_edges)
    edges, date_conflicts = collapse_conflicting_dates(edges)
    if date_conflicts:
        LOG.warning(
            "%s predecessor-successor pairs had conflicting legal dates (ODS-12)", date_conflicts
        )

    LOG.info(
        "parsed %s organisations, %s raw edges -> %s canonical edges",
        len(organisations),
        len(raw_edges),
        len(edges),
    )

    org_df = pd.DataFrame(organisations)
    edge_df = pd.DataFrame([vars(e) for e in edges])

    _assert_invariants(org_df, edge_df)
    return org_df, edge_df


def _assert_invariants(org_df: pd.DataFrame, edge_df: pd.DataFrame) -> None:
    """Fail loudly on violations of invariants established during profiling."""
    duplicated = org_df["ods_code"].duplicated()
    if duplicated.any():
        raise OdsParseError(f"duplicate ods_code: {sorted(org_df.loc[duplicated, 'ods_code'])}")

    missing_from = org_df["valid_from"].isna()
    if missing_from.any():
        raise OdsParseError(
            f"{missing_from.sum()} records lack an Operational start date, "
            f"e.g. {sorted(org_df.loc[missing_from, 'ods_code'])[:5]}"
        )

    mismatched = org_df[org_df["is_current"] != (org_df["status"] == "Active")]
    if not mismatched.empty:
        raise OdsParseError(
            "open-ended Operational date must match Status='Active' (ADR-0002); "
            f"{len(mismatched)} exceptions, e.g. {sorted(mismatched['ods_code'])[:5]}"
        )

    ref_only_with_edges = set(org_df.loc[org_df["is_ref_only"], "ods_code"])
    if not edge_df.empty:
        offenders = ref_only_with_edges & set(edge_df["source_code"])
        if offenders:
            raise OdsParseError(
                f"refOnly records carrying succession (ODS-06): {sorted(offenders)}"
            )

        dangling = (set(edge_df["predecessor_code"]) | set(edge_df["successor_code"])) - set(
            org_df["ods_code"]
        )
        if dangling:
            raise OdsParseError(
                f"succession references {len(dangling)} organisations absent from the corpus; "
                f"run `ingest.ods closure` first. e.g. {sorted(dangling)[:5]}"
            )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Parse the raw ODS corpus into staging tables")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/staging"))
    args = parser.parse_args()

    org_df, edge_df = build_tables(args.corpus)
    args.out.mkdir(parents=True, exist_ok=True)

    org_path = args.out / "ods_organisation.parquet"
    edge_path = args.out / "ods_succession.parquet"
    org_df.to_parquet(org_path, index=False)
    edge_df.to_parquet(edge_path, index=False)

    print(f"{len(org_df):>5} organisations -> {org_path}")
    print(f"{len(edge_df):>5} canonical edges -> {edge_path}")
    print("\n--- organisation summary ---")
    print(f"  active            {int((org_df['status'] == 'Active').sum())}")
    print(f"  ref-only          {int(org_df['is_ref_only'].sum())}")
    print(f"  NHS trusts        {int(org_df['is_nhs_trust'].sum())}")
    print(f"  foundation trusts {int(org_df['is_foundation_trust'].sum())}")
    print("\n--- succession summary ---")
    if not edge_df.empty:
        print(f"  distinct predecessors {edge_df['predecessor_code'].nunique()}")
        print(f"  distinct successors   {edge_df['successor_code'].nunique()}")
        print(edge_df["source_direction"].value_counts().to_string())


if __name__ == "__main__":
    main()
