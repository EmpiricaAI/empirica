"""Provisioning loads the local embedding model once, so no hook waits on its download (goal db2cf3fa, T4.5).

The first use used to be whichever process embedded first, often a hook with a 30 s timeout, and the download took 15-30 s.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import provision_practice_commands as pp

REAL_STEP = pp._embedding_model_step  # captured at import, before conftest's autouse stub


@pytest.fixture
def local_only(monkeypatch):
    import empirica.core.qdrant.connection as qc
    import empirica.core.qdrant.embeddings as emb

    monkeypatch.setattr(emb, "_check_ollama_available", lambda url: False)
    monkeypatch.setattr(emb, "_load_config_file", lambda: {})
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.delenv("CORTEX_QDRANT_URLS_BY_ORG", raising=False)
    services = {"embeddings": "http://127.0.0.1:8000/ollama", "vectors": "http://127.0.0.1:8000/qdrant"}
    monkeypatch.setattr(qc, "local_server_service", lambda name: services.get(name))
    # The store this machine would use is the local server's: never ask the real localhost:6333 from a test.
    monkeypatch.setattr(qc, "store_url", lambda project_id=None: services["vectors"])


def _args(**kw):
    return SimpleNamespace(**{"no_local_server": False, "no_cortex": False, **kw})


def test_the_model_is_loaded_through_the_local_server(local_only):
    calls = []
    step = REAL_STEP(_args(), False, post=lambda url, body, t: calls.append((url, body, t)) or (200, b"{}"))
    assert step["changed"] is True and not step.get("error")
    assert calls == [("http://127.0.0.1:8000/ollama/api/embed", {"input": ["warm"]}, 600)]


def test_a_model_that_does_not_load_is_an_error_with_a_hint(local_only):
    step = REAL_STEP(_args(), False, post=lambda *a: (503, b"{}"))
    assert "did not load (503)" in step["error"] and "doctor" in step["hint"]


def test_with_ollama_the_local_model_is_not_downloaded(monkeypatch):
    import empirica.core.qdrant.embeddings as emb

    monkeypatch.setattr(emb, "_check_ollama_available", lambda url: True)
    step = REAL_STEP(_args(), False, post=lambda *a: pytest.fail("must not download"))
    assert step["changed"] is False and "Ollama" in step["note"]


def test_dry_run_and_the_opt_outs_download_nothing(local_only):
    never = lambda *a: pytest.fail("must not download")  # noqa: E731
    assert REAL_STEP(_args(), True, post=never)["changed"] is True
    assert REAL_STEP(_args(no_local_server=True), False, post=never)["changed"] is False


def test_ollama_set_in_the_config_file_is_probed_there(local_only, monkeypatch):
    # The step asked only EMPIRICA_OLLAMA_URL or localhost, so an Ollama set in ~/.empirica/config.yaml went unseen and the
    # 135 MB local model was downloaded anyway (1.16.x, goal a34eef6d).
    import empirica.core.qdrant.embeddings as emb

    monkeypatch.delenv("EMPIRICA_OLLAMA_URL", raising=False)
    monkeypatch.setattr(emb, "_load_config_file", lambda: {"ollama_url": "http://gpu-box:11434"})
    monkeypatch.setattr(emb, "_check_ollama_available", lambda url: url == "http://gpu-box:11434")
    out = REAL_STEP(_args(), dry_run=True)
    assert out["changed"] is False and "Ollama" in out["note"]


def test_an_explicit_provider_needs_no_local_model(local_only, monkeypatch):
    import empirica.core.qdrant.embeddings as emb

    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.setattr(emb, "_load_config_file", lambda: {"provider": "openai"})
    out = REAL_STEP(_args(), dry_run=True)
    assert out["changed"] is False and "openai" in out["note"]


def test_a_configured_qdrant_needs_no_local_model(local_only, monkeypatch):
    # auto uses the local server's model only with the local server's store; with a configured Qdrant it never would.
    import empirica.core.qdrant.connection as qc

    monkeypatch.setattr(qc, "store_url", lambda project_id=None: "http://qdrant.example:6333")
    out = REAL_STEP(_args(), dry_run=True)
    assert out["changed"] is False and "Qdrant" in out["note"]
