"""When to give an idle session a turn, so a practice with mail is not forgotten (goal 87772ac4).

A Claude Code session hears the mesh only through a Monitor, which expires after 30 minutes, and a session resumed without a turn
never arms one. This scheduler is the part that does not depend on the model: the listener service for a practice tells it about
events and ticks it, and it decides which of the practice's panes to wake with `wake_instance` (which itself refuses a pane that is
busy, mid-draft or on a dialog).

Two rules shape it:

* An event wakes every pane of the practice, and a refused wake is retried every `retry_s` until it lands or the event is older
  than `event_ttl_s`. A pane that stays busy is attempted once per interval, never every tick.
* A heartbeat wakes a pane that has not been woken for `heartbeat_s`, but only if `has_pending()` says the inbox holds something.
  A wake is a model turn; waking every idle pane every two hours to find an empty inbox would spend tokens to learn nothing. The
  check is cheap and runs no model, and when it fails the practice is treated as having mail, because a broken check must not
  silence a practice that has some.

Clock, pane list, wake function and inbox check are injected: the scheduler touches no tmux, network or wall clock itself, and
nothing in it can raise out of `tick`, because it runs inside a long-lived service.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from empirica.core.cockpit.instance_actions import WakeResult

HEARTBEAT_S = 2 * 3600.0
RETRY_S = 300.0
EVENT_TTL_S = 6 * 3600.0


@dataclass(frozen=True)
class WakeOutcome:
    pane: str
    reason: str  # 'event' | 'heartbeat'
    result: WakeResult


class WakeScheduler:
    def __init__(
        self,
        panes: Callable[[], list[str]],
        wake: Callable[[str], WakeResult],
        clock: Callable[[], float],
        has_pending: Callable[[], bool],
        *,
        heartbeat_s: float = HEARTBEAT_S,
        retry_s: float = RETRY_S,
        event_ttl_s: float = EVENT_TTL_S,
    ):
        if heartbeat_s <= 0 or retry_s <= 0 or event_ttl_s <= 0:
            raise ValueError("heartbeat_s, retry_s and event_ttl_s must be positive")
        self._panes, self._wake, self._clock, self._has_pending = panes, wake, clock, has_pending
        self._heartbeat_s, self._retry_s, self._event_ttl_s = heartbeat_s, retry_s, event_ttl_s
        self._started = clock()
        self._event_at: float | None = None
        self._event_seq = (
            0  # which event a pane has been woken for; a counter, because two events can share a clock reading
        )
        self._woken_seq: dict[str, int] = {}
        self._last_ok: dict[str, float] = {}
        self._last_try: dict[str, float] = {}

    def note_event(self) -> None:
        """A mesh event arrived for this practice: wake its panes."""
        self._event_at = self._clock()
        self._event_seq += 1

    def tick(self) -> list[WakeOutcome]:
        """Wake whatever is due and return what was attempted. Never raises."""
        now = self._clock()
        if self._event_at is not None and now - self._event_at > self._event_ttl_s:
            self._event_at = None
        try:
            panes = list(self._panes())
        except Exception:
            return []

        pending_cache: list[bool] = []

        def inbox_has_mail() -> bool:
            if not pending_cache:
                try:
                    pending_cache.append(bool(self._has_pending()))
                except Exception:
                    pending_cache.append(True)
            return pending_cache[0]

        outcomes: list[WakeOutcome] = []
        for pane in panes:
            last_ok = self._last_ok.get(pane, self._started)
            last_try = self._last_try.get(pane)
            event_due = self._event_at is not None and self._woken_seq.get(pane, 0) < self._event_seq
            beat_due = now - last_ok >= self._heartbeat_s
            if not (event_due or beat_due):
                continue
            if last_try is not None and now - last_try < self._retry_s:
                continue
            if not event_due and not inbox_has_mail():
                self._last_try[pane] = now  # an empty inbox is an attempt too: look again after retry_s, not every tick
                continue
            self._last_try[pane] = now
            try:
                result = self._wake(pane)
            except Exception as e:
                result = WakeResult(pane, False, f"wake raised: {e}", "error")
            if result.success:
                self._last_ok[pane] = now
                self._woken_seq[pane] = self._event_seq
            outcomes.append(WakeOutcome(pane, "event" if event_due else "heartbeat", result))

        if self._event_at is not None and panes and all(self._woken_seq.get(p, 0) >= self._event_seq for p in panes):
            self._event_at = None
        return outcomes
