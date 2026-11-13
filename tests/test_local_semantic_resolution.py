"""Clients use the local server's store and model last, after anything configured (goal db2cf3fa, T4.3).

Order: an explicit URL, the per-org resolver a Cortex seat installs, EMPIRICA_QDRANT_URL, a server on 6333, then the local
server when its health advertises the service. Embeddings: a configured provider, Ollama with a model, then the local
server's /ollama, then hashed vectors. So moving to Cortex changes nothing here. The probes are stubbed: this box runs a
real Qdrant on 6333, and a test must not depend on that.
"""

from __future__ import annotations

import urllib.request

import pytest

import empirica.core.qdrant.connection as qc

HEALTH = {"edition": "local", "services": {"vectors": "/qdrant", "embeddings": "/ollama"}}


@pytest.fixture
def no_6333(monkeypatch):
    def refuse(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        raise OSError(f"refused {url}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.delenv("EMPIRICA_QDRANT_URL", raising=False)
    monkeypatch.setenv("EMPIRICA_SERVE_PORT", "8123")
    monkeypatch.setattr(qc, "_local_service_cache", {})
    monkeypatch.setattr(qc, "_url_resolver", None)


def _health(monkeypatch, doc):
    import empirica.core.mesh_endpoint as me

    monkeypatch.setattr(me, "_probe_health", lambda url: doc)


def test_with_nothing_configured_the_store_is_the_local_servers(monkeypatch, no_6333):
    _health(monkeypatch, HEALTH)
    assert qc.local_server_service("vectors") == "http://127.0.0.1:8123/qdrant"
    assert qc.qdrant_search_client().url == "http://127.0.0.1:8123/qdrant"
    client = qc._get_qdrant_client()
    assert client is not None and "8123" in str(getattr(client._client, "rest_uri", ""))


def test_a_configured_store_wins_over_the_local_server(monkeypatch, no_6333):
    _health(monkeypatch, HEALTH)
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://cortex-qdrant.example:6333")
    assert qc.qdrant_search_client().url == "http://cortex-qdrant.example:6333"


def test_a_local_server_without_the_service_is_not_used(monkeypatch, no_6333):
    _health(monkeypatch, {"edition": "local", "services": {"vectors": None}})
    assert qc.local_server_service("vectors") is None and qc.qdrant_search_client() is None
    _health(monkeypatch, {"edition": "cortex", "services": {"vectors": "/qdrant"}})
    monkeypatch.setattr(qc, "_local_service_cache", {})
    assert qc.local_server_service("vectors") is None, "only the local edition"


def test_embeddings_auto_uses_the_local_model_before_hashed_vectors(monkeypatch, no_6333):
    import empirica.core.qdrant.embeddings as emb

    _health(monkeypatch, HEALTH)
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_MODEL", raising=False)
    monkeypatch.setattr(emb, "_check_ollama_available", lambda url: False)
    monkeypatch.setattr(emb, "_load_config_file", lambda: {})
    p = emb.EmbeddingsProvider()
    assert (p.provider, p.ollama_url, p.model) == ("ollama", "http://127.0.0.1:8123/ollama", "multilingual-e5-small")


def test_without_a_local_model_auto_still_falls_back_to_hashed(monkeypatch, no_6333):
    import empirica.core.qdrant.embeddings as emb

    _health(monkeypatch, None)
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.setattr(emb, "_check_ollama_available", lambda url: False)
    monkeypatch.setattr(emb, "_load_config_file", lambda: {})
    assert emb.EmbeddingsProvider().provider == "local"


def test_ollama_down_with_a_configured_store_does_not_switch_to_the_local_model(monkeypatch, no_6333):
    # The local model's 384-d vectors belong in the local store only. With a configured Qdrant (here the env; a server on
    # 6333 or Cortex's resolver alike) holding 1024-d collections, a short Ollama outage at process start switched to the
    # local model: searches failed and create-first writers made 384-d collections there (1.16.0 sweep).
    import empirica.core.qdrant.embeddings as emb

    _health(monkeypatch, HEALTH)
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://cortex-qdrant.example:6333")
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_MODEL", raising=False)
    monkeypatch.setattr(emb, "_check_ollama_available", lambda url: False)
    monkeypatch.setattr(emb, "_load_config_file", lambda: {})
    p = emb.EmbeddingsProvider()
    assert p.model != "multilingual-e5-small" and p.provider == "local", (p.provider, p.model)


def test_per_org_routing_counts_as_a_configured_store(monkeypatch, no_6333):
    # The guard compared only the project-less store; a seat routing Qdrant per organisation (CORTEX_QDRANT_URLS_BY_ORG)
    # resolves its writes per project, so the local model must not be chosen there either (1.16.0 sweep, pass 2).
    import empirica.core.qdrant.embeddings as emb

    _health(monkeypatch, HEALTH)
    monkeypatch.setenv("CORTEX_QDRANT_URLS_BY_ORG", "org-a=http://qdrant-a:6333")
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_MODEL", raising=False)
    monkeypatch.setattr(emb, "_check_ollama_available", lambda url: False)
    monkeypatch.setattr(emb, "_load_config_file", lambda: {})
    assert emb.EmbeddingsProvider().model != "multilingual-e5-small"


def test_once_the_local_model_is_chosen_6333_coming_back_does_not_take_its_vectors(monkeypatch, no_6333):
    # The provider picks the local 384-d model once per process, while 6333 is down; the store was re-probed later, so the
    # process wrote 384-d vectors into 6333's collections once it returned (1.16.1 sweep, goal 5b9aa623).
    import empirica.core.qdrant.embeddings as emb

    _health(monkeypatch, HEALTH)
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_MODEL", raising=False)
    monkeypatch.setattr(emb, "_check_ollama_available", lambda url: False)
    monkeypatch.setattr(emb, "_load_config_file", lambda: {})
    assert emb.EmbeddingsProvider().model == "multilingual-e5-small"

    monkeypatch.setattr(qc, "_probe_default_server", lambda: True)  # 6333 is back

    class Ok:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, *a, **k: Ok())
    assert qc.store_url() == "http://127.0.0.1:8123/qdrant"
    client = qc._get_qdrant_client()
    assert "8123" in str(getattr(client._client, "rest_uri", ""))


def test_without_the_local_model_6333_is_used_when_it_answers(monkeypatch, no_6333):
    monkeypatch.setattr(qc, "_probe_default_server", lambda: True)
    assert qc.store_url() == "http://localhost:6333"  # no pin: the configured server wins as before
