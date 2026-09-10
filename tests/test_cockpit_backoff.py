"""Tests for loop backoff (PROPOSAL_LOOP_BACKOFF.md).

Covers: backoff envelope storage, empty-fire streak advance, found/fail
reset, exponential math (15m → 30m → 1h → 2h → 4h cap), should-fire
gate, poke clear, backwards-compat (loops without policy default to none).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from empirica.core.cockpit import loop_registry as lr


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / ".empirica"
    fake.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lr, "EMPIRICA_DIR", fake)
    return fake


# ─── duration parsing ─────────────────────────────────────────────────────


def test_parse_duration_units():
    assert lr.parse_duration("30s") == 30
    assert lr.parse_duration("15m") == 900
    assert lr.parse_duration("4h") == 14400
    assert lr.parse_duration("1d") == 86400
    # Bare integer = minutes
    assert lr.parse_duration("5") == 300
    # Garbage
    assert lr.parse_duration("") is None
    assert lr.parse_duration(None) is None
    assert lr.parse_duration("xyz") is None


def test_format_duration_inverse():
    assert lr.format_duration(900) == "15m"
    assert lr.format_duration(3600) == "1h"
    assert lr.format_duration(14400) == "4h"
    assert lr.format_duration(86400) == "1d"
    assert lr.format_duration(45) == "45s"


# ─── envelope storage ─────────────────────────────────────────────────────


def test_register_with_backoff_stores_envelope(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    entry = reg.register(
        name="poll",
        kind="cron",
        cron="*/15 * * * *",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    assert entry.backoff.policy == "exponential"
    assert entry.backoff.base_interval_seconds == 900
    assert entry.backoff.max_interval_seconds == 14400
    assert entry.backoff.empty_streak == 0
    assert entry.backoff.next_fire_threshold is None


def test_register_without_backoff_defaults_to_none(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    entry = reg.register(name="poll", kind="cron", cron="*/15 * * * *")
    assert entry.backoff.policy == "none"
    assert entry.backoff.base_interval_seconds is None


def test_register_uses_default_envelope_when_only_policy_given(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    entry = reg.register(name="poll", kind="cron", backoff_policy="exponential")
    assert entry.backoff.base_interval_seconds == lr.DEFAULT_BASE_INTERVAL_S
    assert entry.backoff.max_interval_seconds == lr.DEFAULT_MAX_INTERVAL_S


def test_register_rejects_max_below_base(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    with pytest.raises(ValueError, match="max_interval"):
        reg.register(
            name="poll",
            kind="cron",
            backoff_policy="exponential",
            base_interval="1h",
            max_interval="15m",
        )


def test_register_rejects_invalid_backoff_policy(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    with pytest.raises(ValueError, match="backoff"):
        reg.register(name="poll", kind="cron", backoff_policy="magical")


def test_re_register_preserves_runtime_state(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(
        name="poll",
        kind="cron",
        cron="*/15 * * * *",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    reg.heartbeat("poll", status="ok", result="empty")
    reg.heartbeat("poll", status="ok", result="empty")

    # Re-register with new schedule — state must survive
    reg.register(
        name="poll",
        kind="cron",
        cron="*/30 * * * *",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    entry = reg.get("poll")
    assert entry is not None
    assert entry.backoff.empty_streak == 2  # preserved
    assert entry.cron == "*/30 * * * *"  # updated


# ─── exponential math ─────────────────────────────────────────────────────


def test_exponential_curve_doubles_per_empty_streak(fake_home):
    """After N empty fires, next interval = base * 2^N capped at max.
    Sequence: 30m → 1h → 2h → 4h → 4h (cap)."""
    reg = lr.LoopRegistry("tmux_42")
    reg.register(
        name="poll",
        kind="cron",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )

    # After Nth empty fire, current_interval = base * 2^N (capped at max).
    expected = [1800, 3600, 7200, 14400, 14400]  # streak 1,2,3,4,5
    for i, expected_seconds in enumerate(expected, start=1):
        reg.heartbeat("poll", status="ok", result="empty")
        entry = reg.get("poll")
        assert entry.backoff.empty_streak == i
        assert entry.backoff.current_interval_seconds() == expected_seconds, (
            f"after fire {i}: expected {expected_seconds}s, got {entry.backoff.current_interval_seconds()}s"
        )


def test_found_resets_streak(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(
        name="poll",
        kind="cron",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    for _ in range(3):
        reg.heartbeat("poll", status="ok", result="empty")
    assert reg.get("poll").backoff.empty_streak == 3

    reg.heartbeat("poll", status="ok", result="found")
    entry = reg.get("poll")
    assert entry.backoff.empty_streak == 0
    assert entry.backoff.current_interval_seconds() == 900  # back to base


def test_fail_resets_streak_too(fake_home):
    """Failures should retry at base, not compound the backoff."""
    reg = lr.LoopRegistry("tmux_42")
    reg.register(
        name="poll",
        kind="cron",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    for _ in range(4):
        reg.heartbeat("poll", status="ok", result="empty")
    assert reg.get("poll").backoff.empty_streak == 4

    reg.heartbeat("poll", status="fail", result="fail")
    entry = reg.get("poll")
    assert entry.backoff.empty_streak == 0


def test_threshold_set_after_heartbeat(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(
        name="poll",
        kind="cron",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    before = datetime.now(tz=timezone.utc)
    reg.heartbeat("poll", status="ok", result="empty")
    entry = reg.get("poll")
    threshold = datetime.fromisoformat(entry.backoff.next_fire_threshold)
    # First empty: threshold = now + 30m (streak now 1, so 15m * 2^1 = 30m)
    diff_seconds = (threshold - before).total_seconds()
    assert 1700 < diff_seconds < 1900, f"expected ~30m, got {diff_seconds}s"


# ─── should_fire ──────────────────────────────────────────────────────────


def test_should_fire_when_no_policy(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(name="poll", kind="cron")
    should, reason = reg.should_fire("poll")
    assert should is True
    assert "no policy" in reason


def test_should_fire_when_no_threshold_yet(fake_home):
    """Backoff enabled, but never fired yet — should fire (no threshold to clear)."""
    reg = lr.LoopRegistry("tmux_42")
    reg.register(name="poll", kind="cron", backoff_policy="exponential")
    should, reason = reg.should_fire("poll")
    assert should is True
    assert "no threshold" in reason


def test_should_fire_blocks_when_before_threshold(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(
        name="poll",
        kind="cron",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    reg.heartbeat("poll", status="ok", result="empty")  # threshold = now + 30m
    should, reason = reg.should_fire("poll")
    assert should is False
    assert "before threshold" in reason


def test_should_fire_allows_when_past_threshold(fake_home):
    """Manually set the threshold to the past."""
    reg = lr.LoopRegistry("tmux_42")
    reg.register(
        name="poll",
        kind="cron",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    reg.heartbeat("poll", status="ok", result="empty")
    # Rewrite threshold to 1 hour ago.
    data = reg._read()
    past = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    data["loops"]["poll"]["backoff"]["next_fire_threshold"] = past
    reg._write(data)

    should, reason = reg.should_fire("poll")
    assert should is True
    assert "past threshold" in reason


def test_should_fire_unknown_loop_treated_as_fire(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    should, _reason = reg.should_fire("does-not-exist")
    assert should is True


# ─── poke ─────────────────────────────────────────────────────────────────


def test_poke_clears_streak_and_threshold(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(
        name="poll",
        kind="cron",
        backoff_policy="exponential",
        base_interval="15m",
        max_interval="4h",
    )
    for _ in range(3):
        reg.heartbeat("poll", status="ok", result="empty")
    assert reg.get("poll").backoff.empty_streak == 3
    assert reg.get("poll").backoff.next_fire_threshold is not None

    entry = reg.poke("poll")
    assert entry is not None
    assert entry.backoff.empty_streak == 0
    assert entry.backoff.next_fire_threshold is None


def test_poke_unknown_loop_returns_none(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    assert reg.poke("does-not-exist") is None


# ─── heartbeat result inference ───────────────────────────────────────────


def test_heartbeat_status_ok_no_result_defaults_to_empty(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(name="poll", kind="cron", backoff_policy="exponential")
    entry = reg.heartbeat("poll", status="ok")
    assert entry.last_result == "empty"
    assert entry.backoff.empty_streak == 1


def test_heartbeat_status_fail_no_result_defaults_to_fail(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(name="poll", kind="cron", backoff_policy="exponential")
    reg.heartbeat("poll", status="ok", result="empty")  # streak=1
    entry = reg.heartbeat("poll", status="fail")
    assert entry.last_result == "fail"
    assert entry.backoff.empty_streak == 0  # fail resets


def test_heartbeat_invalid_result_rejected(fake_home):
    reg = lr.LoopRegistry("tmux_42")
    reg.register(name="poll", kind="cron")
    with pytest.raises(ValueError, match="Invalid result"):
        reg.heartbeat("poll", status="ok", result="maybe")


# ─── backwards compat ────────────────────────────────────────────────────


def test_backoff_none_loops_always_fire(fake_home):
    """Loops without backoff opt-in keep the legacy behavior."""
    reg = lr.LoopRegistry("tmux_42")
    reg.register(name="poll", kind="cron")
    # Heartbeat does not advance any threshold for policy=none.
    for _ in range(5):
        reg.heartbeat("poll", status="ok")
    entry = reg.get("poll")
    assert entry.backoff.policy == "none"
    assert entry.backoff.next_fire_threshold is None
    should, _ = reg.should_fire("poll")
    assert should is True


def test_legacy_loop_entry_without_backoff_field_loads_clean(fake_home):
    """Loops persisted before this ship had no 'backoff' field — must
    deserialize cleanly with policy='none'."""
    legacy = {
        "kind": "cron",
        "cron": "*/15 * * * *",
        "description": "pre-backoff loop",
        "registered_at": "2026-04-26T00:00:00+00:00",
        "last_run": None,
        "last_status": None,
        "last_message": None,
    }
    entry = lr.LoopEntry.from_dict("poll", legacy)
    assert entry.backoff.policy == "none"
    assert entry.backoff.empty_streak == 0
