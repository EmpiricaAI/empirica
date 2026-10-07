"""Tests for empirica.core.cockpit.auto_accept.

T11 wires the TUI to cortex's per-user auto-accept-mode toggle. Cortex
ships GET/POST /v1/users/me/auto-accept (Option 1 in their design,
2026-05-15). We mock the HTTP layer so tests run without cortex.

Properties under test:
  - GET parses {enabled: bool}, caches the value for TTL_SEC
  - POST flips state + invalidates cache (next read sees new state)
  - 404 (endpoint not shipped yet) → None, no exception
  - Connection refused / timeout → None
  - Missing creds → None
  - force=True bypasses cache
"""

from __future__ import annotations

import urllib.error

import pytest

from empirica.core.cockpit import auto_accept as aa


@pytest.fixture(autouse=True)
def _reset_module_cache():
    """Tests share the module-level cache — reset between each."""
    aa.reset_cache()
    yield
    aa.reset_cache()


def _mock_creds(monkeypatch, url: str = "https://cortex.test", key: str = "ctx_test") -> None:
    monkeypatch.setattr(aa, "_cortex_creds", lambda: (url, key))


def _mock_creds_missing(monkeypatch) -> None:
    monkeypatch.setattr(aa, "_cortex_creds", lambda: None)


# ── Happy paths ──────────────────────────────────────────────────────────


def test_fetch_returns_true_when_cortex_says_enabled(monkeypatch):
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: {"enabled": True})
    assert aa.fetch_auto_accept_mode() is True


def test_fetch_returns_false_when_cortex_says_disabled(monkeypatch):
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: {"enabled": False})
    assert aa.fetch_auto_accept_mode() is False


def test_set_persists_new_state(monkeypatch):
    _mock_creds(monkeypatch)
    received: list = []

    def fake_request(method, url, key, body=None):
        received.append((method, body))
        return {"enabled": body.get("enabled") if body else False}

    monkeypatch.setattr(aa, "_request", fake_request)
    assert aa.set_auto_accept_mode(True) is True
    assert received[0] == ("POST", {"enabled": True})


def test_set_invalidates_cache_so_next_fetch_sees_new_state(monkeypatch):
    """Cache is bypassed when set fires; subsequent fetch within TTL
    returns the just-written value, not the stale one."""
    _mock_creds(monkeypatch)
    state = {"enabled": False}

    def fake_request(method, url, key, body=None):
        if method == "POST":
            state["enabled"] = body["enabled"]
        return dict(state)

    monkeypatch.setattr(aa, "_request", fake_request)

    assert aa.fetch_auto_accept_mode() is False
    assert aa.set_auto_accept_mode(True) is True
    # Within TTL — must reflect the new state (cache was invalidated)
    assert aa.fetch_auto_accept_mode() is True


# ── Graceful degradation ──────────────────────────────────────────────────


def test_fetch_returns_none_when_creds_missing(monkeypatch):
    """No cortex creds in ~/.empirica/credentials.yaml → state unknown."""
    _mock_creds_missing(monkeypatch)
    assert aa.fetch_auto_accept_mode() is None


def test_fetch_returns_none_on_404_endpoint_not_shipped(monkeypatch):
    """Cortex hasn't shipped /users/me/auto-accept yet → TUI hides chip."""
    _mock_creds(monkeypatch)

    def raises_404(method, url, key, body=None):
        # Simulate the _request internals returning None on HTTPError 404
        return None

    monkeypatch.setattr(aa, "_request", raises_404)
    assert aa.fetch_auto_accept_mode() is None


def test_fetch_returns_none_on_connection_error(monkeypatch):
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda *a, **kw: None)
    assert aa.fetch_auto_accept_mode() is None


def test_set_returns_none_on_failure(monkeypatch):
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda *a, **kw: None)
    assert aa.set_auto_accept_mode(True) is None


# ── Caching ──────────────────────────────────────────────────────────────


def test_fetch_caches_within_ttl(monkeypatch):
    """Second fetch within TTL doesn't hit the network — saves load on
    cortex when the TUI ticks every 5s."""
    _mock_creds(monkeypatch)
    call_count = [0]

    def counting_request(method, url, key, body=None):
        call_count[0] += 1
        return {"enabled": True}

    monkeypatch.setattr(aa, "_request", counting_request)

    aa.fetch_auto_accept_mode()
    aa.fetch_auto_accept_mode()
    aa.fetch_auto_accept_mode()
    assert call_count[0] == 1, "all three fetches should hit cache after the first"


def test_force_bypasses_cache(monkeypatch):
    """When the user just toggled, force=True ensures the next fetch
    actually hits cortex and reflects the new state."""
    _mock_creds(monkeypatch)
    call_count = [0]

    def counting_request(method, url, key, body=None):
        call_count[0] += 1
        return {"enabled": True}

    monkeypatch.setattr(aa, "_request", counting_request)

    aa.fetch_auto_accept_mode()
    aa.fetch_auto_accept_mode(force=True)
    assert call_count[0] == 2


# ── HTTP layer (the actual urllib code path) ─────────────────────────────


def test_request_handles_404_returns_none(monkeypatch):
    """The _request helper turns urllib.error.HTTPError 404 into None
    (rather than raising). Caller treats None as 'unavailable'."""

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(aa.urllib.request, "urlopen", fake_urlopen)
    result = aa._request("GET", "https://cortex.test/v1/users/me/auto-accept", "k")
    assert result is None


def test_request_handles_url_error_returns_none(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(aa.urllib.request, "urlopen", fake_urlopen)
    result = aa._request("GET", "https://cortex.test/v1/users/me/auto-accept", "k")
    assert result is None


# ── Invalid responses (enabled is not a bool) ────────────────────────────


def test_fetch_returns_none_when_enabled_is_string(monkeypatch):
    """Auto-accept reply without boolean 'enabled' is treated as unknown."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: {"enabled": "true"})
    assert aa.fetch_auto_accept_mode() is None


def test_fetch_returns_none_when_enabled_is_int(monkeypatch):
    """Auto-accept reply with int 'enabled' is treated as unknown."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: {"enabled": 1})
    assert aa.fetch_auto_accept_mode() is None


def test_fetch_returns_none_when_enabled_is_missing(monkeypatch):
    """Auto-accept reply without 'enabled' key is treated as unknown."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: {})
    assert aa.fetch_auto_accept_mode() is None


def test_fetch_returns_none_when_body_is_not_dict(monkeypatch):
    """Auto-accept reply that isn't a dict is treated as unknown."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: "not a dict")
    assert aa.fetch_auto_accept_mode() is None


def test_fetch_does_not_cache_invalid_response(monkeypatch):
    """Invalid response is not cached, so second fetch hits network again."""
    _mock_creds(monkeypatch)
    call_count = [0]

    def counting_request(method, url, key, body=None):
        call_count[0] += 1
        return {"enabled": "invalid"}

    monkeypatch.setattr(aa, "_request", counting_request)

    result1 = aa.fetch_auto_accept_mode()
    assert result1 is None
    assert call_count[0] == 1

    result2 = aa.fetch_auto_accept_mode()
    assert result2 is None
    assert call_count[0] == 2, "cache should not have been set, so second fetch should hit network"


def test_set_returns_none_when_enabled_is_string(monkeypatch):
    """Set should return None if server reply doesn't have boolean enabled."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: {"enabled": "true"})
    assert aa.set_auto_accept_mode(True) is None


def test_set_returns_none_when_enabled_is_missing(monkeypatch):
    """Set should return None if server reply missing enabled key."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: {})
    assert aa.set_auto_accept_mode(True) is None


def test_set_does_not_echo_requested_value(monkeypatch):
    """Set should not echo the requested value back when server reply is invalid."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(aa, "_request", lambda method, url, key, body=None: None)
    result = aa.set_auto_accept_mode(True)
    assert result is None, "should return None on server error, not echo True"


def test_set_does_not_cache_invalid_response(monkeypatch):
    """Invalid response from set is not cached."""
    _mock_creds(monkeypatch)

    def fake_request(method, url, key, body=None):
        return {"enabled": 1}  # invalid: int instead of bool

    monkeypatch.setattr(aa, "_request", fake_request)

    result = aa.set_auto_accept_mode(True)
    assert result is None

    # Cache should be empty, so fetch sees the invalid response too
    result2 = aa.fetch_auto_accept_mode()
    assert result2 is None
