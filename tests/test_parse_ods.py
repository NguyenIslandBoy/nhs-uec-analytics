"""Parser tests against real ODS fixtures.

Fixtures deliberately cover all three branches:
  RJY    inactive, refOnly, no Succs, closed 2001-03-31
  G6V2S  active, three Predecessor edges, one crossing to RO107, 5-char ANANA code
  R1G    inactive, single Successor edge (the opposite direction)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest.parse_ods import (
    OPEN_ENDED_DATE,
    OdsParseError,
    coerce_bool,
    deduplicate_edges,
    parse_organisation,
    parse_succession,
    unwrap,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ods"


def load(code: str) -> dict:
    return json.loads((FIXTURES / f"{code}.json").read_text(encoding="utf-8"))


# --- helpers ---------------------------------------------------------------


def test_coerce_bool_handles_string_false():
    """ODS-02: the string "false" is truthy in Python and must not be trusted."""
    assert coerce_bool("false") is False
    assert coerce_bool("true") is True
    assert coerce_bool(True) is True
    assert coerce_bool(None) is False


def test_unwrap_handles_both_container_shapes():
    """ODS-01: the same collection appears wrapped and bare across endpoints."""
    assert unwrap({"Role": [{"id": "A"}]}, "Role") == [{"id": "A"}]
    assert unwrap({"Role": {"id": "A"}}, "Role") == [{"id": "A"}]
    assert unwrap([{"id": "A"}], "Role") == [{"id": "A"}]
    assert unwrap(None, "Role") == []


# --- organisation parsing --------------------------------------------------


def test_parse_inactive_ref_only_record():
    row = parse_organisation(load("RJY"), "RJY.json")
    assert row["ods_code"] == "RJY"
    assert row["status"] == "Inactive"
    assert row["is_ref_only"] is True
    assert row["valid_from"] == "1993-04-01"
    assert row["valid_to"] == "2001-03-31"
    assert row["is_current"] is False
    assert row["is_nhs_trust"] is True
    assert row["primary_role_id"] == "RO197"


def test_parse_active_record_is_open_ended():
    row = parse_organisation(load("G6V2S"), "G6V2S.json")
    assert row["ods_code"] == "G6V2S"
    assert row["status"] == "Active"
    assert row["valid_to"] == OPEN_ENDED_DATE
    assert row["is_current"] is True
    assert row["is_ref_only"] is False


def test_parse_rejects_payload_without_org_id():
    with pytest.raises(OdsParseError, match="OrgId"):
        parse_organisation({"Organisation": {"Name": "X"}}, "bad.json")


def test_parse_rejects_empty_payload():
    with pytest.raises(OdsParseError, match="Organisation"):
        parse_organisation({}, "bad.json")


# --- succession normalisation ----------------------------------------------


def test_predecessor_edges_normalise_to_canonical_direction():
    """G6V2S lists predecessors, so it must appear as the successor on every edge."""
    edges = parse_succession(load("G6V2S"))
    assert len(edges) == 3
    assert {e.successor_code for e in edges} == {"G6V2S"}
    assert {e.predecessor_code for e in edges} == {"RRP", "TAF", "RNK"}
    assert {e.source_direction for e in edges} == {"Predecessor"}


def test_successor_edge_normalises_to_canonical_direction():
    """R1G lists a successor, so R1G is the predecessor on the canonical edge."""
    edges = parse_succession(load("R1G"))
    assert len(edges) == 1
    edge = edges[0]
    assert edge.predecessor_code == "R1G"
    assert edge.successor_code == "RA9"
    assert edge.legal_start_date == "2015-10-01"
    assert edge.source_direction == "Successor"


def test_record_without_succession_yields_no_edges():
    """ODS-03: an absent Succs key means none recorded, not a malformed payload."""
    assert parse_succession(load("RJY")) == []


def test_unknown_succession_type_is_skipped():
    payload = {
        "Organisation": {
            "OrgId": {"extension": "AAA"},
            "Succs": {"Succ": [{"Type": "Sideways", "Target": {"OrgId": {"extension": "BBB"}}}]},
        }
    }
    assert parse_succession(payload) == []


def test_deduplicate_collapses_both_directions_of_one_merger():
    """ODS-09: the same merger recorded from both ends must yield one edge.

    uniqueSuccId differs per direction, which is why it cannot be the dedup key.
    """
    from_successor = {
        "Organisation": {
            "OrgId": {"extension": "OLD"},
            "Succs": {
                "Succ": [
                    {
                        "uniqueSuccId": 111,
                        "Type": "Successor",
                        "Date": [{"Type": "Legal", "Start": "2020-04-01"}],
                        "Target": {"OrgId": {"extension": "NEW"}},
                    }
                ]
            },
        }
    }
    from_predecessor = {
        "Organisation": {
            "OrgId": {"extension": "NEW"},
            "Succs": {
                "Succ": [
                    {
                        "uniqueSuccId": 222,
                        "Type": "Predecessor",
                        "Date": [{"Type": "Legal", "Start": "2020-04-01"}],
                        "Target": {"OrgId": {"extension": "OLD"}},
                    }
                ]
            },
        }
    }

    edges = parse_succession(from_successor) + parse_succession(from_predecessor)
    assert len(edges) == 2, "both ends produce an edge before deduplication"

    unique = deduplicate_edges(edges)
    assert len(unique) == 1
    assert unique[0].predecessor_code == "OLD"
    assert unique[0].successor_code == "NEW"


def test_deduplicate_keeps_distinct_mergers_apart():
    edges = parse_succession(load("G6V2S"))
    assert len(deduplicate_edges(edges)) == 3
