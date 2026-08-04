"""Profile Succs/Rels structure across the downloaded ODS organisation corpus.

Answers: which direction is succession recorded in, what Types occur, and does
refOnly fully explain missing sections.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load_records(root: Path) -> list[tuple[str, dict]]:
    records = []
    for path in sorted(root.glob("*.json")):
        if path.name.endswith(".manifest.json"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.append((path.stem, payload.get("Organisation", {})))
    return records


def as_list(node: object) -> list:
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()

    records = load_records(args.root)
    print(f"{len(records)} organisation records\n")

    # refOnly x has-Succs cross-tab
    crosstab = Counter()
    for _, org in records:
        crosstab[(bool(org.get("refOnly")), bool(org.get("Succs")))] += 1
    print("refOnly x has_Succs")
    for (ref_only, has_succs), n in sorted(crosstab.items()):
        print(f"  refOnly={str(ref_only):<5} has_Succs={str(has_succs):<5}  {n:>4}")

    # Succ types, split by the status of the record carrying them
    succ_types = Counter()
    for _, org in records:
        succs = org.get("Succs", {})
        for succ in as_list(succs.get("Succ") if isinstance(succs, dict) else succs):
            succ_types[(org.get("Status"), succ.get("Type"))] += 1
    print("\n(record Status, Succ Type) counts")
    for key, n in sorted(succ_types.items(), key=lambda kv: -kv[1]):
        print(f"  {str(key):<40} {n:>4}")

    # Rel types
    rel_types = Counter()
    for _, org in records:
        rels = org.get("Rels", {})
        for rel in as_list(rels.get("Rel") if isinstance(rels, dict) else rels):
            rel_types[rel.get("id")] += 1
    print("\nRel id counts")
    for key, n in sorted(rel_types.items(), key=lambda kv: -kv[1]):
        print(f"  {str(key):<10} {n:>4}")

    # Full example of an active record carrying succession
    for code, org in records:
        succs = org.get("Succs")
        if succs and org.get("Status") == "Active":
            print(f"\n--- example: ACTIVE record with Succs ({code}) ---")
            print(json.dumps({"Name": org.get("Name"), "Succs": succs}, indent=2)[:2500])
            break

    for code, org in records:
        succs = org.get("Succs")
        if succs and org.get("Status") == "Inactive":
            print(f"\n--- example: INACTIVE record with Succs ({code}) ---")
            print(json.dumps({"Name": org.get("Name"), "Succs": succs}, indent=2)[:2500])
            break

    # Date types present
    date_types = Counter()
    for _, org in records:
        for d in as_list(org.get("Date")):
            date_types[(d.get("Type"), "End" in d)] += 1
    print("\n(Date Type, has_End) counts")
    for key, n in sorted(date_types.items(), key=lambda kv: -kv[1]):
        print(f"  {str(key):<30} {n:>4}")


if __name__ == "__main__":
    main()
