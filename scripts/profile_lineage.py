"""Profile the succession graph: chain depth, fan-out, and non-trust participants."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", type=Path, default=Path("data/staging"))
    args = parser.parse_args()

    orgs = pd.read_parquet(args.staging / "stg_ods_organisation.parquet")
    edges = pd.read_parquet(args.staging / "stg_ods_succession.parquet")

    successors: dict[str, list[str]] = defaultdict(list)
    for row in edges.itertuples():
        successors[row.predecessor_code].append(row.successor_code)

    # Fan-out: a predecessor with several successors is a demerger, not a merger
    fan_out = {k: v for k, v in successors.items() if len(v) > 1}
    print(f"predecessors with >1 successor (demergers): {len(fan_out)}")
    for code, targets in sorted(fan_out.items())[:10]:
        name = orgs.loc[orgs.ods_code == code, "org_name"]
        label = name.iloc[0] if len(name) else "?"
        print(f"  {code:<8} -> {', '.join(sorted(targets))}   ({label})")

    # Chain depth: resolve each predecessor to its terminal successor
    def terminal(code: str, seen: set[str] | None = None) -> tuple[str, int]:
        seen = seen or set()
        if code in seen:
            return code, -1  # cycle
        nxt = successors.get(code)
        if not nxt:
            return code, 0
        end, depth = terminal(sorted(nxt)[0], seen | {code})
        return end, (depth + 1 if depth >= 0 else -1)

    depths = {code: terminal(code)[1] for code in successors}
    cycles = [c for c, d in depths.items() if d < 0]
    print("\nchain depth distribution (hops to terminal successor)")
    print(pd.Series([d for d in depths.values() if d >= 0]).value_counts().sort_index().to_string())
    if cycles:
        print(f"\nCYCLES DETECTED ({len(cycles)}): {sorted(cycles)[:10]}")

    # Which primary roles participate in trust lineage
    participants = set(edges.predecessor_code) | set(edges.successor_code)
    subset = orgs[orgs.ods_code.isin(participants)]
    print("\nprimary roles participating in succession")
    print(subset.primary_role_id.value_counts().to_string())

    # Active organisations that are not NHS trusts
    odd = orgs[(orgs.status == "Active") & (~orgs.is_nhs_trust)]
    print(f"\nactive non-NHS-trust organisations: {len(odd)}")
    print(odd[["ods_code", "org_name", "primary_role_id"]].to_string(index=False))


if __name__ == "__main__":
    main()
