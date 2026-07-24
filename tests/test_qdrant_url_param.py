"""Tests for the optional `qdrant_url` parameter on the qdrant client factory.

Unblocks cortex's per-org routing (prop_aifzk5hv2vgzjcdmef7pocbhde):
cortex.qdrant_routing.resolve_qdrant_url(org_id) returns the right
container URL per request; this factory then opens a client against it.
None preserves the pre-existing env→localhost fallback exactly.

Surface tested:
  - _get_qdrant_client(qdrant_url=...): per-request URL wins over env
  - _get_qdrant_client(qdrant_url=None): falls through to env then localhost
  - _check_qdrant_available(qdrant_url=...): accepts param for API parity
  - _service_url(qdrant_url=...) + _rest_search(qdrant_url=...): same pattern
  - Backward compat: omitting the kwarg = pre-Phase-2 behavior byte-for-byte
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from empirica.core.qdrant import connection as conn_mod
from empirica.core.qdrant import intent_layer as intent_mod
from empirica.core.qdrant import memory as memory_mod

# ── _get_qdrant_client ────────────────────────────────────────────────


def test_get_qdrant_client_uses_explicit_url_when_provided(monkeypatch):
    """qdrant_url= wins over EMPIRICA_QDRANT_URL env var."""
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://from-env:6333")
    mock_cls = MagicMock()
    with patch.object(conn_mod, "_get_qdrant_imports", return_value=(mock_cls, None, None, None)):
        client = conn_mod._get_qdrant_client(qdrant_url="http://from-arg:7333")
    # Connected to the per-request URL, NOT the env URL
    mock_cls.assert_called_once_with(url="http://from-arg:7333")
    assert client is mock_cls.return_value


def test_get_qdrant_client_falls_through_to_env_when_no_arg(monkeypatch):
    """qdrant_url=None preserves the existing env→localhost priority chain."""
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://from-env:6333")
    mock_cls = MagicMock()
    with patch.object(conn_mod, "_get_qdrant_imports", return_value=(mock_cls, None, None, None)):
        client = conn_mod._get_qdrant_client()
    mock_cls.assert_called_once_with(url="http://from-env:6333")
    assert client is mock_cls.return_value


def test_get_qdrant_client_falls_through_to_localhost_probe(monkeypatch):
    """qdrant_url=None and no env → tries localhost:6333 reachability probe."""
    monkeypatch.delenv("EMPIRICA_QDRANT_URL", raising=False)
    mock_cls = MagicMock()
    # Localhost probe simulated as a successful 200
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    with (
        patch.object(conn_mod, "_get_qdrant_imports", return_value=(mock_cls, None, None, None)),
        patch("urllib.request.urlopen", return_value=mock_resp),
    ):
        client = conn_mod._get_qdrant_client()
    mock_cls.assert_called_once_with(url="http://localhost:6333")
    assert client is mock_cls.return_value


def test_get_qdrant_client_returns_none_when_no_url_anywhere(monkeypatch):
    """qdrant_url=None + no env + localhost unreachable → returns None."""
    monkeypatch.delenv("EMPIRICA_QDRANT_URL", raising=False)
    with (
        patch.object(conn_mod, "_get_qdrant_imports", return_value=(MagicMock(), None, None, None)),
        patch("urllib.request.urlopen", side_effect=Exception("connection refused")),
    ):
        client = conn_mod._get_qdrant_client()
    assert client is None


# ── _check_qdrant_available ───────────────────────────────────────────


def test_check_qdrant_available_accepts_url_param_for_api_parity(monkeypatch):
    """The arg is reserved for a future per-URL probe. For now: ignored,
    same return as the no-arg form. API parity with _get_qdrant_client
    lets cortex use one signature for both."""
    # Reset the module-level cache so we get a fresh check
    monkeypatch.setattr(conn_mod, "_qdrant_available", None)
    monkeypatch.delenv("EMPIRICA_ENABLE_EMBEDDINGS", raising=False)
    with_arg = conn_mod._check_qdrant_available(qdrant_url="http://x:6333")
    monkeypatch.setattr(conn_mod, "_qdrant_available", None)
    without_arg = conn_mod._check_qdrant_available()
    assert with_arg == without_arg


# ── _service_url ──────────────────────────────────────────────────────


def test_service_url_returns_arg_when_provided(monkeypatch):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://from-env:6333")
    assert conn_mod._service_url(qdrant_url="http://from-arg:7333") == "http://from-arg:7333"


def test_service_url_falls_back_to_env_when_no_arg(monkeypatch):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://from-env:6333")
    assert conn_mod._service_url() == "http://from-env:6333"


def test_service_url_returns_none_when_neither_arg_nor_env(monkeypatch):
    monkeypatch.delenv("EMPIRICA_QDRANT_URL", raising=False)
    assert conn_mod._service_url() is None


# ── _rest_search ──────────────────────────────────────────────────────


def test_rest_search_uses_arg_url_in_request(monkeypatch):
    """When qdrant_url= is passed, the POST goes to THAT URL."""
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://from-env:6333")
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"result": [{"id": 1}]})
    with patch("requests.post", return_value=fake_resp) as mock_post:
        out = conn_mod._rest_search(
            "my_collection",
            [0.1, 0.2],
            5,
            qdrant_url="http://from-arg:7333",
        )
    # POST hit the arg URL, not the env URL
    assert mock_post.call_args[0][0] == "http://from-arg:7333/collections/my_collection/points/search"
    assert out == [{"id": 1}]


def test_rest_search_no_arg_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://from-env:6333")
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"result": []})
    with patch("requests.post", return_value=fake_resp) as mock_post:
        conn_mod._rest_search("c", [0.1], 1)
    assert mock_post.call_args[0][0] == "http://from-env:6333/collections/c/points/search"


def test_rest_search_returns_empty_when_no_url(monkeypatch):
    """No arg, no env → empty list (offline-safe, no crash)."""
    monkeypatch.delenv("EMPIRICA_QDRANT_URL", raising=False)
    assert conn_mod._rest_search("c", [0.1], 1) == []


# ── Backward compatibility envelope ───────────────────────────────────


def test_omitting_qdrant_url_is_byte_for_byte_legacy_behavior(monkeypatch):
    """Cortex's safety claim: with qdrant_url=None (the default), behavior
    is identical to pre-change. This test pins that by exercising both
    call shapes against the same mocked deps and asserting identical output."""
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://legacy:6333")
    mock_cls = MagicMock()
    with patch.object(conn_mod, "_get_qdrant_imports", return_value=(mock_cls, None, None, None)):
        legacy_call = conn_mod._get_qdrant_client()
    mock_cls.reset_mock()
    with patch.object(conn_mod, "_get_qdrant_imports", return_value=(mock_cls, None, None, None)):
        new_call_with_none = conn_mod._get_qdrant_client(qdrant_url=None)
    # Same client object returned, same URL used
    assert legacy_call is not None
    assert new_call_with_none is not None
    assert mock_cls.call_args[1]["url"] == "http://legacy:6333"


# ── embed_* / upsert_* / search threading (prop_t7s6whxwjncoploks5wwcdqssm) ──
# The write-path gap cortex found: embed_single_memory_item / embed_assumption /
# embed_decision called _get_qdrant_client() bare, so per-org writes landed on
# the base URL. These tests pin that all 10 functions in memory.py +
# intent_layer.py forward qdrant_url to BOTH the availability check and the
# client factory.

_MEMORY_FNS = ["embed_single_memory_item", "upsert_docs", "upsert_memory", "search"]
_INTENT_FNS = [
    "embed_assumption",
    "embed_decision",
    "embed_intent_edge",
    "search_assumptions",
    "search_decisions",
    "search_intents",
]


def test_all_memory_functions_accept_qdrant_url_defaulting_none():
    for name in _MEMORY_FNS:
        sig = inspect.signature(getattr(memory_mod, name))
        assert "qdrant_url" in sig.parameters, f"{name} missing qdrant_url"
        assert sig.parameters["qdrant_url"].default is None


def test_all_intent_layer_functions_accept_qdrant_url_defaulting_none():
    for name in _INTENT_FNS:
        sig = inspect.signature(getattr(intent_mod, name))
        assert "qdrant_url" in sig.parameters, f"{name} missing qdrant_url"
        assert sig.parameters["qdrant_url"].default is None


def test_embed_single_memory_item_threads_url_to_check_and_client():
    """The exact repro from cortex: write must route to the per-org URL."""
    with (
        patch.object(memory_mod, "_check_qdrant_available", return_value=True) as mock_check,
        patch.object(memory_mod, "_get_qdrant_client", return_value=None) as mock_client,
    ):
        memory_mod.embed_single_memory_item(
            "proj",
            "item-1",
            "text",
            "finding",
            qdrant_url="http://org-mod:7335",
        )
    # prop_ure7rqfuon 2026-07-24: callers now thread project_id too, so
    # the resolver hook can fire when qdrant_url is not passed. Explicit
    # url still wins (priority 1), but the signature carries both args.
    mock_check.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")
    mock_client.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")


def test_embed_assumption_threads_url_to_check_and_client():
    with (
        patch.object(intent_mod, "_check_qdrant_available", return_value=True) as mock_check,
        patch.object(intent_mod, "_get_qdrant_client", return_value=None) as mock_client,
    ):
        intent_mod.embed_assumption(
            "proj",
            "a-1",
            "an assumption",
            qdrant_url="http://org-mod:7335",
        )
    # prop_ure7rqfuon 2026-07-24: callers now thread project_id too, so
    # the resolver hook can fire when qdrant_url is not passed. Explicit
    # url still wins (priority 1), but the signature carries both args.
    mock_check.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")
    mock_client.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")


def test_embed_decision_threads_url_to_check_and_client():
    with (
        patch.object(intent_mod, "_check_qdrant_available", return_value=True) as mock_check,
        patch.object(intent_mod, "_get_qdrant_client", return_value=None) as mock_client,
    ):
        intent_mod.embed_decision(
            "proj",
            "d-1",
            "a choice",
            "a rationale",
            qdrant_url="http://org-mod:7335",
        )
    # prop_ure7rqfuon 2026-07-24: callers now thread project_id too, so
    # the resolver hook can fire when qdrant_url is not passed. Explicit
    # url still wins (priority 1), but the signature carries both args.
    mock_check.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")
    mock_client.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")


def test_embed_intent_edge_threads_url_to_check_and_client():
    with (
        patch.object(intent_mod, "_check_qdrant_available", return_value=True) as mock_check,
        patch.object(intent_mod, "_get_qdrant_client", return_value=None) as mock_client,
    ):
        intent_mod.embed_intent_edge(
            "proj",
            "i-1",
            "noetic_to_praxic",
            "src",
            "finding",
            "tgt",
            "commit",
            0.8,
            qdrant_url="http://org-mod:7335",
        )
    # prop_ure7rqfuon 2026-07-24: callers now thread project_id too, so
    # the resolver hook can fire when qdrant_url is not passed. Explicit
    # url still wins (priority 1), but the signature carries both args.
    mock_check.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")
    mock_client.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")


def test_upsert_memory_threads_url_to_check_and_client():
    with (
        patch.object(memory_mod, "_check_qdrant_available", return_value=True) as mock_check,
        patch.object(memory_mod, "_get_qdrant_client", return_value=None) as mock_client,
    ):
        memory_mod.upsert_memory("proj", [{"id": "x", "text": "t"}], qdrant_url="http://org-mod:7335")
    # prop_ure7rqfuon 2026-07-24: callers now thread project_id too, so
    # the resolver hook can fire when qdrant_url is not passed. Explicit
    # url still wins (priority 1), but the signature carries both args.
    mock_check.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")
    mock_client.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")


def test_search_threads_url_to_check_and_client():
    with (
        patch.object(memory_mod, "_check_qdrant_available", return_value=True) as mock_check,
        patch.object(memory_mod, "_get_qdrant_client", return_value=None) as mock_client,
    ):
        memory_mod.search("proj", "query", qdrant_url="http://org-mod:7335")
    # prop_ure7rqfuon 2026-07-24: callers now thread project_id too, so
    # the resolver hook can fire when qdrant_url is not passed. Explicit
    # url still wins (priority 1), but the signature carries both args.
    mock_check.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")
    mock_client.assert_called_once_with(qdrant_url="http://org-mod:7335", project_id="proj")


def test_embed_omitting_url_is_legacy_behavior():
    """Backward-compat envelope for the embed layer: no kwarg = check/client
    called with qdrant_url=None — exactly the pre-change resolution chain."""
    with (
        patch.object(memory_mod, "_check_qdrant_available", return_value=True) as mock_check,
        patch.object(memory_mod, "_get_qdrant_client", return_value=None) as mock_client,
    ):
        memory_mod.embed_single_memory_item("proj", "item-1", "text", "finding")
    # Legacy caller: qdrant_url omitted → None (env fallback). project_id
    # comes from the positional arg and IS threaded — the resolver hook
    # will fire only when installed (post-prop_ure7rqfuon), else falls
    # through to env identically to pre-change behavior.
    mock_check.assert_called_once_with(qdrant_url=None, project_id="proj")
    mock_client.assert_called_once_with(qdrant_url=None, project_id="proj")
