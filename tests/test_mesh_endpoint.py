"""Where the mesh paths send their requests (1.15.1, goal b4cdb5db; David's ruling 2026-11-10).

A configured Cortex always wins. With none, the mesh paths (mailbox, registration, listener catch-up, heartbeats) use the
local server when `empirica serve` answers on loopback as the local edition. Everything else keeps asking
`cortex_bearer()` / `get_cortex_config()`, which still mean "a real Cortex seat": the reader audit found 11 readers that
break if "no Cortex" starts returning a local URL.
"""

from __future__ import annotations

import pytest

from empirica.core import mesh_endpoint as me


def _cortex(url=None, bearer=None, reason=None):
    def fake(loader=None):
        out = {"url": url, "bearer": bearer, "source": "api_key" if bearer else "none"}
        if reason:
            out["reason"] = reason
        return out

    return fake


def test_a_configured_cortex_wins_and_the_local_server_is_not_probed(monkeypatch):
    probed = []
    monkeypatch.setattr(me, "_cortex_bearer", _cortex("https://cortex.example", "ctx_k"))
    out = me.mesh_bearer(probe=lambda url: probed.append(url) or {"edition": "local"})
    assert (out["url"], out["bearer"], out["edition"]) == ("https://cortex.example", "ctx_k", "cortex")
    assert probed == []


def test_with_no_cortex_a_local_edition_on_loopback_is_used(monkeypatch):
    monkeypatch.setattr(me, "_cortex_bearer", _cortex())
    monkeypatch.setenv("EMPIRICA_SERVE_PORT", "8123")
    monkeypatch.delenv("EMPIRICA_LOCAL_TOKEN", raising=False)
    seen = []
    out = me.mesh_bearer(probe=lambda url: seen.append(url) or {"status": "healthy", "edition": "local"})
    assert seen == ["http://127.0.0.1:8123/v1/health"]
    assert out == {"url": "http://127.0.0.1:8123", "bearer": "local", "edition": "local", "source": "local"}


def test_a_configured_local_token_is_the_bearer(monkeypatch):
    monkeypatch.setattr(me, "_cortex_bearer", _cortex())
    monkeypatch.setenv("EMPIRICA_LOCAL_TOKEN", "sekrit")
    assert me.mesh_bearer(probe=lambda url: {"edition": "local"})["bearer"] == "sekrit"


@pytest.mark.parametrize("doc", [None, {"edition": "cortex"}, {"status": "healthy"}, {"edition": "LOCAL "}])
def test_anything_but_a_local_edition_is_not_used(monkeypatch, doc):
    """A development Cortex on the same port, an older serve without the local routes, or nothing listening."""
    monkeypatch.setattr(me, "_cortex_bearer", _cortex(reason="no cortex credential"))
    out = me.mesh_bearer(probe=lambda url: doc)
    assert out["url"] is None and out["edition"] is None and out["reason"] == "no cortex credential"


def test_a_cortex_with_a_url_but_no_usable_bearer_is_not_replaced_by_the_local_server(monkeypatch):
    """A revoked key on a real Cortex seat must surface as that, not silently switch the practice to local."""
    monkeypatch.setattr(me, "_cortex_bearer", _cortex("https://cortex.example", None, "api_key unusable (revoked)"))
    out = me.mesh_bearer(probe=lambda url: {"edition": "local"})
    assert out["url"] == "https://cortex.example" and out["bearer"] is None and out["edition"] == "cortex"


@pytest.mark.live_local_probe
def test_the_default_probe_reads_a_real_health_document():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy", "edition": "local"}).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert me._probe_health(f"http://127.0.0.1:{server.server_port}/v1/health")["edition"] == "local"
        assert me._probe_health("http://127.0.0.1:1/v1/health") is None  # nothing listening: None, fast
    finally:
        server.shutdown()


def test_an_explicit_other_host_never_gets_the_local_bearer(monkeypatch):
    """project-register --cortex-url https://other with no --api-key sent the local token to that host (1.15.1 sweep)."""
    from types import SimpleNamespace

    from empirica.cli.command_handlers import projects_commands as pc

    monkeypatch.setattr(
        me, "mesh_bearer", lambda *a, **k: {"url": "http://127.0.0.1:8000", "bearer": "local", "edition": "local"}
    )
    url, key = pc._resolve_cortex_config(SimpleNamespace(cortex_url="https://other.example", api_key=None))
    assert url == "https://other.example" and key is None
