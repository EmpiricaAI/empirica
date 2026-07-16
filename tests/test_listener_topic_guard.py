"""Listener never falls back to the retired bare `orchestration-events` ntfy
topic (autonomy prop_6v3v4iob — the 403-storm that wedged 12-13/15 fleet seats).

Covers the run_listener topic-resolution guard + the last-good-topic cache:
  - resolved topic is used AND persisted
  - unresolved + cache present → uses the cached (valid) topic
  - unresolved + no cache + default is the retired bare topic → REFUSES (rc=2)
  - ORCHESTRATION_NTFY_TOPIC override bypasses resolution
  - the cache helpers never surface the bare topic
"""

from __future__ import annotations

import io

from empirica.core.loop_scheduler import listener as listener_mod
from empirica.core.loop_scheduler.listener import (
    _RETIRED_BARE_TOPIC,
    ListenerStopped,
    _last_good_topic_path,
    _persist_last_good_topic,
    _read_last_good_topic,
    run_listener,
)

_NTFY = {"url": "https://ntfy.test", "topic": _RETIRED_BARE_TOPIC, "user": "u", "password": "p"}
_GOOD = "empirica-orchestration-events"


def _mock_ntfy(monkeypatch, topic=_RETIRED_BARE_TOPIC):
    from empirica.config.credentials_loader import get_credentials_loader

    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {**_NTFY, "topic": topic})


def _mock_channels(monkeypatch, body):
    """Force notification_channels.fetch to return `body` (bypasses its cache)."""
    import empirica.core.cockpit.notification_channels as nc

    monkeypatch.setattr(nc, "fetch_notification_channels", lambda *a, **k: body)


# ── cache helpers ──────────────────────────────────────────────────────────


def test_persist_and_read_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _persist_last_good_topic("cortex", _GOOD)
    assert _last_good_topic_path("cortex").read_text().strip() == _GOOD
    assert _read_last_good_topic("cortex") == _GOOD


def test_persist_refuses_bare_topic(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _persist_last_good_topic("cortex", _RETIRED_BARE_TOPIC)
    assert not _last_good_topic_path("cortex").exists()


def test_read_refuses_stale_bare_topic(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = _last_good_topic_path("cortex")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_RETIRED_BARE_TOPIC)  # hand-edited / stale
    assert _read_last_good_topic("cortex") is None


def test_read_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _read_last_good_topic("cortex") is None


# ── run_listener resolution guard ──────────────────────────────────────────


def test_refuses_retired_bare_topic_when_unresolved_no_cache(monkeypatch, tmp_path):
    """The core fleet-safety property: cortex unresolved + no cache + the only
    candidate is the retired bare topic → refuse (rc=2), never 403-subscribe."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _mock_ntfy(monkeypatch)
    _mock_channels(monkeypatch, None)  # unresolved
    err = io.StringIO()
    rc = run_listener(
        "cortex",
        output_stream=io.StringIO(),
        err_stream=err,
        _sleep=lambda *a: None,
        _initial_catchup=False,
    )
    assert rc == 2
    assert "REFUSING" in err.getvalue()
    assert _RETIRED_BARE_TOPIC in err.getvalue()


def test_uses_last_good_cache_when_unresolved(monkeypatch, tmp_path):
    """Unresolved but a prior good topic is cached → subscribe to the cache,
    not the bare topic."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _persist_last_good_topic("cortex", _GOOD)  # prior good resolution
    _mock_ntfy(monkeypatch)
    _mock_channels(monkeypatch, None)  # now unresolved
    monkeypatch.setattr(listener_mod, "_emit_catchup_events", lambda *a, **k: 0)

    def factory_that_dies(url, headers):
        raise ListenerStopped("stop after resolve")

    err = io.StringIO()
    rc = run_listener(
        "cortex", output_stream=io.StringIO(), err_stream=err, _stream_factory=factory_that_dies, _sleep=lambda *a: None
    )
    assert rc == 0
    out = err.getvalue()
    assert "last-resolved-good" in out
    assert _GOOD in out  # the subscribe URL logs the chosen topic
    assert f"/{_RETIRED_BARE_TOPIC}/" not in out  # never the bare topic


def test_resolved_topic_is_used_and_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _mock_ntfy(monkeypatch)
    _mock_channels(monkeypatch, {"channels": [{"topic": _GOOD}]})  # resolves to prefixed
    monkeypatch.setattr(listener_mod, "_emit_catchup_events", lambda *a, **k: 0)

    def factory_that_dies(url, headers):
        raise ListenerStopped("stop after resolve")

    err = io.StringIO()
    rc = run_listener(
        "cortex", output_stream=io.StringIO(), err_stream=err, _stream_factory=factory_that_dies, _sleep=lambda *a: None
    )
    assert rc == 0
    assert _GOOD in err.getvalue()
    # good resolution is cached for the next cortex-unreachable start
    assert _read_last_good_topic("cortex") == _GOOD


def test_env_override_bypasses_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATION_NTFY_TOPIC", "custom-debug-topic")
    _mock_ntfy(monkeypatch, topic="custom-debug-topic")
    monkeypatch.setattr(listener_mod, "_emit_catchup_events", lambda *a, **k: 0)

    def factory_that_dies(url, headers):
        raise ListenerStopped("stop after resolve")

    err = io.StringIO()
    rc = run_listener(
        "cortex", output_stream=io.StringIO(), err_stream=err, _stream_factory=factory_that_dies, _sleep=lambda *a: None
    )
    assert rc == 0
    assert "custom-debug-topic" in err.getvalue()


# ── retired_channels (cortex-authoritative retired-topic list) ──────────────


def test_retired_topic_names_extracts_names():
    from empirica.core.cockpit.notification_channels import retired_topic_names

    body = {
        "retired_channels": [
            {"name": "orchestration-events", "retired_at": "x", "migration_hint": "y"},
            {"name": "old-eco-topic"},
            {"retired_at": "z"},  # no name → skipped
        ]
    }
    assert retired_topic_names(body) == {"orchestration-events", "old-eco-topic"}
    assert retired_topic_names(None) == set()  # cortex unreachable
    assert retired_topic_names({}) == set()  # older cortex, field absent


def test_cached_topic_rejected_when_since_retired(monkeypatch, tmp_path):
    """Cache invalidation: a previously-good topic that cortex has since retired
    is rejected via retired_channels — the listener refuses rather than 403."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _persist_last_good_topic("cortex", _GOOD)  # was good
    _mock_ntfy(monkeypatch)  # ntfy default = bare retired topic
    # cortex reachable, nothing resolvable, and retired_channels now lists the
    # previously-cached topic
    _mock_channels(monkeypatch, {"channels": [], "retired_channels": [{"name": _GOOD}]})
    err = io.StringIO()
    rc = run_listener(
        "cortex", output_stream=io.StringIO(), err_stream=err, _sleep=lambda *a: None, _initial_catchup=False
    )
    assert rc == 2  # cache rejected (retired) + bare default retired → refuse
    assert "REFUSING" in err.getvalue()
