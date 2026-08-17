"""Cortex-checked engagement write auth (David's option-b ruling, 2026-08-17).

Engagement WRITES over HTTP require a cortex-issued credential verified live
(fail-closed); reads stay on verify_mint_bearer; the emk_ service-token lane
is unchanged. Pins every branch of verify_engagement_write_auth.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import empirica.api.entity_mint_auth as ema


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    monkeypatch.setattr(ema, "_cortex_ok_cache", {})


def _call(authorization):
    # asyncio.run, not get_event_loop(): Python 3.14 raises when no loop exists
    # in the thread, so the old form only passed when ANOTHER suite had already
    # created one — order-dependent green.
    return asyncio.run(ema.verify_engagement_write_auth(authorization=authorization))


def _expect(status, authorization):
    with pytest.raises(HTTPException) as ei:
        _call(authorization)
    assert ei.value.status_code == status


def test_missing_bearer_401():
    _expect(401, None)


def test_emk_service_token_allows_without_cortex(monkeypatch):
    monkeypatch.setenv(ema.ENV_TOKENS, "emk_valid")
    calls = []
    monkeypatch.setattr(ema, "_validate_bearer_with_cortex", lambda *a, **k: calls.append(1) or True)
    _call("Bearer emk_valid")  # must not raise
    assert calls == []  # cortex never consulted for the service lane


def test_valid_cortex_bearer_allows_and_caches(monkeypatch):
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    monkeypatch.setattr(ema, "_cortex_url_from_credentials", lambda: "https://cortex.example")
    calls = []
    monkeypatch.setattr(ema, "_validate_bearer_with_cortex", lambda t, u, **k: calls.append(1) or True)
    _call("Bearer oauth-abc")
    _call("Bearer oauth-abc")  # second call rides the cache
    assert len(calls) == 1


def test_rejected_bearer_401(monkeypatch):
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    monkeypatch.setattr(ema, "_cortex_url_from_credentials", lambda: "https://cortex.example")
    monkeypatch.setattr(ema, "_validate_bearer_with_cortex", lambda *a, **k: False)
    _expect(401, "Bearer bad-token")


def test_cortex_unreachable_503_fail_closed(monkeypatch):
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    monkeypatch.setattr(ema, "_cortex_url_from_credentials", lambda: "https://cortex.example")
    monkeypatch.setattr(ema, "_validate_bearer_with_cortex", lambda *a, **k: None)
    _expect(503, "Bearer whatever")


def test_no_cortex_configured_503_fail_closed(monkeypatch):
    monkeypatch.delenv(ema.ENV_TOKENS, raising=False)
    monkeypatch.setattr(ema, "_cortex_url_from_credentials", lambda: None)
    _expect(503, "Bearer whatever")


def test_route_wiring_writes_gated_reads_not():
    """The three WRITE endpoints carry the cortex-checked dependency; reads
    keep verify_mint_bearer — pinned against the route source so a refactor
    can't silently swap them back."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "empirica" / "api" / "routes" / "engagements.py").read_text()
    assert src.count("Depends(verify_engagement_write_auth)") == 3
    # every write verb is gated
    for anchor in (
        '@router.post("/engagements"',
        '@router.patch("/engagements/{engagement_id}"',
        '@router.post("/engagements/{engagement_id}/sources"',
    ):
        i = src.index(anchor)
        assert "verify_engagement_write_auth" in src[i : i + 120]
