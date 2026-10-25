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

import threading
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
        on_check_error: Callable[[Exception], None] | None = None,
    ):
        if heartbeat_s <= 0 or retry_s <= 0 or event_ttl_s <= 0:
            raise ValueError("heartbeat_s, retry_s and event_ttl_s must be positive")
        self._panes, self._wake, self._clock, self._has_pending = panes, wake, clock, has_pending
        self._on_check_error = on_check_error
        self._heartbeat_s, self._retry_s, self._event_ttl_s = heartbeat_s, retry_s, event_ttl_s
        self._started = clock()
        # Guards only the event fields below, and only for the few instructions that touch them. It is NEVER held across a subprocess
        # call: note_event runs on the listener's event path and must not wait for a tick that is inside a poll, a capture and a send.
        self._lock = threading.Lock()
        self._event_at: float | None = None
        self._event_seq = (
            0  # which event a pane has been woken for; a counter, because two events can share a clock reading
        )
        self._woken_seq: dict[str, int] = {}
        self._last_ok: dict[str, float] = {}
        self._last_try: dict[str, float] = {}

    def note_event(self) -> None:
        """A mesh event arrived for this practice: wake its panes."""
        with self._lock:
            self._event_at = self._clock()
            self._event_seq += 1

    def tick(self) -> list[WakeOutcome]:
        """Wake whatever is due and return what was attempted. Never raises."""
        now = self._clock()
        with self._lock:
            if self._event_at is not None and now - self._event_at > self._event_ttl_s:
                self._event_at = None
        try:
            panes = list(self._panes())
        except Exception:
            return []
        inbox = self._inbox_look()
        outcomes = [o for o in (self._tick_pane(pane, now, inbox) for pane in panes) if o is not None]
        with self._lock:
            if (
                self._event_at is not None
                and panes
                and all(self._woken_seq.get(p, 0) >= self._event_seq for p in panes)
            ):
                self._event_at = None
        return outcomes

    def _inbox_look(self) -> Callable[[bool], bool]:
        """One look at the inbox per tick, shared by every pane: True/False, or a failed look.

        A failed look wakes for an event (the event proves mail arrived) and not for a heartbeat: the hook a wake runs polls the same
        inbox the same way, so waking on a broken check delivers a bare [wake] and nothing else. The failure is reported, never silent.
        """
        look: list[bool | None] = []

        def has_mail(event_due: bool) -> bool:
            if not look:
                try:
                    look.append(bool(self._has_pending()))
                except Exception as e:
                    look.append(None)
                    if self._on_check_error is not None:
                        try:
                            self._on_check_error(e)
                        except Exception:  # noqa: S110 - reporting must never break the scheduler
                            pass
            return event_due if look[0] is None else look[0]

        return has_mail

    def _tick_pane(self, pane: str, now: float, inbox: Callable[[bool], bool]) -> WakeOutcome | None:
        last_ok = self._last_ok.get(pane, self._started)
        last_try = self._last_try.get(pane)
        with self._lock:
            seq = self._event_seq
            event_due = self._event_at is not None and self._woken_seq.get(pane, 0) < seq
        beat_due = now - last_ok >= self._heartbeat_s
        if not (event_due or beat_due):
            return None
        if last_try is not None and now - last_try < self._retry_s:
            return None
        self._last_try[pane] = now
        if not inbox(event_due):
            # No mail to deliver. For an event it means the mail was already handled (a first wake was refused while the session
            # answered it), so the event is satisfied for this pane: waking it now would be a bare [wake] turn.
            if event_due:
                self._mark_woken(pane, seq)
            return None
        try:
            result = self._wake(pane)
        except Exception as e:
            result = WakeResult(pane, False, f"wake raised: {e}", "error")
        if result.success:
            self._last_ok[pane] = now
            self._mark_woken(
                pane, seq
            )  # `seq` is the event this wake answered: one that arrived DURING a slow wake is still owed
        return WakeOutcome(pane, "event" if event_due else "heartbeat", result)

    def _mark_woken(self, pane: str, seq: int) -> None:
        with self._lock:
            self._woken_seq[pane] = max(self._woken_seq.get(pane, 0), seq)
