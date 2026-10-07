"""Tests for fail-soft behavior on invalid notification-channels responses.

Verifies that non-JSON and non-dict bodies from cortex don't crash with
tracebacks but instead cleanly raise RuntimeError from the resolver.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from empirica.core.cockpit import notification_channels as nc


@pytest.fixture(autouse=True)
def _reset_module_cache():  # pyright: ignore[reportUnusedFunction]
    nc.reset_cache()
    yield
    nc.reset_cache()


def _mock_creds(monkeypatch, url: str = "https://cortex.test", key: str = "ctx_test") -> None:
    monkeypatch.setattr(nc, "_cortex_creds", lambda: (url, key))


def test_request_returns_none_on_invalid_json(monkeypatch):
    """Invalid JSON from response should log debug and return None, not crash."""
    _mock_creds(monkeypatch)

    def mock_urlopen(*args, **kwargs):
        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=None)
        mock_resp.read = Mock(return_value=b"not valid json {")
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    result = nc._request("https://cortex.test/v1/users/me/notification-channels", "key")
    assert result is None


def test_request_returns_none_on_utf8_decode_error(monkeypatch):
    """Invalid UTF-8 from response should log debug and return None, not crash."""
    _mock_creds(monkeypatch)

    def mock_urlopen(*args, **kwargs):
        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=None)
        # Invalid UTF-8 sequence
        mock_resp.read = Mock(return_value=b"\xff\xfe")
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    result = nc._request("https://cortex.test/v1/users/me/notification-channels", "key")
    assert result is None


def test_fetch_returns_none_on_non_dict_body(monkeypatch):
    """Non-dict parsed body (e.g., list, string) should return None, not crash."""
    _mock_creds(monkeypatch)
    # Mock _request to return a list instead of dict
    monkeypatch.setattr(nc, "_request", lambda url, key: ["item1", "item2"])
    result = nc.fetch_notification_channels()
    assert result is None


def test_fetch_returns_none_on_null_body(monkeypatch):
    """Null/None body should return None."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(nc, "_request", lambda url, key: None)
    result = nc.fetch_notification_channels()
    assert result is None


def test_resolver_raises_clean_error_on_invalid_json(monkeypatch):
    """Invalid JSON response should result in clean RuntimeError from resolver,
    not a JSON decode traceback."""
    _mock_creds(monkeypatch)

    def mock_urlopen(*args, **kwargs):
        mock_resp = Mock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=None)
        mock_resp.read = Mock(return_value=b"invalid json {")
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    with pytest.raises(RuntimeError, match="orchestration-events"):
        nc.resolve_orchestration_events_topic("empirica")


def test_resolver_raises_clean_error_on_non_dict_body(monkeypatch):
    """Non-dict response should result in clean RuntimeError from resolver."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(nc, "_request", lambda url, key: [])

    with pytest.raises(RuntimeError, match="orchestration-events"):
        nc.resolve_orchestration_events_topic("empirica")


def test_fetch_caches_valid_dict(monkeypatch):
    """Verify that valid dicts are still cached normally."""
    _mock_creds(monkeypatch)
    calls = {"n": 0}

    def fake_request(url, key):
        calls["n"] += 1
        return {"channels": []}

    monkeypatch.setattr(nc, "_request", fake_request)
    nc.fetch_notification_channels()
    nc.fetch_notification_channels()
    assert calls["n"] == 1, "valid dict should be cached"
