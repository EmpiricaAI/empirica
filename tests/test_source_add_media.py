"""`source-add --media` producer push — _upsert_media_to_cortex contract.

The producer half of media-bearing sources (SER ser_a92b3a05): POST the blob
to cortex's upsert-on-body endpoint (commit d76ae9c9) with the load-bearing
headers. These tests mock urllib so they run offline, and assert the exact
header contract cortex specified — especially X-Source-Visibility, which the
cross-tenant get_raw gate depends on.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

from empirica.cli.command_handlers.artifact_log_commands import _upsert_media_to_cortex


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, status=200, body=None):
    """Patch urlopen; return a dict that captures the Request that was sent."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["data"] = req.data
        return _FakeResp(status, body or {"ok": True, "uuid": "u1", "body_hash": "h", "size_bytes": 3})

    # The helper does `import urllib.request` internally, so patching the module
    # attribute reaches it.
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_upsert_sends_visibility_and_headers(monkeypatch):
    cap = _capture(monkeypatch)
    res = _upsert_media_to_cortex(
        "https://cortex.example", "ctx_key", "src-1", b"png", "image/png", "Logo", "proj-9", "shared"
    )
    assert res["pushed"] is True
    assert cap["url"].endswith("/v1/sources/src-1/body")
    assert cap["method"] == "POST"
    h = cap["headers"]
    assert h["x-source-visibility"] == "shared"  # load-bearing for cross-tenant
    assert h["content-type"] == "image/png"
    assert h["x-source-title"] == "Logo"
    assert h["x-project-id"] == "proj-9"
    assert h["authorization"] == "Bearer ctx_key"
    assert cap["data"] == b"png"


def test_non_ascii_title_dropped(monkeypatch):
    # A unicode title would raise at HTTP-send (headers are latin-1) — the
    # helper must omit it and let cortex default to media-<hash>.
    cap = _capture(monkeypatch)
    _upsert_media_to_cortex(
        "https://cortex.example", "ctx_key", "src-2", b"x", "image/png", "logø-café", None, "public"
    )
    assert "x-source-title" not in cap["headers"]
    assert "x-project-id" not in cap["headers"]  # None project omitted
    assert cap["headers"]["x-source-visibility"] == "public"


def test_http_error_surfaces(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(b'{"error":"hash_mismatch"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    res = _upsert_media_to_cortex(
        "https://cortex.example", "ctx_key", "src-3", b"x", "image/png", "t", "p", "shared"
    )
    assert res["pushed"] is False
    assert res["error"] == "hash_mismatch"
    assert res["status"] == 409


def test_network_error_surfaces(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    res = _upsert_media_to_cortex(
        "https://cortex.example", "ctx_key", "src-4", b"x", "image/png", "t", "p", "shared"
    )
    assert res["pushed"] is False
    assert "URLError" in res["error"]


def test_default_mime_when_none(monkeypatch):
    cap = _capture(monkeypatch)
    _upsert_media_to_cortex("https://cortex.example", "ctx_key", "src-5", b"x", None, "t", "p", "shared")
    assert cap["headers"]["content-type"] == "application/octet-stream"
