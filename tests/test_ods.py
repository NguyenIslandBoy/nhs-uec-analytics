"""Unit tests for the ODS client. No network access."""

from __future__ import annotations

import json

import pytest
import requests

from ingest._io import sha256_bytes, write_raw_json
from ingest.ods import OdsClient, OdsFetchError


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, url: str = "http://x"):
        self.status_code = status_code
        self._payload = payload or {}
        self.url = url

    def json(self) -> dict:
        return self._payload


def make_client(monkeypatch, responses):
    """Build a client whose session returns the given responses in order."""
    client = OdsClient(min_interval_s=0.0, max_attempts=3)
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        idx = calls["n"]
        calls["n"] += 1
        item = responses[idx]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(client._session, "get", fake_get)
    monkeypatch.setattr("ingest.ods.time.sleep", lambda _: None)
    return client, calls


def test_get_returns_payload_on_success(monkeypatch):
    client, _ = make_client(monkeypatch, [FakeResponse(200, {"ok": True})])
    payload, url = client.get("roles")
    assert payload == {"ok": True}
    assert url == "http://x"


def test_get_retries_transient_status_then_succeeds(monkeypatch):
    client, calls = make_client(monkeypatch, [FakeResponse(503), FakeResponse(200, {"ok": True})])
    payload, _ = client.get("roles")
    assert payload == {"ok": True}
    assert calls["n"] == 2


def test_get_does_not_retry_client_error(monkeypatch):
    client, calls = make_client(monkeypatch, [FakeResponse(404)])
    with pytest.raises(OdsFetchError, match="404"):
        client.get("organisations/NOPE")
    assert calls["n"] == 1, "404 must not be retried"


def test_get_raises_after_exhausting_attempts(monkeypatch):
    client, calls = make_client(monkeypatch, [requests.ConnectionError("boom")] * 3)
    with pytest.raises(OdsFetchError, match="exhausted"):
        client.get("roles")
    assert calls["n"] == 3


def test_search_stops_when_page_is_short(monkeypatch):
    client, calls = make_client(
        monkeypatch,
        [FakeResponse(200, {"Organisations": [{"OrgId": "A"}, {"OrgId": "B"}]})],
    )
    results = client.search_organisations(page_size=10)
    assert len(results) == 2
    assert calls["n"] == 1, "a short page means no further requests"


def test_write_raw_json_records_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr("ingest._io.RAW_ROOT", tmp_path)
    monkeypatch.setattr("ingest._io.PROJECT_ROOT", tmp_path.parent)

    payload = {"Organisation": {"Name": "TEST TRUST"}}
    artifact = write_raw_json("ods", "t.json", payload, "http://src", "2026-08-04T10:00:00+00:00")

    written = tmp_path / "ods" / "ingest_date=2026-08-04" / "t.json"
    assert written.is_file()
    assert json.loads(written.read_text(encoding="utf-8")) == payload
    assert artifact.content_sha256 == sha256_bytes(written.read_bytes())

    manifest = json.loads(written.with_suffix(".json.manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_url"] == "http://src"
    assert manifest["byte_size"] == artifact.byte_size
