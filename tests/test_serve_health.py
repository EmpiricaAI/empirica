"""Tests for serve daemon health-check backend-URL resolution.

The health probes (`_check_ollama` / `_check_qdrant`) must reflect the ACTUAL
configured backend (env > config.yaml > localhost), not a hardcoded localhost —
otherwise remote-Ollama/Qdrant setups false-negative in the Diagnostics tab.
"""

from __future__ import annotations

from unittest.mock import patch

from empirica.api import serve_app as sa

# ── ollama URL resolution (env > config.yaml embeddings.ollama_url > localhost) ──


def test_resolve_ollama_env_wins(monkeypatch):
    monkeypatch.setenv("EMPIRICA_OLLAMA_URL", "http://halo-strix:11434")
    with patch.object(sa, "_config_ollama_url", return_value="http://cfg:11434"):
        assert sa._resolve_ollama_url() == "http://halo-strix:11434"


def test_resolve_ollama_config_when_no_env(monkeypatch):
    monkeypatch.delenv("EMPIRICA_OLLAMA_URL", raising=False)
    with patch.object(sa, "_config_ollama_url", return_value="http://empirica-server:11434/"):
        assert sa._resolve_ollama_url() == "http://empirica-server:11434"  # trailing slash stripped


def test_resolve_ollama_localhost_fallback(monkeypatch):
    monkeypatch.delenv("EMPIRICA_OLLAMA_URL", raising=False)
    with patch.object(sa, "_config_ollama_url", return_value=None):
        assert sa._resolve_ollama_url() == "http://localhost:11434"


def test_config_ollama_url_none_when_no_config(tmp_path, monkeypatch):
    # _config_ollama_url reads ~/.empirica/config.yaml directly (no heavy import)
    monkeypatch.setenv("HOME", str(tmp_path))  # empty home → no config.yaml
    assert sa._config_ollama_url() is None


def test_resolve_ollama_does_not_import_embeddings(monkeypatch):
    # The /health hot path must NOT import the heavy embeddings/openai module.
    import sys
    monkeypatch.delenv("EMPIRICA_OLLAMA_URL", raising=False)
    sys.modules.pop("empirica.core.qdrant.embeddings", None)
    with patch.object(sa, "_config_ollama_url", return_value=None):
        sa._resolve_ollama_url()
    assert "empirica.core.qdrant.embeddings" not in sys.modules


# ── qdrant URL resolution (EMPIRICA_QDRANT_URL > localhost) ──


def test_resolve_qdrant_env_wins(monkeypatch):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://pilot-qdrant:6333/")
    assert sa._resolve_qdrant_url() == "http://pilot-qdrant:6333"  # trailing slash stripped


def test_resolve_qdrant_localhost_fallback(monkeypatch):
    monkeypatch.delenv("EMPIRICA_QDRANT_URL", raising=False)
    assert sa._resolve_qdrant_url() == "http://localhost:6333"


# ── the probes hit the RESOLVED host, not hardcoded localhost ──


def test_check_ollama_probes_resolved_url(monkeypatch):
    monkeypatch.setenv("EMPIRICA_OLLAMA_URL", "http://remote-ollama:11434")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        from contextlib import nullcontext
        return nullcontext()

    with patch("urllib.request.urlopen", fake_urlopen):
        assert sa._check_ollama() is True
    assert seen["url"] == "http://remote-ollama:11434/api/tags"


def test_check_qdrant_probes_resolved_url(monkeypatch):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://remote-qdrant:6333")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        from contextlib import nullcontext
        return nullcontext()

    with patch("urllib.request.urlopen", fake_urlopen):
        assert sa._check_qdrant() is True
    assert seen["url"] == "http://remote-qdrant:6333/collections"


def test_check_ollama_false_on_unreachable(monkeypatch):
    monkeypatch.delenv("EMPIRICA_OLLAMA_URL", raising=False)
    with patch("empirica.core.qdrant.embeddings._load_config_file", return_value={}), \
         patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        assert sa._check_ollama() is False
