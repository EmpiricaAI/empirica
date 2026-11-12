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


def test_an_embed_while_the_model_loads_is_answered_at_once(monkeypatch):
    # The model loaded under the lock every embed waits on, so during a slow first download each request held a worker thread
    # until it finished (1.16.0 sweep, a34eef6d). A second caller is told "loading" at once.
    import threading

    from empirica.local_server import embedder

    started, release = threading.Event(), threading.Event()

    def slow_load():
        started.set()
        release.wait(5)
        return lambda texts: [[0.0] * 384 for _ in texts]

    model = embedder.Embedder(load=slow_load)
    first = threading.Thread(target=lambda: model(["warm"]))
    first.start()
    assert started.wait(5)
    with pytest.raises(embedder.ModelLoading):
        model(["second"])
    release.set()
    first.join(5)
    assert model(["after"]) == [[0.0] * 384]


def test_the_local_server_answer_expires(monkeypatch):
    # Cached for the life of the process, a "no local server" answer kept serve out of reach of a long-lived process
    # that asked before it started (1.16.0 sweep, a34eef6d).
    import empirica.core.mesh_endpoint as me
    import empirica.core.qdrant.connection as qc

    answers = iter([None, {"edition": "local", "services": {"vectors": "/qdrant"}}])
    monkeypatch.setattr(me, "_probe_health", lambda url: next(answers))
    monkeypatch.setattr(me, "local_server_url", lambda: "http://127.0.0.1:8000")
    clock = [1000.0]
    monkeypatch.setattr(qc, "_service_clock", lambda: clock[0], raising=False)
    qc._local_service_cache.clear()
    assert qc.local_server_service("vectors") is None  # serve not up yet
    clock[0] += 10
    assert qc.local_server_service("vectors") is None  # cached: not asked again within the window
    clock[0] += 21
    assert qc.local_server_service("vectors") == "http://127.0.0.1:8000/qdrant"  # asked again, serve is up
