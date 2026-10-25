"""The wake service a practice's listener runs: it gives idle sessions a turn when there is mail (goal 87772ac4).

The listener already receives mesh events for its practice, but a session only hears them through a Monitor, which expires, or not at
all if it was resumed without a turn. This service sits beside the listener and does what the model cannot: it wakes the practice's
idle panes (`wake_instance`, which refuses a busy pane, a draft or a dialog) on an event and on a two-hour heartbeat, and the woken
turn's hook (tool-router, blank prompt) injects the inbox.

Two rules bound it:

* **Opt-in.** `build_wake_service` returns None unless `EMPIRICA_LISTENER_WAKE` is set. It types into terminals unattended, and
  scheduled behaviour is never installed by default.
* **One wake per message.** Mail already surfaced by a successful wake is remembered (`~/.empirica/wake_surfaced_<ai_id>.json`) and
  never wakes a pane again, so a message nobody archives is not nudged every two hours; a NEW message still wakes. Mail from before the
  service existed is surfaced once, at the first heartbeat. SessionStart still shows everything pending.

Nothing here may raise into the listener: `tick_once` and the thread loop swallow and log every failure.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from empirica.core.cockpit.instance_actions import WakeResult, wake_instance
from empirica.core.cockpit.wake_scheduler import WakeScheduler
from empirica.core.cockpit.wake_targets import wakeable_panes

ENABLE_VAR = "EMPIRICA_LISTENER_WAKE"
OVER_SUGGESTION_VAR = "EMPIRICA_WAKE_OVER_SUGGESTION"
TICK_S = 60.0
PANES_TTL_S = 120.0
POLL_TIMEOUT_S = 20
SURFACED_KEEP = 500


def _stamp() -> str:
    """Wall-clock UTC for a log line. The listener's stderr is a file, not journald, so a line carries its own time."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class InboxWatch:
    """A token-free look at the practice's accepted mail, and the memory of which of it a wake has already surfaced."""

    def __init__(self, ai_id: str, state_path: Path, run: Callable = subprocess.run):
        self._ai_id, self._state_path, self._run = ai_id, state_path, run
        self._current: list[str] = []

    def _accepted_ids(self) -> list[str]:
        out = self._run(
            [
                "empirica",
                "mailbox",
                "poll",
                "--ai-id",
                self._ai_id,
                "--status",
                "accepted",
                "--limit",
                "100",
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=POLL_TIMEOUT_S,
            check=False,
        )
        if out.returncode != 0:
            raise RuntimeError(f"mailbox poll exited {out.returncode}")
        payload = json.loads(out.stdout)
        return [str(p["id"]) for p in payload.get("proposals") or [] if p.get("id")]

    def _surfaced(self) -> set[str]:
        try:
            return set(json.loads(self._state_path.read_text()).get("ids") or [])
        except (OSError, ValueError, AttributeError):
            return set()

    def has_pending(self) -> bool:
        """Whether accepted mail exists that no wake has surfaced yet. Raises when the poll fails: the scheduler treats that as mail."""
        self._current = self._accepted_ids()
        return bool(set(self._current) - self._surfaced())

    def mark_surfaced(self) -> None:
        """Record the current accepted mail as surfaced. Called after a wake landed; re-polls so mail that arrived since is included."""
        with contextlib.suppress(Exception):  # on failure keep the ids from the last successful look
            self._current = self._accepted_ids()
        merged = list(dict.fromkeys([*self._surfaced(), *self._current]))[-SURFACED_KEEP:]
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"ids": merged}))
            tmp.replace(self._state_path)
        except OSError:
            pass


class WakeService:
    def __init__(
        self,
        ai_id: str,
        *,
        watch: InboxWatch,
        panes: Callable[[], list[str]] | None = None,
        wake: Callable[[str], WakeResult] = wake_instance,
        clock: Callable[[], float] = time.monotonic,
        err_stream=sys.stderr,
    ):
        self._ai_id, self._watch, self._clock, self._err = ai_id, watch, clock, err_stream
        self._raw_wake = wake
        self._pane_source = panes or (lambda: wakeable_panes(ai_id))
        self._panes_at: float | None = None
        self._panes_cache: list[str] = []
        self._scheduler = WakeScheduler(panes=self._panes, wake=self._wake, clock=clock, has_pending=watch.has_pending)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _panes(self) -> list[str]:
        now = self._clock()
        if self._panes_at is None or now - self._panes_at >= PANES_TTL_S:
            self._panes_cache, self._panes_at = list(self._pane_source()), now
        return self._panes_cache

    def _wake(self, pane: str) -> WakeResult:
        result = self._raw_wake(pane)
        if result.success:
            self._watch.mark_surfaced()
        return result

    def note_event(self) -> None:
        with self._lock:
            self._scheduler.note_event()

    def tick_once(self) -> None:
        """One scheduler pass. Never raises; every attempt is logged to the listener's stderr."""
        try:
            with self._lock:
                outcomes = self._scheduler.tick()
            for o in outcomes:
                self._err.write(
                    f"{_stamp()} listener: wake {o.reason} {o.pane}: {'sent' if o.result.success else o.result.detail}\n"
                )
        except Exception as e:
            try:
                self._err.write(f"{_stamp()} listener: wake tick failed (non-fatal): {type(e).__name__}: {e}\n")
            except Exception:  # noqa: S110 - a broken log stream must not take the listener down
                pass

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(TICK_S):
                self.tick_once()

        self._thread = threading.Thread(target=loop, name=f"listener-wake-{self._ai_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_wake_service(ai_id: str, env: Mapping[str, str] | None = None, err_stream=sys.stderr) -> WakeService | None:
    """The practice's wake service, or None when `EMPIRICA_LISTENER_WAKE` is not set to a true value.

    `EMPIRICA_WAKE_OVER_SUGGESTION` additionally lets a wake land on a pane that shows only Claude Code's dim prompt suggestion. It
    is separate and off by default: a suggestion is the commonest idle state after a turn, so without it many panes are skipped, but
    what Space+Enter does to one is still being measured.
    """
    source = env if env is not None else os.environ
    if not _truthy(source.get(ENABLE_VAR)):
        return None
    over_suggestion = _truthy(source.get(OVER_SUGGESTION_VAR))
    state = Path.home() / ".empirica" / f"wake_surfaced_{ai_id}.json"
    return WakeService(
        ai_id,
        watch=InboxWatch(ai_id, state),
        wake=lambda pane: wake_instance(pane, allow_suggestion=over_suggestion),
        err_stream=err_stream,
    )
