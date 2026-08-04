"""Ad-hoc raw-payload inspector. Development aid, not part of the pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def shape(node: Any, depth: int = 0, max_depth: int = 3) -> Any:
    """Return a type-only skeleton of a JSON structure."""
    if depth >= max_depth:
        return type(node).__name__
    if isinstance(node, dict):
        return {k: shape(v, depth + 1, max_depth) for k, v in node.items()}
    if isinstance(node, list):
        if not node:
            return "list[empty]"
        return [shape(node[0], depth + 1, max_depth), f"...({len(node)} items)"]
    return type(node).__name__


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--shape", action="store_true", help="print type skeleton only")
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--grep", default=None, help="print list items matching this text")
    parser.add_argument(
        "--at", default=None, help="dotted path into the payload, e.g. Organisation.Date"
    )
    args = parser.parse_args()

    data = json.loads(args.path.read_text(encoding="utf-8"))

    if args.at:
        for key in args.at.split("."):
            data = data[int(key)] if key.isdigit() else data[key]

    if args.grep:
        rows = data if isinstance(data, list) else [data]
        needle = args.grep.upper()
        hits = [r for r in rows if needle in json.dumps(r).upper()]
        print(f"{len(hits)} of {len(rows)} matched\n")
        for row in hits:
            print(json.dumps(row, indent=2))
        return

    print(json.dumps(shape(data, max_depth=args.depth) if args.shape else data, indent=2))


if __name__ == "__main__":
    main()
