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
    monkeypatch.setattr(qc, "local_server_service", lambda name: "http://127.0.0.1:8000/ollama")


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
