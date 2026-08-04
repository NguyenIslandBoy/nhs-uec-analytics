"""Bisect which ORD search parameter combinations the API accepts.

The search endpoint returns HTTP 406 for some parameter combinations that the
documentation implies are valid. This walks a matrix of combinations and reports
status code, X-Total-Count and returned record count for each.
"""

from __future__ import annotations

import time

import requests

BASE = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations"
HEADERS = {
    "User-Agent": "nhs-uec-analytics/0.1 (portfolio project)",
    "Accept": "application/json",
}

CASES: list[tuple[str, dict[str, object]]] = [
    ("role only", {"PrimaryRoleId": "RO197"}),
    ("role + limit20", {"PrimaryRoleId": "RO197", "Limit": 20}),
    ("role + limit1000", {"PrimaryRoleId": "RO197", "Limit": 1000}),
    ("role + offset0", {"PrimaryRoleId": "RO197", "Offset": 0}),
    ("role + offset1", {"PrimaryRoleId": "RO197", "Offset": 1}),
    ("role + limit + offset0", {"PrimaryRoleId": "RO197", "Limit": 1000, "Offset": 0}),
    ("role + limit + offset1000", {"PrimaryRoleId": "RO197", "Limit": 1000, "Offset": 1000}),
    ("role + Status=Active", {"PrimaryRoleId": "RO197", "Status": "Active"}),
    ("role + Status=Inactive", {"PrimaryRoleId": "RO197", "Status": "Inactive"}),
    ("role + status=active lower", {"PrimaryRoleId": "RO197", "Status": "active"}),
    ("status only", {"Status": "Inactive"}),
    ("role + OrgRecordClass", {"PrimaryRoleId": "RO197", "OrgRecordClass": "RC1"}),
]


def main() -> None:
    print(f"{'case':<30} {'HTTP':<6} {'X-Total-Count':<14} {'returned':<9} note")
    print("-" * 90)

    for label, params in CASES:
        time.sleep(0.35)
        try:
            resp = requests.get(BASE, params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            print(f"{label:<30} {'ERR':<6} {'-':<14} {'-':<9} {exc}")
            continue

        total = resp.headers.get("X-Total-Count", "-")
        count: object = "-"
        note = ""
        if resp.status_code == 200:
            try:
                count = len(resp.json().get("Organisations", []))
            except ValueError:
                note = "non-JSON body"
        else:
            note = resp.text[:80].replace("\n", " ")

        print(f"{label:<30} {resp.status_code:<6} {str(total):<14} {str(count):<9} {note}")


if __name__ == "__main__":
    main()
