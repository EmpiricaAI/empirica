"""Tests for empirica.core.cockpit.notification_channels.

ECO_COLLAB_RESCOPE Phase 4: listener discovers per-org-prefixed ntfy
topics from cortex's /v1/users/me/notification-channels endpoint instead
of hardcoding the bare `orchestration-events` topic. Cortex prop_oe7jz5.

Properties under test:
  - fetch_notification_channels parses cortex JSON + caches per TTL
  - resolve_orchestration_events_topic returns per-org-prefixed topic on success
  - resolve_orchestration_events_topic returns legacy bare topic on failure
  - Channel matching prefers kind='orchestration_events', falls back to
    substring scan on topic name
  - Cache invalidation via force=True
  - Missing creds / 404 / connection error → resolver returns bare fallback
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit import notification_channels as nc


@pytest.fixture(autouse=True)
def _reset_module_cache():  # pyright: ignore[reportUnusedFunction]
    nc.reset_cache()
    yield
    nc.reset_cache()


def _mock_creds(monkeypatch, url: str = "https://cortex.test", key: str = "ctx_test") -> None:
    monkeypatch.setattr(nc, "_cortex_creds", lambda: (url, key))


def _mock_creds_missing(monkeypatch) -> None:
    monkeypatch.setattr(nc, "_cortex_creds", lambda: None)


# ── fetch_notification_channels ──────────────────────────────────────────


def test_fetch_parses_channels_body(monkeypatch):
    _mock_creds(monkeypatch)
    body = {
        "channels": [
            {"topic": "empirica-orchestration-events", "kind": "orchestration_events"},
            {"topic": "empirica-system", "kind": "system"},
        ],
        "system_topic": "empirica-system",
    }
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    assert nc.fetch_notification_channels() == body


def test_fetch_caches_within_ttl(monkeypatch):
    _mock_creds(monkeypatch)
    calls = {"n": 0}

    def fake_request(url, key):
        calls["n"] += 1
        return {"channels": [], "system_topic": "x"}

    monkeypatch.setattr(nc, "_request", fake_request)
    nc.fetch_notification_channels()
    nc.fetch_notification_channels()
    nc.fetch_notification_channels()
    assert calls["n"] == 1, "cached after first fetch within TTL"


def test_force_bypasses_cache(monkeypatch):
    _mock_creds(monkeypatch)
    calls = {"n": 0}

    def fake_request(url, key):
        calls["n"] += 1
        return {"channels": []}

    monkeypatch.setattr(nc, "_request", fake_request)
    nc.fetch_notification_channels()
    nc.fetch_notification_channels(force=True)
    assert calls["n"] == 2


def test_fetch_returns_none_on_missing_creds(monkeypatch):
    _mock_creds_missing(monkeypatch)
    assert nc.fetch_notification_channels() is None


def test_fetch_returns_none_on_request_failure(monkeypatch):
    _mock_creds(monkeypatch)
    monkeypatch.setattr(nc, "_request", lambda url, key: None)
    assert nc.fetch_notification_channels() is None


# ── resolve_orchestration_events_topic ───────────────────────────────────


def test_resolver_uses_per_org_topic_when_cortex_responds(monkeypatch):
    _mock_creds(monkeypatch)
    body = {
        "channels": [
            {"topic": "empirica-orchestration-events", "kind": "orchestration_events"},
        ],
    }
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    topic = nc.resolve_orchestration_events_topic("empirica")
    assert topic == "ntfy:empirica-orchestration-events?tags=empirica"


def test_resolver_matches_by_kind(monkeypatch):
    """When multiple channels exist, kind='orchestration_events' wins."""
    _mock_creds(monkeypatch)
    body = {
        "channels": [
            {"topic": "empirica-system", "kind": "system"},
            {"topic": "empirica-publish", "kind": "publish"},
            {"topic": "myorg-events", "kind": "orchestration_events"},
        ],
    }
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    topic = nc.resolve_orchestration_events_topic("cortex")
    assert topic == "ntfy:myorg-events?tags=cortex"


def test_resolver_falls_back_to_substring_when_kind_missing(monkeypatch):
    """Older cortex deploys may not set kind — match on topic substring."""
    _mock_creds(monkeypatch)
    body = {
        "channels": [
            {"topic": "myorg-orchestration-events"},  # no kind field
        ],
    }
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    topic = nc.resolve_orchestration_events_topic("extension")
    assert topic == "ntfy:myorg-orchestration-events?tags=extension"


def test_resolver_raises_on_cortex_unreachable(monkeypatch):
    """Cortex unreachable → RAISE, never the dead bare topic (no ACL → 403)."""
    _mock_creds(monkeypatch)
    monkeypatch.setattr(nc, "_request", lambda url, key: None)
    with pytest.raises(RuntimeError, match="orchestration-events"):
        nc.resolve_orchestration_events_topic("empirica")


def test_resolver_raises_on_missing_creds(monkeypatch):
    """No cortex creds → RAISE rather than silently subscribe to bare."""
    _mock_creds_missing(monkeypatch)
    with pytest.raises(RuntimeError, match="orchestration-events"):
        nc.resolve_orchestration_events_topic("empirica")


def test_resolver_derives_prefix_when_no_explicit_channel(monkeypatch):
    """No orchestration-events channel, but >=2 org-prefixed siblings →
    derive the org prefix and build `<org>-orchestration-events`.

    This is the real cortex payload shape: channels keyed by `category`
    (eco/collab/system/publish/roster), none named orchestration-events."""
    _mock_creds(monkeypatch)
    body = {
        "channels": [
            {"topic": "empirica-eco-david", "category": "eco"},
            {"topic": "empirica-collab-david", "category": "collab"},
            {"topic": "empirica-system", "category": "system"},
            {"topic": "empirica-publish", "category": "publish"},
        ],
    }
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    topic = nc.resolve_orchestration_events_topic("empirica")
    assert topic == "ntfy:empirica-orchestration-events?tags=empirica"


def test_resolver_raises_when_no_prefixable_channels(monkeypatch):
    """Cortex responds but nothing prefix-derivable (single channel) →
    RAISE, not bare fallback."""
    _mock_creds(monkeypatch)
    body = {"channels": [{"topic": "empirica-system", "category": "system"}]}
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    with pytest.raises(RuntimeError, match="orchestration-events"):
        nc.resolve_orchestration_events_topic("empirica")


def test_resolver_appends_tags_filter_per_ai(monkeypatch):
    """Different ai_ids get different ?tags= suffixes off the same base."""
    _mock_creds(monkeypatch)
    body = {"channels": [{"topic": "shared-events", "kind": "orchestration_events"}]}
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    assert nc.resolve_orchestration_events_topic("empirica") == "ntfy:shared-events?tags=empirica"
    nc.reset_cache()
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    assert nc.resolve_orchestration_events_topic("cortex") == "ntfy:shared-events?tags=cortex"


def test_resolver_skips_channels_with_no_topic(monkeypatch):
    """Defensive: missing/null topic on a channel doesn't blow up."""
    _mock_creds(monkeypatch)
    body = {
        "channels": [
            {"kind": "orchestration_events"},  # no topic — skip
            {"topic": "valid-orchestration-events", "kind": "orchestration_events"},
        ],
    }
    monkeypatch.setattr(nc, "_request", lambda url, key: body)
    topic = nc.resolve_orchestration_events_topic("empirica")
    assert topic == "ntfy:valid-orchestration-events?tags=empirica"
