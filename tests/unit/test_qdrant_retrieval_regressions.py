from pathlib import Path
from unittest.mock import patch

from empirica.cli.command_handlers.project_embed import (
    _build_embedding_text,
    _compact_text,
    _has_indexed_python_files,
    _resolve_doc_path,
)
from empirica.core.qdrant.connection import (
    CollectionDimensionMismatchError,
    _ensure_collection_matches_vector,
)
from empirica.core.qdrant.embeddings import EmbeddingsProvider
from empirica.core.qdrant.memory import upsert_docs


class DummyDistance:
    COSINE = "cosine"


class DummyVectorParams:
    def __init__(self, size, distance):
        self.size = size
        self.distance = distance


class DummyPointStruct:
    def __init__(self, id, vector, payload):
        self.id = id
        self.vector = vector
        self.payload = payload


def test_resolve_doc_path_prefers_project_root_for_file_oriented_indexes(tmp_path):
    project_root = tmp_path
    root_file = project_root / "web" / "README.md"
    root_file.parent.mkdir(parents=True)
    root_file.write_text("root readme", encoding="utf-8")

    docs_file = project_root / "docs" / "web" / "README.md"
    docs_file.parent.mkdir(parents=True)
    docs_file.write_text("docs mirror", encoding="utf-8")

    resolved = _resolve_doc_path(str(project_root), "web/README.md")

    assert Path(resolved) == root_file


def test_build_embedding_text_uses_metadata_and_compact_excerpt():
    text = "line " * 200
    meta = {
        "tags": ["playwright", "auth"],
        "concepts": ["setup"],
        "questions": ["What files handle auth setup?"],
        "use_cases": ["test login"],
        "description": "Authentication entrypoint for Playwright E2E setup",
        "doc_type": "test-config",
    }

    built = _build_embedding_text("web/tests/e2e/auth-setup.ts", meta, text)

    assert "File: web/tests/e2e/auth-setup.ts" in built
    assert "Tags: playwright, auth" in built
    assert "Doc type: test-config" in built
    assert "Excerpt:" in built
    assert len(_compact_text(text, max_chars=240)) <= 244


def test_has_indexed_python_files_only_when_python_entries_exist():
    assert not _has_indexed_python_files({"web/README.md": {}, "web/src/index.ts": {}})
    assert _has_indexed_python_files({"docs/guide.md": {}, "scripts/tool.py": {}})


def test_upsert_docs_creates_collection_before_upsert():
    client = type("DummyClient", (), {})()
    client.created = None
    client.upsert_calls = []
    client.collection_exists = lambda name: False

    def create_collection(name=None, vectors_config=None, collection_name=None):
        client.created = (collection_name or name, vectors_config)

    def upsert(collection_name, points):
        client.upsert_calls.append((collection_name, points))

    client.create_collection = create_collection
    client.upsert = upsert

    with (
        patch("empirica.core.qdrant.memory._check_qdrant_available", return_value=True),
        patch(
            "empirica.core.qdrant.memory._get_qdrant_imports",
            return_value=(None, DummyDistance, DummyVectorParams, DummyPointStruct),
        ),
        patch("empirica.core.qdrant.memory._get_qdrant_client", return_value=client),
        patch(
            "empirica.core.qdrant.connection._get_qdrant_imports",
            return_value=(None, DummyDistance, DummyVectorParams, DummyPointStruct),
        ),
        patch("empirica.core.qdrant.connection._get_embeddings_batch", return_value=[[0.1, 0.2, 0.3]]),
        patch("empirica.core.qdrant.memory._docs_collection", return_value="project_test_docs"),
    ):
        count = upsert_docs("project-id", [{"id": 1, "text": "hello", "metadata": {"doc_path": "a.md"}}])

    assert count == 1
    assert client.created is not None
    assert client.created[0] == "project_test_docs"
    assert client.created[1].size == 3
    assert client.created[1].distance == DummyDistance.COSINE
    assert len(client.upsert_calls) == 1


def test_dimension_guard_raises_before_mismatched_qdrant_write():
    class DummyCollectionInfo:
        class Config:
            class Params:
                class Vectors:
                    size = 1024

                vectors = Vectors()

            params = Params()

        config = Config()

    client = type("DummyClient", (), {})()
    client.collection_exists = lambda name: True
    client.get_collection = lambda name: DummyCollectionInfo()

    with patch("empirica.core.qdrant.connection._get_provider_context", return_value="ollama/nomic-embed-text"):
        try:
            _ensure_collection_matches_vector(client, "epistemic_events", 768, create_if_missing=False)
            raised = None
        except Exception as exc:  # pragma: no branch - assertion below validates type
            raised = exc

    assert isinstance(raised, CollectionDimensionMismatchError)
    assert "epistemic_events" in str(raised)
    assert "1024d" in str(raised)
    assert "768d" in str(raised)
    assert "rebuild --qdrant" in str(raised)


def test_embed_ollama_retries_with_smaller_prompts_before_fallback():
    provider = EmbeddingsProvider.__new__(EmbeddingsProvider)
    provider.ollama_url = "http://localhost:11434"
    provider.model = "all-minilm"
    provider._vector_size = 384
    provider._embed_local_hash = lambda text: [9.0]

    prompts = []

    class DummyResponse:
        def __init__(self, embedding):
            self._embedding = embedding

        def raise_for_status(self):
            return None

        def json(self):
            return {"embedding": self._embedding}

    def fake_post(url, json, timeout):
        prompts.append(json["prompt"])
        if len(prompts) == 1:
            return DummyResponse([])
        return DummyResponse([0.1] * 384)

    with patch("requests.post", side_effect=fake_post), patch("time.sleep", return_value=None):
        result = provider._embed_ollama("token " * 500)

    assert len(prompts) == 2
    assert len(prompts[1]) <= len(prompts[0])
    assert result == [0.1] * 384


def test_embed_ollama_sends_keep_alive_and_degrades_on_timeout():
    """keep_alive re-arms model warmth on every call (self-healing pin); a read
    -timeout degrades to local hash IMMEDIATELY — no 3x shrink-retry — because the
    model LOAD, not the prompt size, is the bottleneck. Regression guard for the
    ~120s preflight hang (mesh-support prop_4pqqclkpcjhnjnszspasu7qvqm)."""
    import requests

    provider = EmbeddingsProvider.__new__(EmbeddingsProvider)
    provider.ollama_url = "http://localhost:11434"
    provider.model = "qwen3-embedding"
    provider._vector_size = 1024
    provider._embed_local_hash = lambda text: [7.0]

    captured = {}
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json["prompt"])
        captured["keep_alive"] = json.get("keep_alive")
        raise requests.exceptions.ReadTimeout("simulated cold-load")

    with patch("requests.post", side_effect=fake_post), patch("time.sleep", return_value=None):
        result = provider._embed_ollama("some task context")

    assert captured["keep_alive"]  # keep_alive present in the embed payload
    assert len(calls) == 1  # timeout degrades on first attempt, no shrink-retry
    assert result == [7.0]  # local-hash fallback, never a hang


def test_get_embedding_safe_memoizes_by_query_string():
    """retrieve_task_patterns fans out ~20 searches per preflight, many re-embedding
    the same string. Memoize at the _get_embedding_safe chokepoint so each DISTINCT
    query embeds at most once per process."""
    from empirica.core.qdrant import connection as C

    C._embed_memo.clear()
    calls = []

    def fake_get_embedding(text):
        calls.append(text)
        return [0.5] * 1024

    with patch("empirica.core.qdrant.embeddings.get_embedding", side_effect=fake_get_embedding):
        v1 = C._get_embedding_safe("same query")
        v2 = C._get_embedding_safe("same query")
        C._get_embedding_safe("different query")

    assert v1 is v2  # cache returns the identical object on hit
    assert calls == ["same query", "different query"]  # each distinct string embedded once


def test_get_embedding_safe_does_not_cache_failures():
    """A transient backend failure (None) must not poison later calls for the same string."""
    from empirica.core.qdrant import connection as C

    C._embed_memo.clear()
    outcomes = [None, [0.9] * 1024]

    with patch("empirica.core.qdrant.embeddings.get_embedding", side_effect=lambda text: outcomes.pop(0)):
        first = C._get_embedding_safe("q")  # fails -> None, NOT cached
        second = C._get_embedding_safe("q")  # retries -> real vector

    assert first is None
    assert second == [0.9] * 1024


def test_get_embedding_safe_empty_text_returns_none():
    """Empty text is a no-op — never hits the backend, never cached."""
    from empirica.core.qdrant import connection as C

    assert C._get_embedding_safe("") is None
