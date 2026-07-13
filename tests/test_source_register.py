"""Shared-source → cortex catalogue registration (Option B convergence).

`source-add --visibility=shared` for a non-media source now POSTs to
`/v1/sources/register` so it reaches the one cortex catalogue the extension +
sources-map read — instead of being stranded in local epistemic_sources. These
tests mock urllib/config so they run offline and assert the register contract +
the applicability gate.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from empirica.cli.command_handlers.artifact_log_commands import (
    _register_shared_source_if_applicable,
    _register_source_in_cortex,
)

IDENTITY = {"content_hash": "sha256:abc", "size_bytes": 512, "canonical_path": None, "mime_type": "text/plain"}


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, status=200, body=None):
    cap = {}

    def fake(req, timeout=None):
        cap["url"] = req.full_url
        cap["method"] = req.get_method()
        cap["payload"] = json.loads(req.data.decode())
        cap["ctype"] = {k.lower(): v for k, v in req.header_items()}.get("content-type")
        return _Resp(status, body or {"source": {"id": "src-1"}, "adopted_status": "minted"})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return cap


def test_register_posts_expected_body(monkeypatch):
    cap = _capture(monkeypatch)
    res = _register_source_in_cortex(
        "https://cortex.example", "k", "src-1", "proj-9", "RFC 7519", "document", "shared", IDENTITY
    )
    assert res["registered"] is True
    assert res["adopted_status"] == "minted"
    assert cap["url"].endswith("/v1/sources/register")
    assert cap["method"] == "POST"
    assert cap["ctype"] == "application/json"
    p = cap["payload"]
    assert p["posted_uuid"] == "src-1"
    assert p["project_id"] == "proj-9"
    assert p["title"] == "RFC 7519"
    assert p["source_type"] == "document"
    assert p["visibility"] == "shared"
    assert p["content_hash"] == "sha256:abc"
    assert p["size_bytes"] == 512
    assert "canonical_path" not in p  # None identity fields omitted


def test_register_http_error_surfaces(monkeypatch):
    import io

    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(b'{"error":"bad_visibility"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    res = _register_source_in_cortex("https://c", "k", "s", "p", "t", "document", "shared", IDENTITY)
    assert res["registered"] is False
    assert res["error"] == "bad_visibility"


def _args():
    import types

    return types.SimpleNamespace(cortex_url=None, api_key=None)


def test_applicability_skips_local(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        "empirica.cli.command_handlers.artifact_log_commands._register_source_in_cortex",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"registered": True},
    )
    # local visibility → never registers
    assert _register_shared_source_if_applicable(_args(), "s", "p", "t", "document", "local", IDENTITY, None) is None
    # --media source → skip (the /body upsert already registered it)
    assert (
        _register_shared_source_if_applicable(_args(), "s", "p", "t", "document", "shared", IDENTITY, "/img.png")
        is None
    )
    assert called["n"] == 0


def test_applicability_skips_when_cortex_unconfigured(monkeypatch):
    import empirica.cli.command_handlers.projects_commands as pc

    monkeypatch.setattr(pc, "_resolve_cortex_config", lambda args: (None, None))
    assert _register_shared_source_if_applicable(_args(), "s", "p", "t", "document", "shared", IDENTITY, None) is None
