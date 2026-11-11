"""Qdrant client defects found in the 2026-11-11 inventory (goal 6f1e9bb6).

Each test runs against qdrant-client's in-memory store, so it needs no server and does not read the box's Qdrant.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.models import Distance, PointStruct, VectorParams  # noqa: E402

from empirica.core.qdrant import connection  # noqa: E402

KEYS = [
    "engagement",
    "know",
    "do",
    "context",
    "clarity",
    "coherence",
    "signal",
    "density",
    "state",
    "change",
    "completion",
    "impact",
    "uncertainty",
]


def _persona(persona_id: str, value: float):
    priors = dict.fromkeys(KEYS, value)
    return SimpleNamespace(
        persona=SimpleNamespace(persona_id=persona_id, epistemic_config=SimpleNamespace(priors=priors))
    )


# --- PersonaRegistry: client.search() is gone in qdrant-client 1.18, and a ScoredPoint has no .point ---------------


def test_find_similar_personas_returns_the_neighbour():
    from empirica.core.qdrant.persona_registry import PersonaRegistry

    client = QdrantClient(":memory:")
    registry = PersonaRegistry(client=client)
    for pid, value in (("a", 0.6), ("b", 0.61)):
        client.upsert(
            PersonaRegistry.COLLECTION_NAME,
            points=[
                PointStruct(
                    id=PersonaRegistry._persona_id_to_point_id(pid),
                    vector=[value + i * 0.01 for i in range(13)],
                    payload={"persona_id": pid, "name": pid},
                )
            ],
        )
    similar = registry.find_similar_personas(_persona("a", 0.6), limit=3, min_similarity=0.5)
    assert [p["persona_id"] for p in similar] == ["b"]
    assert similar[0]["similarity_score"] > 0.5


def test_registry_with_no_address_uses_the_resolved_store(monkeypatch):
    from empirica.core.qdrant.persona_registry import PersonaRegistry

    client = QdrantClient(":memory:")
    monkeypatch.setattr(connection, "_get_qdrant_client", lambda *a, **k: client)
    assert PersonaRegistry().client is client


def test_registry_with_no_store_raises_connection_error(monkeypatch):
    from empirica.core.qdrant.persona_registry import PersonaRegistry

    monkeypatch.setattr(connection, "_get_qdrant_client", lambda *a, **k: None)
    with pytest.raises(ConnectionError):
        PersonaRegistry()


# --- Delete by filter: the result carries no 'deleted' count, so both deletes always reported 0 -------------------


def _seeded(collection: str, payloads: list[dict]) -> QdrantClient:
    client = QdrantClient(":memory:")
    client.create_collection(collection, vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    client.upsert(
        collection,
        points=[PointStruct(id=str(uuid.uuid4()), vector=[0.1, 0.2, 0.3, 0.4], payload=p) for p in payloads],
    )
    return client


def test_source_chunk_delete_reports_what_it_deleted(monkeypatch):
    from empirica.cli.command_handlers import artifact_log_commands as alc
    from empirica.core.qdrant.collections import _docs_collection

    coll = _docs_collection("p1")
    client = _seeded(coll, [{"source_id": "s1"}, {"source_id": "s1"}, {"source_id": "other"}])
    monkeypatch.setattr(connection, "_get_qdrant_client", lambda *a, **k: client)
    assert alc._hard_delete_source_chunks("p1", "s1") == 2
    assert client.count(coll, exact=True).count == 1


def test_source_embed_delete_reports_what_it_deleted(monkeypatch):
    from empirica.cli.command_handlers import artifact_log_commands as alc
    from empirica.core.qdrant.collections import _memory_collection

    coll = _memory_collection("p1")
    client = _seeded(coll, [{"artifact_id": "s1", "type": "source"}, {"artifact_id": "s1", "type": "finding"}])
    monkeypatch.setattr(connection, "_get_qdrant_client", lambda *a, **k: client)
    assert alc._hard_delete_source_memory_embed("p1", "s1") == 1
    assert client.count(coll, exact=True).count == 1


# --- epistemics_store: 'session_<uuid>' is not a valid point id, and the collection was never created -------------


def test_trajectory_upsert_stores_a_session_point(monkeypatch):
    from empirica.core.qdrant import epistemics_store as es
    from empirica.core.qdrant.collections import _epistemics_collection

    client = QdrantClient(":memory:")
    seen: dict = {}

    def fake_client(*a, **k):
        seen.update(k)
        return client

    monkeypatch.setattr(es, "_check_qdrant_available", lambda *a, **k: True)
    monkeypatch.setattr(es, "_get_qdrant_client", fake_client)
    monkeypatch.setattr(es, "_get_embedding_safe", lambda text: [0.1, 0.2, 0.3, 0.4])
    sid = str(uuid.uuid4())
    item = {"id": f"session_{sid}", "text": "learned x", "metadata": {"session_id": sid}}
    assert es.upsert_epistemics("p1", [item]) == 1
    assert es.upsert_epistemics("p1", [item]) == 1  # the same session again replaces, it does not add
    coll = _epistemics_collection("p1")
    assert client.count(coll, exact=True).count == 1
    point = client.scroll(coll, limit=1)[0][0]
    assert point.payload["session_id"] == sid
    assert seen.get("project_id") == "p1"


# --- Resolver bypasses: one place answers "which store", with the per-org resolver and the local server ------------


def test_store_url_prefers_the_resolver_then_env(monkeypatch):
    monkeypatch.setattr(connection, "_url_resolver", lambda pid: "http://org-store:7335" if pid == "p1" else None)
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://env-store:6333")
    assert connection.store_url("p1") == "http://org-store:7335"
    assert connection.store_url("p2") == "http://env-store:6333"
    assert connection.store_url() == "http://env-store:6333"


def test_store_url_falls_back_to_the_local_server(monkeypatch):
    monkeypatch.setattr(connection, "_url_resolver", None)
    monkeypatch.delenv("EMPIRICA_QDRANT_URL", raising=False)
    monkeypatch.setattr(connection, "_probe_default_server", lambda: False)
    monkeypatch.setattr(connection, "local_server_service", lambda s: "http://127.0.0.1:8765/qdrant")
    assert connection.store_url() == "http://127.0.0.1:8765/qdrant"


def test_lessons_storage_uses_the_shared_resolver(monkeypatch):
    from empirica.core.lessons import storage

    sentinel = object()
    monkeypatch.setattr(connection, "_get_qdrant_client", lambda *a, **k: sentinel)
    assert storage._try_get_qdrant_client() is sentinel


def test_project_references_asks_the_resolved_store(monkeypatch, tmp_path):
    import io
    import json
    import urllib.request

    from empirica.data.repositories import workspace_db

    asked: list[str] = []

    def fake_urlopen(url, timeout=None):
        asked.append(url if isinstance(url, str) else url.full_url)
        return io.BytesIO(json.dumps({"result": {"collections": [{"name": "project_p1_memory"}]}}).encode())

    monkeypatch.setattr(connection, "store_url", lambda project_id=None: "http://org-store:7335")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    repo = workspace_db.WorkspaceDBRepository.__new__(workspace_db.WorkspaceDBRepository)
    monkeypatch.setattr(repo, "_execute", lambda *a, **k: SimpleNamespace(fetchone=lambda: (0,)), raising=False)
    refs = repo.project_references("p1")
    assert asked == ["http://org-store:7335/collections"]
    assert refs["qdrant_collections"] == ["project_p1_memory"]
