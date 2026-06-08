"""Tests for the mesh status health computation + curl-pid matching.

Surfaced by mesh-support prop_po4nyp3xzb: every cortex instance showed
curl=dead/health_color=red even while fires were actively flowing. Two
related bugs:

1. `_find_listener_pids` used `tags={ai_id}` (basename) as the curl
   needle, but post-3-form migration the listener subscribes with the
   canonical tag `tags=<org>.<tenant>.<ai_id>` — so curl_subprocess_pid
   was None for every instance.

2. `_compute_health` checked curl_subprocess_pid BEFORE the last_fire
   idle check — so even with fires flowing the fire-flow path was
   structurally unreachable.

Fix: match curl by either legacy basename needle OR canonical 3-form
suffix, with a delimiter check to prevent `tags=empirica` falsely
matching a cmdline carrying `tags=empirica-cortex`. Plus reorder
_compute_health so recent fires return green regardless of curl-pid.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from empirica.cli.command_handlers.mesh_commands import (
    ZOMBIE_THRESHOLD_SECONDS,
    _compute_health,
    _tag_matches,
)

# --- _tag_matches: delimiter-aware substring check ---


def test_tag_matches_exact_at_end():
    assert _tag_matches("curl -s ?tags=cortex", "tags=cortex") is True


def test_tag_matches_followed_by_ampersand():
    assert _tag_matches("?tags=cortex&since=...", "tags=cortex") is True


def test_tag_matches_followed_by_quote():
    assert _tag_matches('curl "?tags=cortex"', "tags=cortex") is True


def test_tag_matches_rejects_prefix_overlap():
    """tags=empirica must NOT match a cmdline carrying tags=empirica-cortex."""
    assert _tag_matches("?tags=empirica-cortex", "tags=empirica") is False


def test_tag_matches_dot_suffix_canonical():
    """The canonical-suffix match: cmdline carries the 3-form tag."""
    assert _tag_matches(
        "?tags=empirica.david.empirica-cortex", ".empirica-cortex"
    ) is True


def test_tag_matches_dot_suffix_rejects_prefix_overlap():
    """`.cortex` must NOT match `.empirica-cortex`."""
    assert _tag_matches("?tags=empirica.david.empirica-cortex", ".cortex") is False


def test_tag_matches_needle_absent_returns_false():
    assert _tag_matches("curl https://example.com", "tags=cortex") is False


# --- _compute_health: fire-flow is authoritative liveness signal ---


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _state(
    *,
    service_installed: bool = True,
    service_active: bool = True,
    listener_pid: int | None = 1234,
    curl_pid: int | None = 5678,
    last_fire: datetime | None = None,
    backoff_state: str | None = None,
) -> dict:
    return {
        "ai_id": "cortex",
        "service_installed": service_installed,
        "service_active": service_active,
        "listener_process_pid": listener_pid,
        "curl_subprocess_pid": curl_pid,
        "last_fire_at_utc": last_fire,
        "backoff_state": backoff_state,
    }


def test_recent_fires_with_curl_pid_missing_returns_green():
    """The core regression: pre-1.11.8 this was red ('curl subscription
    dead') even though fires were flowing. Fire-flow now wins.
    """
    recent = _now_utc() - timedelta(seconds=30)
    s = _state(curl_pid=None, last_fire=recent)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "green"
    assert "last fire" in msg


def test_recent_fires_with_curl_pid_present_returns_green():
    recent = _now_utc() - timedelta(seconds=120)
    s = _state(curl_pid=5678, last_fire=recent)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "green"


def test_silent_fires_with_curl_dead_returns_red():
    """When fires HAVE gone silent past zombie threshold AND curl is
    not detected, red is the right call."""
    silent = _now_utc() - timedelta(seconds=ZOMBIE_THRESHOLD_SECONDS + 60)
    s = _state(curl_pid=None, last_fire=silent)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "red"
    assert "curl subscription dead" in msg


def test_silent_fires_with_curl_alive_returns_red_zombie():
    """Curl alive but fires silent = real zombie."""
    silent = _now_utc() - timedelta(seconds=ZOMBIE_THRESHOLD_SECONDS + 60)
    s = _state(curl_pid=5678, last_fire=silent)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "red"
    assert "zombie" in msg


def test_no_fires_yet_with_curl_alive_returns_yellow():
    s = _state(curl_pid=5678, last_fire=None)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "yellow"
    assert "no fires recorded" in msg or "cold start" in msg


def test_no_fires_yet_with_curl_dead_returns_red():
    """Cold start + curl can't spawn = real outage."""
    s = _state(curl_pid=None, last_fire=None)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "red"
    assert "curl subscription dead" in msg


def test_rate_limit_backoff_with_no_fires_returns_yellow():
    s = _state(curl_pid=None, last_fire=None, backoff_state="rate_limit")
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "yellow"
    assert "rate" in msg.lower()


def test_service_not_installed_returns_yellow():
    s = _state(service_installed=False)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "yellow"


# --- Watchdog cross-reference with listener_health_<ai_id>.json ---
#
# Symptom (autonomy practitioner, 2026-06-08): mesh status flagged
# canonical empirica-autonomy as "zombie suspected: no fires in 44min"
# while the listener was actually alive + subscribed, just genuinely
# idle. The fire-flow heuristic alone can't tell quiet-but-healthy from
# dead. Fix: cross-reference the positive-liveness health marker the
# listener writes on every successful poll cycle.


def _write_health_marker(home: Path, ai_id: str, *, status: str, age_seconds: float):
    """Write listener_health_<ai_id>.json with the given staleness."""
    import json as _json
    health_file = home / ".empirica" / f"listener_health_{ai_id}.json"
    health_file.parent.mkdir(parents=True, exist_ok=True)
    ts = _now_utc() - timedelta(seconds=age_seconds)
    health_file.write_text(_json.dumps({
        "instance_id": ai_id, "loop": "cortex-mailbox-poll",
        "status": status, "ts": ts.isoformat(),
    }))


def test_silent_fires_with_fresh_health_marker_returns_green(monkeypatch, tmp_path):
    """The autonomy practitioner's exact case: curl alive, no fires in
    > zombie threshold, but listener health marker is fresh (status=ok,
    ts within HEALTH_MARKER_FRESH_SECONDS) — listener is quiet but
    healthy, not zombie. Pre-fix this returned red; post-fix green.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_health_marker(tmp_path, "cortex", status="ok", age_seconds=60)
    silent = _now_utc() - timedelta(seconds=ZOMBIE_THRESHOLD_SECONDS + 600)
    s = _state(curl_pid=5678, last_fire=silent)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "green"
    assert "quiet but healthy" in msg
    assert "44m" in msg or "40m" in msg


def test_silent_fires_with_stale_health_marker_still_red_zombie(monkeypatch, tmp_path):
    """Health marker is too old (older than HEALTH_MARKER_FRESH_SECONDS)
    — falls back to the existing zombie-suspected flag. The listener
    health signal has to be FRESH to override fire-flow silence."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_health_marker(tmp_path, "cortex", status="ok", age_seconds=3600)  # 1hr stale
    silent = _now_utc() - timedelta(seconds=ZOMBIE_THRESHOLD_SECONDS + 60)
    s = _state(curl_pid=5678, last_fire=silent)
    color, msg = _compute_health(s, cortex_configured=True)
    assert color == "red"
    assert "zombie" in msg


def test_silent_fires_with_degraded_health_marker_still_red_zombie(monkeypatch, tmp_path):
    """status=degraded marker (listener explicitly self-reports unhealthy)
    must NOT shield the zombie flag — that would mask a real problem."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_health_marker(tmp_path, "cortex", status="degraded", age_seconds=60)
    silent = _now_utc() - timedelta(seconds=ZOMBIE_THRESHOLD_SECONDS + 60)
    s = _state(curl_pid=5678, last_fire=silent)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "red"


def test_silent_fires_with_no_health_marker_still_red_zombie(monkeypatch, tmp_path):
    """No marker on disk → fall back to the existing heuristic. Preserves
    pre-fix behavior for instances that haven't written a marker yet."""
    monkeypatch.setenv("HOME", str(tmp_path))
    silent = _now_utc() - timedelta(seconds=ZOMBIE_THRESHOLD_SECONDS + 60)
    s = _state(curl_pid=5678, last_fire=silent)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "red"


def test_health_freshness_helper_handles_malformed_marker(monkeypatch, tmp_path):
    """Helper must not crash on bad JSON / missing fields / bad ts."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from empirica.cli.command_handlers.mesh_commands import (
        _listener_health_freshness,
    )
    (tmp_path / ".empirica").mkdir()
    # Bad JSON
    (tmp_path / ".empirica" / "listener_health_a.json").write_text("not json {")
    assert _listener_health_freshness("a") is None
    # Missing ts
    (tmp_path / ".empirica" / "listener_health_b.json").write_text('{"status":"ok"}')
    assert _listener_health_freshness("b") is None
    # Bad ts
    (tmp_path / ".empirica" / "listener_health_c.json").write_text(
        '{"status":"ok","ts":"not a timestamp"}'
    )
    assert _listener_health_freshness("c") is None
    # No file at all
    assert _listener_health_freshness("nonexistent") is None


def test_service_inactive_returns_red():
    s = _state(service_active=False)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "red"


def test_listener_pid_missing_returns_red():
    s = _state(listener_pid=None)
    color, _ = _compute_health(s, cortex_configured=True)
    assert color == "red"


def test_local_only_returns_green_when_cortex_unconfigured():
    """No cortex configured → curl checks don't apply."""
    s = _state(curl_pid=None, last_fire=None)
    color, msg = _compute_health(s, cortex_configured=False)
    assert color == "green"
    assert "local-only" in msg
