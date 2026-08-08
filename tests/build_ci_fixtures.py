"""Build Parquet extracts from the committed fixtures for CI to run dbt against.

CI must not depend on network access or on gitignored raw data. The three fixtures
cover every parser branch (refOnly / Predecessor / Successor), so a dbt build over
them exercises the real staging logic and contract tests on a hermetic input.
"""

from __future__ import annotations

from pathlib import Path

from ingest.parse_ods import build_tables

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "ods"
OUT = PROJECT_ROOT / "data" / "staging-ci"


def main() -> None:
    org_df, edge_df = build_tables(FIXTURES)
    OUT.mkdir(parents=True, exist_ok=True)
    org_df.to_parquet(OUT / "ods_organisation.parquet", index=False)
    edge_df.to_parquet(OUT / "ods_succession.parquet", index=False)
    print(f"{len(org_df)} organisations, {len(edge_df)} edges -> {OUT}")


if __name__ == "__main__":
    main()
