"""A model outage must not outlive the outage.

`_check_ollama_available` cached its verdict in a module global forever. A long-running process that probed while the embedding model
was missing (or Ollama was down) stayed on the hash fallback after the model came back, until the process was restarted
(cortex, prop_c37siadan, observed on prod). The verdict now expires: a failure is re-probed soon, a success is re-checked rarely, and
the cache is per URL.
"""

from __future__ import annotations

import pytest
import requests

from empirica.core.qdrant import embeddings as emb

URL = "http://ollama.test:11434"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


class _Server:
    """Stands in for requests.get: serves the model list it is given, or raises while 'down'."""

    def __init__(self):
        self.calls = []
        self.down = False
        self.models = ["qwen3-embedding:latest"]

    def __call__(self, url, timeout=None, **_kw):
        self.calls.append(url)
        if self.down:
            raise requests.ConnectionError("ollama is down")

        class R:
            status_code = 200

            def json(_self):
                return {"models": [{"name": m} for m in self.models]}

        return R()


@pytest.fixture
def world(monkeypatch):
    clock, server = _Clock(), _Server()
    monkeypatch.setattr(emb.time, "monotonic", clock)
    monkeypatch.setattr(requests, "get", server)
    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_MODEL", raising=False)
    emb._reset_ollama_probe()
    yield clock, server
    emb._reset_ollama_probe()


def test_a_failure_is_cached_briefly_then_the_next_call_probes_again(world):
    clock, server = world
    server.down = True
    assert emb._check_ollama_available(URL) is False
    assert emb._check_ollama_available(URL) is False
    assert len(server.calls) == 1, "inside the failure window the verdict is reused, not re-probed on every embedding"

    server.down = False
    clock.advance(emb._OLLAMA_FAILURE_TTL_S + 1)
    assert emb._check_ollama_available(URL) is True, "the outage ended; the process must notice without a restart"
    assert len(server.calls) == 2


def test_a_missing_model_is_noticed_when_it_appears(world):
    clock, server = world
    server.models = ["llama3"]
    assert emb._check_ollama_available(URL) is False
    server.models = ["qwen3-embedding:latest"]
    clock.advance(emb._OLLAMA_FAILURE_TTL_S + 1)
    assert emb._check_ollama_available(URL) is True


def test_a_success_is_reused_then_rechecked_so_a_removed_model_is_noticed(world):
    clock, server = world
    assert emb._check_ollama_available(URL) is True
    clock.advance(emb._OLLAMA_FAILURE_TTL_S + 1)
    assert emb._check_ollama_available(URL) is True
    assert len(server.calls) == 1, "a success must not be re-probed at the failure interval"

    server.down = True
    clock.advance(emb._OLLAMA_SUCCESS_TTL_S + 1)
    assert emb._check_ollama_available(URL) is False
    assert len(server.calls) == 2


def test_the_verdict_is_per_url(world):
    _, server = world
    assert emb._check_ollama_available(URL) is True
    server.down = True
    assert emb._check_ollama_available("http://other.test:11434") is False
    assert emb._check_ollama_available(URL) is True, "another URL's failure must not overwrite this URL's verdict"


def test_the_ttls_are_sane():
    assert 0 < emb._OLLAMA_FAILURE_TTL_S < emb._OLLAMA_SUCCESS_TTL_S
    assert emb._OLLAMA_FAILURE_TTL_S <= 120
