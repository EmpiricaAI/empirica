"""Tests for PROPOSAL_LOOP_SELF_SCHEDULING — body-owned self-rescheduling.

Covers:
  - SchedulePlan / cron_pin_one_shot output shape
  - schedule_next math: snap-back at streak 0, doubling per empty,
    cap at max_interval
  - heartbeat preserves scheduling fields when caller doesn't pass them
  - heartbeat with result='paused' freezes backoff state (no streak math)
  - pause clears next_scheduled_job_id (the body's pause-check is the
    backstop the proposal explicitly relies on)
  - resume preserves scheduler_kind for the cockpit hint path
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from empirica.core.cockpit import loop_registry as lr


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake_dir = tmp_path / ".empirica"
    fake_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lr, "EMPIRICA_DIR", fake_dir)
    return fake_dir


# ─── cron pin helper ────────────────────────────────────────────────────────


class TestCronPinOneShot:
    def test_matches_proposal_example(self):
        # Proposal example: 2026-04-28T03:30:00Z → '30 3 28 4 *'
        t = datetime(2026, 4, 28, 3, 30, 0, tzinfo=timezone.utc)
        assert lr.cron_pin_one_shot(t) == "30 3 28 4 *"

    def test_day_of_week_is_wildcard(self):
        # One-shot — DOW must be wildcarded so the scheduler doesn't
        # accidentally match a future occurrence.
        t = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
        assert lr.cron_pin_one_shot(t).endswith(" *")


# ─── schedule_next math ─────────────────────────────────────────────────────


class TestScheduleNext:
    def test_returns_none_for_unknown_loop(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        assert reg.schedule_next("does-not-exist") is None

    def test_streak_zero_uses_base(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(
            name="loop-a",
            kind="cron",
            interval="15m",
            backoff_policy="exponential",
            base_interval="15m",
            max_interval="4h",
        )
        plan = reg.schedule_next("loop-a")
        assert plan is not None
        assert plan.interval_seconds == 15 * 60
        assert plan.current_streak == 0
        assert "snap-back" in plan.reason

    def test_empty_streak_doubles_then_caps(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(
            name="loop-a",
            kind="cron",
            interval="15m",
            backoff_policy="exponential",
            base_interval="15m",
            max_interval="4h",
        )
        # Simulate streak 1, 2, 3, ... up to cap.
        intervals: list[int] = []
        for _ in range(8):
            reg.heartbeat("loop-a", status="ok", result="empty")
            plan = reg.schedule_next("loop-a")
            assert plan is not None
            intervals.append(plan.interval_seconds)
        # Doubling — 15m → 30m → 60m → 120m → 240m (cap) → 240m → ...
        # current_interval_seconds caps at 4h = 14400s.
        assert intervals[0] == 30 * 60  # streak 1
        assert intervals[1] == 60 * 60  # streak 2
        assert intervals[2] == 120 * 60  # streak 3
        assert intervals[3] == 240 * 60  # streak 4 hits cap
        assert intervals[4] == 240 * 60  # capped
        assert intervals[5] == 240 * 60  # still capped

    def test_found_resets_streak(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(
            name="loop-a",
            kind="cron",
            interval="15m",
            backoff_policy="exponential",
            base_interval="15m",
            max_interval="4h",
        )
        # Stretch out for a few empty fires, then found.
        for _ in range(3):
            reg.heartbeat("loop-a", status="ok", result="empty")
        reg.heartbeat("loop-a", status="ok", result="found")
        plan = reg.schedule_next("loop-a")
        assert plan is not None
        assert plan.current_streak == 0
        assert plan.interval_seconds == 15 * 60  # back to base

    def test_fail_resets_streak(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(
            name="loop-a",
            kind="cron",
            interval="15m",
            backoff_policy="exponential",
            base_interval="15m",
            max_interval="4h",
        )
        for _ in range(3):
            reg.heartbeat("loop-a", status="ok", result="empty")
        reg.heartbeat("loop-a", status="fail", result="fail")
        plan = reg.schedule_next("loop-a")
        assert plan is not None
        assert plan.current_streak == 0
        assert plan.interval_seconds == 15 * 60  # retry at base, no compound delay

    def test_no_backoff_uses_base_interval(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(
            name="loop-a",
            kind="cron",
            interval="10m",
            # No backoff — base_interval falls back to interval.
        )
        plan = reg.schedule_next("loop-a")
        assert plan is not None
        assert plan.interval_seconds == 10 * 60
        assert plan.current_streak == 0
        assert "no backoff" in plan.reason

    def test_plan_stamps_next_fire_at_on_registry(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(name="loop-a", kind="cron", interval="15m")
        plan = reg.schedule_next("loop-a")
        assert plan is not None
        entry = reg.get("loop-a")
        assert entry is not None
        assert entry.scheduling.next_fire_at is not None
        # Round-trip should match within one second.
        stored = datetime.fromisoformat(entry.scheduling.next_fire_at)
        assert abs((stored - plan.fire_at).total_seconds()) < 1

    def test_plan_to_dict_shape_matches_proposal(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(
            name="loop-a",
            kind="cron",
            interval="15m",
            backoff_policy="exponential",
            base_interval="15m",
            max_interval="4h",
        )
        plan = reg.schedule_next("loop-a")
        assert plan is not None
        d = plan.to_dict()
        assert set(d.keys()) == {
            "next_fire_at",
            "interval_seconds",
            "current_streak",
            "reason",
            "cron_one_shot",
        }


# ─── heartbeat scheduling fields ────────────────────────────────────────────


class TestHeartbeatScheduling:
    def test_records_next_scheduled_job_id(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(name="loop-a", kind="cron", interval="15m")
        reg.heartbeat(
            "loop-a",
            status="ok",
            result="empty",
            next_scheduled_job_id="job-abc123",
            scheduler_kind="cron-create",
        )
        entry = reg.get("loop-a")
        assert entry is not None
        assert entry.scheduling.next_scheduled_job_id == "job-abc123"
        assert entry.scheduling.scheduler_kind == "cron-create"

    def test_empty_string_clears_job_id(self, fake_home):
        # Pause path uses '' to clear the job id without changing other fields.
        reg = lr.LoopRegistry("tmux_1")
        reg.register(name="loop-a", kind="cron", interval="15m")
        reg.heartbeat("loop-a", status="ok", result="empty", next_scheduled_job_id="job-xyz")
        reg.heartbeat("loop-a", status="ok", result="empty", next_scheduled_job_id="")
        entry = reg.get("loop-a")
        assert entry is not None
        assert entry.scheduling.next_scheduled_job_id is None

    def test_omitted_args_preserve_existing_scheduling(self, fake_home):
        # Caller can heartbeat with just status/result; previously-set
        # scheduling fields persist.
        reg = lr.LoopRegistry("tmux_1")
        reg.register(name="loop-a", kind="cron", interval="15m")
        reg.heartbeat(
            "loop-a", status="ok", result="empty", next_scheduled_job_id="job-xyz", scheduler_kind="cron-create"
        )
        reg.heartbeat("loop-a", status="ok", result="empty")
        entry = reg.get("loop-a")
        assert entry is not None
        assert entry.scheduling.next_scheduled_job_id == "job-xyz"
        assert entry.scheduling.scheduler_kind == "cron-create"

    def test_paused_result_freezes_streak(self, fake_home):
        # PROPOSAL_LOOP_SELF_SCHEDULING: result='paused' must NOT advance
        # the empty_streak — pause is a no-state transition. Backoff
        # state freezes mid-stretch.
        reg = lr.LoopRegistry("tmux_1")
        reg.register(
            name="loop-a",
            kind="cron",
            interval="15m",
            backoff_policy="exponential",
            base_interval="15m",
            max_interval="4h",
        )
        # Stretch to streak 2.
        reg.heartbeat("loop-a", status="ok", result="empty")
        reg.heartbeat("loop-a", status="ok", result="empty")
        # Now a pause-skip fire — should NOT bump streak to 3.
        reg.heartbeat("loop-a", status="ok", result="paused", message="skipped, paused")
        entry = reg.get("loop-a")
        assert entry is not None
        assert entry.backoff.empty_streak == 2  # frozen, not 3
        assert entry.last_result == "paused"

    def test_invalid_scheduler_kind_rejected(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(name="loop-a", kind="cron", interval="15m")
        with pytest.raises(ValueError, match="Invalid scheduler_kind"):
            reg.heartbeat("loop-a", status="ok", scheduler_kind="made-up")


# ─── round-trip persistence ─────────────────────────────────────────────────


class TestSchedulingRoundTrip:
    def test_scheduling_state_persists_to_disk(self, fake_home):
        reg = lr.LoopRegistry("tmux_1")
        reg.register(name="loop-a", kind="cron", interval="15m")
        reg.heartbeat(
            "loop-a", status="ok", result="empty", next_scheduled_job_id="job-abc", scheduler_kind="cron-create"
        )
        # Re-create registry pointing at same file
        reg2 = lr.LoopRegistry("tmux_1")
        entry = reg2.get("loop-a")
        assert entry is not None
        assert entry.scheduling.scheduler_kind == "cron-create"
        assert entry.scheduling.next_scheduled_job_id == "job-abc"

    def test_legacy_entries_default_empty_scheduling(self, fake_home):
        # An entry written before scheduling field existed should load
        # cleanly with default empty SchedulingState.
        reg = lr.LoopRegistry("tmux_1")
        reg.register(name="loop-a", kind="cron", interval="15m")
        # Manually strip scheduling from on-disk JSON to simulate old format.
        import json

        with open(reg.path, encoding="utf-8") as f:
            data = json.load(f)
        del data["loops"]["loop-a"]["scheduling"]
        with open(reg.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        # Re-read should produce default scheduling (None fields).
        reg2 = lr.LoopRegistry("tmux_1")
        entry = reg2.get("loop-a")
        assert entry is not None
        assert entry.scheduling.scheduler_kind is None
        assert entry.scheduling.next_scheduled_job_id is None
        assert entry.scheduling.next_fire_at is None
