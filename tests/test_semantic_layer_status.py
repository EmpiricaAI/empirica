"""Semantic search says when it is off (goal db2cf3fa, T1).

Fresh-machine probe (2026-11-11), no Qdrant and no Ollama: a logged finding warned "Qdrant is available; embedding
failed", project-search answered ok with zero results (indistinguishable from "nothing relevant"), and doctor had no
check at all. ``semantic_layer_status`` asks the server and names the embedding provider; search, the embed warning and
doctor report it.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import empirica.core.qdrant.connection as qc


class _Qdrant(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"result": {"collections": []}}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(qc, "_check_qdrant_available", lambda *a, **k: True)


@pytest.fixture
def qdrant_url():
    server = HTTPServer(("127.0.0.1", 0), _Qdrant)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _provider(monkeypatch, provider, model="m"):
    import empirica.core.qdrant.embeddings as emb

    monkeypatch.setattr(emb, "get_provider_info", lambda: {"provider": provider, "model": model, "vector_size": 3})


def test_no_server_answering_is_off_and_says_where_it_looked(monkeypatch, enabled):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:1")
    _provider(monkeypatch, "ollama")
    s = qc.semantic_layer_status()
    assert s["semantic"] is False and s["qdrant"].startswith("unreachable")
    assert "no Qdrant answers at http://127.0.0.1:1" in s["reason"]


def test_hashed_vectors_are_degraded_not_semantic(monkeypatch, enabled, qdrant_url):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", qdrant_url)
    _provider(monkeypatch, "local", "hash-1536")
    s = qc.semantic_layer_status()
    assert s["semantic"] is False and s["qdrant"] == "reachable" and "hashed vectors" in s["reason"]


def test_a_reachable_server_and_a_real_model_is_semantic(monkeypatch, enabled, qdrant_url):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", qdrant_url)
    _provider(monkeypatch, "ollama", "qwen3-embedding:0.6b")
    s = qc.semantic_layer_status()
    assert s["semantic"] is True and s["reason"] == "" and s["embeddings"] == "ollama/qwen3-embedding:0.6b"


def test_not_installed_or_disabled_is_off_without_asking_anyone(monkeypatch):
    monkeypatch.setattr(qc, "_check_qdrant_available", lambda *a, **k: False)
    s = qc.semantic_layer_status()
    assert s["qdrant"] == "off" and s["qdrant_url"] is None


def test_the_embed_warning_no_longer_claims_qdrant_is_available(monkeypatch, enabled):
    from empirica.cli.command_handlers.artifact_log_commands import _embed_warning

    monkeypatch.setattr(
        qc,
        "semantic_layer_status",
        lambda *a, **k: {
            "qdrant": "unreachable (URLError)",
            "reason": "semantic search is off: no Qdrant answers at X",
        },
    )
    text = _embed_warning(False, "finding")
    assert "Qdrant is available" not in text and "no Qdrant answers at X" in text


@pytest.mark.parametrize(
    "status, verdict",
    [
        ({"semantic": True, "qdrant": "reachable", "qdrant_url": "u", "embeddings": "ollama/m"}, "PASS"),
        ({"semantic": False, "qdrant": "off", "reason": "off"}, "SKIP"),
        ({"semantic": False, "qdrant": "unreachable (URLError)", "qdrant_url": "u", "reason": "no Qdrant"}, "WARN"),
        ({"semantic": False, "qdrant": "reachable", "qdrant_url": "u", "reason": "hashed vectors"}, "WARN"),
    ],
)
def test_doctor_reports_the_semantic_layer(status, verdict):
    from empirica.cli.command_handlers.doctor import check_semantic_layer

    check = check_semantic_layer(status_fn=lambda: status)
    assert check.status == verdict, check
