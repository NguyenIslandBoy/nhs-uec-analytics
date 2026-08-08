"""Sync the ODS test fixtures to a closed subgraph.

Fixtures must form a closed succession graph or `build_tables` rejects them for dangling
references -- the same guard that protects the real corpus. Hand-picking is not viable:
each record added pulls in its own predecessors. This walks the closure over the local
corpus and copies exactly the records needed, no more.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ingest.parse_ods import _succession_targets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "ods"

# Seeds chosen to cover every parser branch:
#   RJY    inactive, refOnly, no succession
#   G6V2S  active, multiple Predecessor edges, one crossing to RO107, 5-char code
#   R1G    inactive, single Successor edge (opposite direction)
DEFAULT_SEEDS = ["RJY", "G6V2S", "R1G"]


def targets_of(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _succession_targets(payload.get("Organisation", {}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--seeds", nargs="*", default=DEFAULT_SEEDS)
    parser.add_argument("--max-records", type=int, default=100)
    args = parser.parse_args()

    needed: set[str] = {s.upper() for s in args.seeds}
    frontier = set(needed)

    while frontier:
        discovered: set[str] = set()
        for code in sorted(frontier):
            source = args.corpus / f"{code}.json"
            if not source.is_file():
                raise SystemExit(f"{code} not found in corpus at {args.corpus}")
            discovered |= targets_of(source)
        frontier = discovered - needed
        needed |= discovered
        if len(needed) > args.max_records:
            raise SystemExit(
                f"closure exceeded --max-records={args.max_records} ({len(needed)} records). "
                "Choose seeds with a smaller lineage footprint."
            )

    FIXTURES.mkdir(parents=True, exist_ok=True)
    for stale in FIXTURES.glob("*.json"):
        stale.unlink()
    for code in sorted(needed):
        shutil.copy(args.corpus / f"{code}.json", FIXTURES / f"{code}.json")

    print(f"fixture closure: {len(needed)} records from seeds {args.seeds}")
    print("  " + ", ".join(sorted(needed)))


if __name__ == "__main__":
    main()
