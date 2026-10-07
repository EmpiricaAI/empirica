"""Tests for _resolve_base_topic excluding bare and retired orchestration-events topics."""

from __future__ import annotations

from empirica.core.cockpit import notification_channels as nc


def test_derive_org_prefix_basic():
    """Verify _derive_org_prefix works with basic inputs."""
    result = nc._derive_org_prefix(["empirica-eco", "empirica-system"])
    assert result == "empirica-"


def test_derive_org_prefix_mod():
    """Verify _derive_org_prefix works with mod prefix."""
    result = nc._derive_org_prefix(["mod-system", "mod-eco"])
    assert result == "mod-"


def test_resolve_base_topic_skips_bare_orchestration_events():
    """Explicit bare orchestration-events (no org prefix) should be skipped."""
    body = {
        "channels": [
            {"topic": "orchestration-events", "kind": "orchestration_events"},  # bare, should skip
            {"topic": "empirica-system", "kind": "system"},
            {"topic": "empirica-eco", "kind": "eco"},
        ]
    }
    # Should derive from sibling channels instead of using bare
    result = nc._resolve_base_topic(body)
    assert result == "empirica-orchestration-events"


def test_resolve_base_topic_skips_retired_orchestration_events():
    """Retired orchestration-events topic should be skipped."""
    body = {
        "channels": [
            {"topic": "old-empirica-orchestration-events", "kind": "orchestration_events"},  # retired
            {"topic": "empirica-system", "kind": "system"},
            {"topic": "empirica-eco", "kind": "eco"},
        ],
        "retired_channels": [
            {"name": "old-empirica-orchestration-events"},
        ],
    }
    # Should derive from sibling channels instead of using retired explicit topic
    result = nc._resolve_base_topic(body)
    assert result == "empirica-orchestration-events"


def test_resolve_base_topic_uses_non_retired_explicit():
    """Non-retired explicit orchestration-events topic should be returned."""
    body = {
        "channels": [
            {"topic": "empirica-orchestration-events", "kind": "orchestration_events"},
            {"topic": "empirica-system", "kind": "system"},
        ],
        "retired_channels": [],
    }
    result = nc._resolve_base_topic(body)
    assert result == "empirica-orchestration-events"


def test_resolve_base_topic_excludes_retired_from_prefix_derivation():
    """Retired topics should not be considered when deriving org prefix."""
    body = {
        "channels": [
            {"topic": "old-org-system"},  # this is retired
            {"topic": "empirica-system"},
            {"topic": "empirica-eco"},
        ],
        "retired_channels": [
            {"name": "old-org-system"},
        ],
    }
    # Should derive prefix from empirica-* topics, not old-org-*
    result = nc._resolve_base_topic(body)
    assert result == "empirica-orchestration-events"


def test_resolve_base_topic_skips_bare_and_derives_from_valid_topics():
    """Both bare and prefixed topics present; bare skipped, derived from prefixed."""
    body = {
        "channels": [
            {"topic": "orchestration-events", "kind": "orchestration_events"},  # bare, skip
            {"topic": "mod-system", "kind": "system"},
            {"topic": "mod-eco", "kind": "eco"},
            {"topic": "mod-collab", "kind": "collab"},
        ]
    }
    result = nc._resolve_base_topic(body)
    assert result == "mod-orchestration-events"


def test_resolve_base_topic_returns_none_when_only_bare_or_retired():
    """No valid topic found when only bare/retired available and no derivable prefix."""
    body = {
        "channels": [
            {"topic": "orchestration-events", "kind": "orchestration_events"},  # bare
        ],
        "retired_channels": [],
    }
    result = nc._resolve_base_topic(body)
    # Can't derive prefix from just one topic, and bare is skipped
    assert result is None


def test_resolve_base_topic_with_topic_hint_in_name():
    """Topic with orchestration-events hint but prefixed (not bare) should be used."""
    body = {
        "channels": [
            {"topic": "mod-orchestration-events"},  # has hint but is prefixed
            {"topic": "mod-system"},
        ]
    }
    result = nc._resolve_base_topic(body)
    assert result == "mod-orchestration-events"


def test_resolve_base_topic_skips_retired_topic_by_topic_hint():
    """Retired topic containing orchestration-events hint should be skipped."""
    body = {
        "channels": [
            {"topic": "old-orchestration-events"},  # hint but retired
            {"topic": "empirica-system"},
            {"topic": "empirica-eco"},
        ],
        "retired_channels": [
            {"name": "old-orchestration-events"},
        ],
    }
    result = nc._resolve_base_topic(body)
    # Should derive instead of using retired
    assert result == "empirica-orchestration-events"
