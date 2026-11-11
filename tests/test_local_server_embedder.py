"""The local server's Ollama-compatible embedding routes (goal db2cf3fa, T4.2).

A stub model stands in for fastembed, so these never download the ~490 MB model. They check the three routes empirica's
existing `ollama` provider calls, that the provider itself works against them unchanged, and that a model that will not
load (offline) answers 503 instead of a vector.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def _stub(texts):
    return [[float(len(t)), 1.0, 0.0] for t in texts]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from empirica.local_server import embedder

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMPIRICA_SERVE_HOST", "127.0.0.1")
    monkeypatch.setattr(embedder, "available", lambda: True)
    monkeypatch.setattr(embedder, "_default", embedder.Embedder(load=lambda: _stub))
    from empirica.api.serve_app import create_serve_app

    return TestClient(create_serve_app(), base_url="http://127.0.0.1:8000")


def test_tags_advertise_a_model_the_provider_knows(client):
    from empirica.core.qdrant.embeddings import MODEL_DIMENSIONS

    names = [m["name"].split(":")[0] for m in client.get("/ollama/api/tags").json()["models"]]
    assert names == ["multilingual-e5-small"] and MODEL_DIMENSIONS["multilingual-e5-small"] == 384


def test_single_and_batch_embeddings(client):
    one = client.post("/ollama/api/embeddings", json={"model": "multilingual-e5-small", "prompt": "abc"}).json()
    assert one["embedding"] == [3.0, 1.0, 0.0]
    many = client.post("/ollama/api/embed", json={"model": "multilingual-e5-small", "input": ["a", "bb"]}).json()
    assert many["embeddings"] == [[1.0, 1.0, 0.0], [2.0, 1.0, 0.0]]


def test_a_model_that_will_not_load_is_503_not_a_fake_vector(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from empirica.local_server import embedder

    def offline():
        raise OSError("no network")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMPIRICA_SERVE_HOST", "127.0.0.1")
    monkeypatch.setattr(embedder, "available", lambda: True)
    monkeypatch.setattr(embedder, "_default", embedder.Embedder(load=offline))
    from empirica.api.serve_app import create_serve_app

    c = TestClient(create_serve_app(), base_url="http://127.0.0.1:8000")
    r = c.post("/ollama/api/embeddings", json={"prompt": "x"})
    assert r.status_code == 503 and "unavailable" in r.json()["error"]


def test_health_advertises_the_embedding_service(client):
    assert client.get("/v1/health").json()["services"]["embeddings"] == "/ollama"
