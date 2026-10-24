"""WakeScheduler decides which panes to wake and when (goal 87772ac4, step 2).

A session whose Monitor expired, or that was resumed without a turn, hears nothing until something gives it a turn. The scheduler
is that something, run by the listener service outside the model: on an event it wakes the practice's panes, and every two hours
it wakes them if the inbox holds something pending. Two properties carry the design and are tested first:

* An empty inbox costs nothing. A wake is a model turn, so a blind heartbeat across a dozen idle panes would burn tokens to
  learn nothing. The heartbeat asks a cheap inbox check (no model) and wakes only when it says something is pending.
* A refused wake is retried, not forgotten. The guard refuses a busy pane or a draft; the event must still reach that session
  once it is idle, and a pane that stays busy must not be hammered every tick.

Clock, pane list, wake function and inbox check are injected, so no test touches tmux, the network or the wall clock.
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit.instance_actions import WakeResult
from empirica.core.cockpit.wake_scheduler import WakeScheduler

HOUR = 3600.0


class Rig:
    """A scheduler wired to fakes, with a clock the test moves and a record of every wake attempt."""

    def __init__(self, panes=("tmux_3",), pending=False, idle=True, **kw):
        self.now = 1_000_000.0
        self.panes, self.pending, self.idle = list(panes), pending, idle
        self.wakes: list[str] = []
        self.checks = 0
        self.sched = WakeScheduler(
            panes=lambda: self.panes, wake=self._wake, clock=lambda: self.now, has_pending=self._pending, **kw
        )

    def _wake(self, pane):
        self.wakes.append(pane)
        ok = self.idle
        return WakeResult(
            pane, ok, "sent" if ok else "not woken: a turn is running", "tmux-send-keys" if ok else "refused"
        )

    def _pending(self):
        self.checks += 1
        return self.pending

    def advance(self, seconds):
        self.now += seconds
        return self.sched.tick()


def test_nothing_is_woken_at_start_or_on_a_quiet_tick():
    rig = Rig(pending=True)
    assert rig.sched.tick() == []
    assert rig.wakes == []


def test_an_event_wakes_every_pane_of_the_practice():
    rig = Rig(panes=("tmux_3", "tmux_4"))
    rig.sched.note_event()
    out = rig.sched.tick()
    assert sorted(rig.wakes) == ["tmux_3", "tmux_4"]
    assert {o.reason for o in out} == {"event"} and all(o.result.success for o in out)


def test_a_woken_pane_is_not_woken_again_for_the_same_event():
    rig = Rig()
    rig.sched.note_event()
    rig.sched.tick()
    rig.advance(60)
    rig.advance(600)
    assert rig.wakes == ["tmux_3"]


def test_the_heartbeat_wakes_after_two_hours_only_when_something_is_pending():
    rig = Rig(pending=True)
    rig.advance(2 * HOUR - 1)
    assert rig.wakes == [], "not yet due"
    out = rig.advance(2)
    assert rig.wakes == ["tmux_3"] and out[0].reason == "heartbeat"


def test_an_empty_inbox_costs_no_wake_and_is_checked_once_per_due_pane_not_every_tick():
    rig = Rig(pending=False)
    rig.advance(2 * HOUR + 1)
    assert rig.wakes == []
    assert rig.checks >= 1
    checks_after_due = rig.checks
    rig.advance(60)
    assert rig.wakes == []
    assert rig.checks <= checks_after_due + 1, "the inbox check must not run on every tick between heartbeats"


def test_a_successful_heartbeat_restarts_the_two_hours():
    rig = Rig(pending=True)
    rig.advance(2 * HOUR + 1)
    rig.advance(HOUR)
    assert rig.wakes == ["tmux_3"]
    rig.advance(HOUR + 2)
    assert rig.wakes == ["tmux_3", "tmux_3"]


def test_a_refused_wake_is_retried_after_the_retry_interval_and_not_before():
    rig = Rig(idle=False, retry_s=300)
    rig.sched.note_event()
    rig.sched.tick()
    assert rig.wakes == ["tmux_3"]
    rig.advance(60)
    assert rig.wakes == ["tmux_3"], "inside the retry interval: no hammering"
    rig.idle = True
    out = rig.advance(300)
    assert rig.wakes == ["tmux_3", "tmux_3"] and out[0].result.success


def test_a_pane_that_stays_busy_is_attempted_once_per_retry_interval():
    rig = Rig(idle=False, retry_s=300)
    rig.sched.note_event()
    for _ in range(10):
        rig.advance(60)
    assert len(rig.wakes) <= 3, rig.wakes


def test_a_stale_event_is_dropped_instead_of_waking_a_pane_that_appears_much_later():
    rig = Rig(panes=(), event_ttl_s=6 * HOUR)
    rig.sched.note_event()
    rig.advance(7 * HOUR)
    rig.panes = ["tmux_9"]
    rig.pending = False
    rig.advance(60)
    assert rig.wakes == []


def test_a_pane_that_appears_while_an_event_is_pending_is_woken():
    rig = Rig(panes=())
    rig.sched.note_event()
    rig.advance(60)
    rig.panes = ["tmux_9"]
    rig.advance(60)
    assert rig.wakes == ["tmux_9"]


def test_a_wake_that_raises_does_not_escape_the_tick():
    def boom(_pane):
        raise RuntimeError("tmux exploded")

    sched = WakeScheduler(panes=lambda: ["tmux_3"], wake=boom, clock=lambda: 5.0, has_pending=lambda: True)
    sched.note_event()
    out = sched.tick()
    assert len(out) == 1 and out[0].result.success is False and "tmux exploded" in out[0].result.detail


def test_an_inbox_check_that_raises_is_treated_as_something_pending():
    """Fail toward waking: a broken check must not silence a practice that has mail."""

    def broken():
        raise OSError("cortex unreachable")

    now = [0.0]
    woke: list[str] = []
    sched = WakeScheduler(
        panes=lambda: ["tmux_3"],
        wake=lambda p: (woke.append(p), WakeResult(p, True, "sent", "tmux-send-keys"))[1],
        clock=lambda: now[0],
        has_pending=broken,
    )
    now[0] = 2 * HOUR + 1
    sched.tick()
    assert woke == ["tmux_3"]


@pytest.mark.parametrize("kw", [{"heartbeat_s": 0}, {"retry_s": 0}, {"event_ttl_s": 0}])
def test_nonsense_intervals_are_refused_at_construction(kw):
    with pytest.raises(ValueError):
        WakeScheduler(panes=lambda: [], wake=lambda _p: None, clock=lambda: 0.0, has_pending=lambda: False, **kw)
