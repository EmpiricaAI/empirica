"""A failed embed gives no vector, never a hashed one (goal 6f1e9bb6, David 2026-11-11).

The ollama and API providers used to return a hashed vector on a timeout, a refused connection or an error reply.
``_get_embedding_safe`` cached it as a success and writers stored it in semantic collections, with the same dimensions,
so nothing failed and nothing said so. No network: ``requests.post`` is replaced.
"""

from __future__ import annotations

import pytest

requests = pytest.importorskip("requests")

from empirica.core.qdrant import connection, embeddings  # noqa: E402


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("EMPIRICA_EMBEDDINGS_PROVIDER", "ollama")
    monkeypatch.setenv("EMPIRICA_EMBEDDINGS_MODEL", "qwen3-embedding")
    monkeypatch.setenv("EMPIRICA_OLLAMA_URL", "http://127.0.0.1:9")
    p = embeddings.EmbeddingsProvider()
    monkeypatch.setattr(embeddings, "_provider_singleton", p)
    connection._embed_memo.clear()
    yield p
    connection._embed_memo.clear()


def _post_raising(exc, calls):
    def post(*a, **k):
        calls.append(a)
        raise exc

    return post


@pytest.mark.parametrize("exc", [requests.exceptions.ConnectionError("refused"), requests.exceptions.Timeout("slow")])
def test_unreachable_model_raises_and_backs_off(provider, monkeypatch, exc):
    calls: list = []
    monkeypatch.setattr(requests, "post", _post_raising(exc, calls))
    with pytest.raises(embeddings.EmbeddingUnavailable):
        provider.embed("hello")
    with pytest.raises(embeddings.EmbeddingUnavailable):
        provider.embed("hello again")
    assert len(calls) == 1  # the second embed did not ask the model again inside the backoff


def test_safe_embed_returns_none_and_caches_nothing(provider, monkeypatch):
    calls: list = []
    monkeypatch.setattr(requests, "post", _post_raising(requests.exceptions.ConnectionError("refused"), calls))
    assert connection._get_embedding_safe("a note") is None
    assert "a note" not in connection._embed_memo


def test_model_back_after_backoff_gives_a_real_vector(provider, monkeypatch):
    calls: list = []
    monkeypatch.setattr(requests, "post", _post_raising(requests.exceptions.ConnectionError("refused"), calls))
    assert connection._get_embedding_safe("a note") is None

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embedding": [0.5] * 1024}  # qwen3-embedding's 1024 dimensions

    monkeypatch.setattr(requests, "post", lambda *a, **k: Reply())
    provider._down_until = 0.0  # the backoff has passed
    assert connection._get_embedding_safe("a note") == [0.5] * 1024


def test_api_provider_error_raises(monkeypatch):
    monkeypatch.setenv("EMPIRICA_EMBEDDINGS_PROVIDER", "jina")
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    p = embeddings.EmbeddingsProvider()
    calls: list = []
    monkeypatch.setattr(requests, "post", _post_raising(requests.exceptions.HTTPError("401"), calls))
    with pytest.raises(embeddings.EmbeddingUnavailable):
        p.embed("hello")


def test_batch_gives_none_per_text_when_the_model_is_down(provider, monkeypatch):
    calls: list = []
    monkeypatch.setattr(requests, "post", _post_raising(requests.exceptions.ConnectionError("refused"), calls))
    assert provider.batch_embed(["a", "b"]) == [None, None]
    assert len(calls) == 1  # the batch failed once; the sequential fallback did not ask again


def test_explicit_local_provider_still_hashes(monkeypatch):
    monkeypatch.setenv("EMPIRICA_EMBEDDINGS_PROVIDER", "local")
    p = embeddings.EmbeddingsProvider()
    vec = p.embed("hello")
    assert isinstance(vec, list) and len(vec) > 0
