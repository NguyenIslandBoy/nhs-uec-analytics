"""Shared IO helpers for raw-layer persistence.

Every raw artefact is written alongside a sidecar manifest recording where it came
from, when, and its content hash. Bronze is append-only and immutable: nothing in
data/raw is ever edited in place.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data" / "raw"


@dataclass(frozen=True)
class RawArtifact:
    """Provenance record for a single file landed in the raw layer."""

    path: str
    source_url: str
    fetched_at: str
    content_sha256: str
    byte_size: int


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def raw_partition(dataset: str, fetched_at: str | None = None) -> Path:
    """Return data/raw/<dataset>/ingest_date=YYYY-MM-DD/, creating it if needed."""
    stamp = (fetched_at or utc_now_iso())[:10]
    partition = RAW_ROOT / dataset / f"ingest_date={stamp}"
    partition.mkdir(parents=True, exist_ok=True)
    return partition


def write_raw_json(
    dataset: str,
    filename: str,
    payload: Any,
    source_url: str,
    fetched_at: str | None = None,
) -> RawArtifact:
    """Write a JSON payload to the raw layer with a provenance sidecar."""
    fetched_at = fetched_at or utc_now_iso()
    partition = raw_partition(dataset, fetched_at)
    target = partition / filename

    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    target.write_bytes(encoded)

    artifact = RawArtifact(
        path=str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        source_url=source_url,
        fetched_at=fetched_at,
        content_sha256=sha256_bytes(encoded),
        byte_size=len(encoded),
    )
    manifest = target.with_suffix(target.suffix + ".manifest.json")
    manifest.write_text(json.dumps(asdict(artifact), indent=2), encoding="utf-8")
    return artifact
