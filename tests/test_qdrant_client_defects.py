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


def test_an_empty_answer_from_the_local_fallback_store_is_unknown_not_zero(monkeypatch):
    # With 6333 briefly down and nothing configured, store_url falls through to the local server's store, which holds none of
    # a project whose vectors live on 6333; reading that as "no collections" licensed a delete that orphaned them (1.16.x).
    import io
    import json
    import urllib.request

    from empirica.data.repositories import workspace_db

    fallback = "http://127.0.0.1:8765/qdrant"
    monkeypatch.setattr(connection, "store_url", lambda project_id=None: fallback)
    monkeypatch.setattr(connection, "_service_url", lambda qdrant_url=None, project_id=None: None)
    monkeypatch.setattr(connection, "local_server_service", lambda s: fallback if s == "vectors" else None)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: io.BytesIO(
            json.dumps({"result": {"collections": [{"name": "project_other_memory"}]}}).encode()
        ),
    )
    repo = workspace_db.WorkspaceDBRepository.__new__(workspace_db.WorkspaceDBRepository)
    monkeypatch.setattr(repo, "_execute", lambda *a, **k: SimpleNamespace(fetchone=lambda: (0,)), raising=False)
    refs = repo.project_references("p1")
    assert refs["qdrant_collections"] is None and "fallback" in refs["qdrant_error"]


def test_the_local_fallback_store_is_authoritative_when_it_holds_the_project(monkeypatch):
    import io
    import json
    import urllib.request

    from empirica.data.repositories import workspace_db

    fallback = "http://127.0.0.1:8765/qdrant"
    monkeypatch.setattr(connection, "store_url", lambda project_id=None: fallback)
    monkeypatch.setattr(connection, "_service_url", lambda qdrant_url=None, project_id=None: None)
    monkeypatch.setattr(connection, "local_server_service", lambda s: fallback if s == "vectors" else None)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: io.BytesIO(
            json.dumps({"result": {"collections": [{"name": "project_p1_memory"}]}}).encode()
        ),
    )
    repo = workspace_db.WorkspaceDBRepository.__new__(workspace_db.WorkspaceDBRepository)
    monkeypatch.setattr(repo, "_execute", lambda *a, **k: SimpleNamespace(fetchone=lambda: (0,)), raising=False)
    assert repo.project_references("p1")["qdrant_collections"] == ["project_p1_memory"]


def test_a_scrub_of_a_fallback_store_holding_none_of_the_project_is_incomplete():
    from empirica.core import secret_scrub as ss

    class Store:
        def list_collections(self):
            return ["project_other_memory"]

        def scroll(self, collection):
            return []

    out = ss.scrub_qdrant(Store(), "p1", empty_is_unknown=True)
    assert "error" in out and "fallback" in out["error"]
    assert "error" not in ss.scrub_qdrant(Store(), "p1")  # a configured store that holds nothing is a clean answer


def test_the_6333_probe_is_asked_once_per_window(monkeypatch):
    # The per-prompt path asked twice (qdrant_search_client, then the embedding provider's store_url); a hanging listener
    # cost a second each time (1.16.x).
    import urllib.request

    calls: list = []

    def refuse(*a, **k):
        calls.append(1)
        raise OSError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(connection, "_probe_cache", {}, raising=False)
    assert connection._probe_default_server() is False
    assert connection._probe_default_server() is False
    assert len(calls) == 1


def test_epistemics_search_applies_its_filters(monkeypatch):
    # The filters were accepted and never read, so a --min-learning-delta search returned every trajectory (1.16.x).
    from empirica.core.qdrant import epistemics_store as es

    client = QdrantClient(":memory:")
    monkeypatch.setattr(es, "_check_qdrant_available", lambda *a, **k: True)
    monkeypatch.setattr(es, "_get_qdrant_client", lambda *a, **k: client)
    monkeypatch.setattr(es, "_get_embedding_safe", lambda text: [0.1, 0.2, 0.3, 0.4])
    items = [
        {
            "id": f"session_{i}",
            "text": "t",
            "metadata": {"session_id": str(i), "deltas": {"know": k}, "calibration_accuracy": q},
        }
        for i, (k, q) in enumerate([(0.05, "poor"), (0.3, "good"), (0.4, "fair")])
    ]
    assert es.upsert_epistemics("p1", items) == 3
    high = es.search_epistemics("p1", "q", filters={"deltas.know": {"$gte": 0.2}}, limit=5)
    assert sorted(r["session_id"] for r in high) == ["1", "2"]
    good = es.search_epistemics("p1", "q", filters={"calibration_accuracy": "good"}, limit=5)
    assert [r["session_id"] for r in good] == ["1"]


def test_the_not_embedded_warning_asks_about_the_projects_store(monkeypatch):
    # It asked about the default store, so a per-org seat was told "no Qdrant at localhost:6333" (1.16.0 sweep, a34eef6d).
    from empirica.cli.command_handlers import artifact_log_commands as alc

    asked: list = []
    monkeypatch.setattr(connection, "_check_qdrant_available", lambda *a, **k: True)
    monkeypatch.setattr(
        connection,
        "semantic_layer_status",
        lambda project_id=None: asked.append(project_id) or {"qdrant": "reachable", "semantic": True},
    )
    alc._embed_warning(False, "finding", project_id="p1")
    assert asked == ["p1"]


def test_doctors_semantic_check_asks_about_the_practices_store(monkeypatch, tmp_path):
    from empirica.cli.command_handlers import doctor

    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text("project_id: p1\n")
    monkeypatch.chdir(tmp_path)
    asked: list = []
    monkeypatch.setattr(
        connection,
        "semantic_layer_status",
        lambda project_id=None: asked.append(project_id) or {"semantic": True, "qdrant": "reachable"},
    )
    doctor.check_semantic_layer()
    assert asked == ["p1"]


def test_epistemics_search_fills_its_limit_past_the_first_fetch(monkeypatch):
    # B8 (1.16.1 sweep): one over-fetch, then a client-side filter, so matches ranked below the window were silently dropped.
    pytest.importorskip("qdrant_client")
    from qdrant_client import QdrantClient

    from empirica.core.qdrant import epistemics_store as es

    client = QdrantClient(":memory:")
    monkeypatch.setattr(es, "_check_qdrant_available", lambda *a, **k: True)
    monkeypatch.setattr(es, "_get_qdrant_client", lambda *a, **k: client)
    monkeypatch.setattr(
        es, "_get_embedding_safe", lambda text: [1.0, 0.0, 0.0, 0.0] if text != "far" else [0.0, 1.0, 0.0, 0.0]
    )
    near = [
        {"id": f"n{i}", "text": "near", "metadata": {"session_id": f"n{i}", "deltas": {"know": 0.0}}} for i in range(60)
    ]
    far = [
        {"id": f"f{i}", "text": "far", "metadata": {"session_id": f"f{i}", "deltas": {"know": 0.5}}} for i in range(3)
    ]
    assert es.upsert_epistemics("p1", near + far) == 63
    found = es.search_epistemics("p1", "q", filters={"deltas.know": {"$gte": 0.2}}, limit=3)
    assert sorted(r["session_id"] for r in found) == ["f0", "f1", "f2"]
