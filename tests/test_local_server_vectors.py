"""The local server's Qdrant-compatible routes, driven by the real clients (goal db2cf3fa, T4 step 1).

The routes are only right if qdrant-client's own REST client and RestQdrant (the per-prompt hot path) work against them
unchanged, with nothing but a base URL. So this starts the serve app in uvicorn on a scratch port, with the store in a
temporary folder, and drives every operation empirica uses through those clients.
"""

from __future__ import annotations

import socket
import threading
import time
import warnings

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
pytest.importorskip("qdrant_client")


@pytest.fixture
def base_url(tmp_path, monkeypatch):
    import uvicorn

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMPIRICA_SERVE_HOST", "127.0.0.1")
    monkeypatch.setenv("EMPIRICA_LOCAL_VECTORS", str(tmp_path / "vectors"))
    monkeypatch.setenv("EMPIRICA_LOCAL_USER", "tester")
    from empirica.api.serve_app import create_serve_app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_serve_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


def test_the_qdrant_client_works_against_the_local_store_unchanged(base_url):
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as m

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a failed version check is a UserWarning: it must not happen
        client = QdrantClient(url=f"{base_url}/qdrant", timeout=10)
        name = "project_p_memory"
        client.create_collection(name, vectors_config=m.VectorParams(size=3, distance=m.Distance.COSINE))
    assert client.collection_exists(name) and not client.collection_exists("nope")
    vectors = client.get_collection(name).config.params.vectors
    assert isinstance(vectors, m.VectorParams) and vectors.size == 3  # one unnamed vector, as empirica creates
    assert name in [c.name for c in client.get_collections().collections]

    client.upsert(
        name,
        points=[
            m.PointStruct(id=1, vector=[1.0, 0.0, 0.0], payload={"type": "finding", "text": "widget cache"}),
            m.PointStruct(id=2, vector=[0.0, 1.0, 0.0], payload={"type": "unknown", "text": "other"}),
        ],
    )
    finding_only = m.Filter(must=[m.FieldCondition(key="type", match=m.MatchValue(value="finding"))])
    hits = client.query_points(name, query=[0.9, 0.1, 0.0], query_filter=finding_only, limit=5, with_payload=True)
    assert [p.id for p in hits.points] == [1] and hits.points[0].payload["text"] == "widget cache"
    above = client.query_points(name, query=[0.0, 1.0, 0.0], limit=5, score_threshold=0.9)
    assert [p.id for p in above.points] == [2]

    assert [r.id for r in client.retrieve(name, ids=[2], with_vectors=True)] == [2]
    points, nxt = client.scroll(name, scroll_filter=finding_only, limit=10, with_payload=True)
    assert [p.id for p in points] == [1] and nxt is None
    assert client.count(name, exact=True).count == 2

    client.set_payload(name, payload={"resolved": True}, points=[1])
    assert client.retrieve(name, ids=[1])[0].payload["resolved"] is True

    client.delete(name, points_selector=m.FilterSelector(filter=finding_only))
    client.delete(name, points_selector=m.PointIdsList(points=[2]))
    assert client.count(name, exact=True).count == 0
    client.delete_collection(name)
    assert not client.collection_exists(name)


def test_rest_qdrant_and_its_search_fallback_work_against_it(base_url):
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as m

    from empirica.core.qdrant.rest import RestQdrant

    client = QdrantClient(url=f"{base_url}/qdrant", timeout=10)
    client.create_collection("c", vectors_config=m.VectorParams(size=2, distance=m.Distance.COSINE))
    client.upsert("c", points=[m.PointStruct(id=7, vector=[1.0, 0.0], payload={"artifact_id": "a1"})])
    out = RestQdrant(f"{base_url}/qdrant").query_points(collection_name="c", query=[1.0, 0.0], limit=3)
    assert [(p.id, p.payload["artifact_id"]) for p in out.points] == [(7, "a1")]


def test_a_missing_collection_is_a_404_the_client_raises_on(base_url):
    from qdrant_client import QdrantClient
    from qdrant_client.http.exceptions import UnexpectedResponse

    with pytest.raises(UnexpectedResponse, match="404"):
        QdrantClient(url=f"{base_url}/qdrant", timeout=10).get_collection("never_made")


def test_health_advertises_the_vector_service(base_url):
    import json
    import urllib.request

    with urllib.request.urlopen(f"{base_url}/v1/health", timeout=5) as r:
        doc = json.loads(r.read())
    assert doc["edition"] == "local" and doc["services"]["vectors"] == "/qdrant"
