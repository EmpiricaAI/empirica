"""The embeddings hash fallback can be refused, is said out loud, and is left when Ollama comes back (prop_tecek2mq5vgadcihzozgdihdla).

With the provider on `auto` and Ollama unreachable when the provider was built, every embed returned a hash vector of the right
size for the rest of the process. The writes succeeded and the vectors were not semantic, so the damage showed only as search
misses: hosted Cortex wrote them for 15 hours while its embedding server was off the tailnet.

- EMPIRICA_EMBEDDINGS_STRICT=1 (or `strict=true` in embeddings.conf): `auto` never falls back; an embed raises
  EmbeddingUnavailable until the embedder answers. An explicit `provider=local` (tests) is unaffected.
- Without it, falling back logs a warning once, provider info says `fallback: true`, and the first embed after Ollama answers
  again uses Ollama.
"""

from __future__ import annotations

import logging

import pytest

from empirica.core.qdrant import embeddings as E


@pytest.fixture
def ollama(monkeypatch, tmp_path):
    """Ollama's availability, switchable; no config file, no env from the box."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in (
        "EMPIRICA_EMBEDDINGS_PROVIDER",
        "EMPIRICA_EMBEDDINGS_MODEL",
        "EMPIRICA_EMBEDDINGS_STRICT",
        "EMPIRICA_OLLAMA_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(E, "_load_config_file", dict)
    monkeypatch.setattr(E, "_fallback_warned", False)  # once per process: start each test as a fresh process
    up = {"v": False}
    monkeypatch.setattr(E, "_check_ollama_available", lambda _url=None: up["v"])
    from empirica.core.qdrant import connection

    monkeypatch.setattr(connection, "local_server_service", lambda _name: None)
    monkeypatch.setattr(E.EmbeddingsProvider, "_embed_ollama", lambda self, text: [0.5] * (self._vector_size or 4))
    return up


def test_without_strict_mode_the_fallback_is_hash_and_says_so_once(ollama, caplog):
    with caplog.at_level(logging.WARNING, logger=E.logger.name):
        p = E.EmbeddingsProvider()
        p.embed("a b c")
        p.embed("d e")
    assert p.provider == "local" and p.is_hash_fallback is True
    warnings = [r for r in caplog.records if "hash" in r.getMessage() and r.levelno == logging.WARNING]
    assert len(warnings) == 1, "said once, not per embed"
    assert "EMPIRICA_EMBEDDINGS_STRICT" in warnings[0].getMessage()


def test_a_process_on_the_fallback_uses_ollama_once_it_answers(ollama):
    p = E.EmbeddingsProvider()
    hashed = p.embed("one two")
    assert sum(1 for v in hashed if v) <= 2, "a hash vector: one non-zero per token"
    ollama["v"] = True
    assert p.embed("one two") == [0.5] * p._vector_size
    assert (p.provider, p.is_hash_fallback) == ("ollama", False)
    assert p.model == E.DEFAULT_MODELS["ollama"]


def test_strict_mode_raises_instead_of_writing_hash_vectors(ollama, monkeypatch):
    monkeypatch.setenv("EMPIRICA_EMBEDDINGS_STRICT", "1")

    def unreachable(self, text):
        raise self._unavailable("connection refused", down=True)

    monkeypatch.setattr(E.EmbeddingsProvider, "_embed_ollama", unreachable)
    p = E.EmbeddingsProvider()
    assert (p.provider, p.is_hash_fallback) == ("ollama", False)
    with pytest.raises(E.EmbeddingUnavailable):
        p.embed("anything")


def test_strict_mode_from_the_config_file(ollama, monkeypatch):
    monkeypatch.setattr(E, "_load_config_file", lambda: {"strict": "true"})
    assert E.EmbeddingsProvider().provider == "ollama"


def test_strict_mode_leaves_an_explicit_local_provider_alone(ollama, monkeypatch):
    monkeypatch.setenv("EMPIRICA_EMBEDDINGS_STRICT", "1")
    monkeypatch.setenv("EMPIRICA_EMBEDDINGS_PROVIDER", "local")
    p = E.EmbeddingsProvider()
    assert p.provider == "local" and p.is_hash_fallback is False
    assert len(p.embed("x")) == p._vector_size


def test_provider_info_reports_the_fallback(ollama, monkeypatch):
    monkeypatch.setattr(E, "_provider_singleton", None)
    info = E.get_provider_info()
    assert info["fallback"] is True and info["strict"] is False
