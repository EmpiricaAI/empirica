"""A listener's memory next to its activity.

`empirica loop listen` is a long-lived daemon. mesh-support measured its RSS at 137 to 471 MB across ten listeners and 1,014 MB in one after 29 h, from
single `ps` samples, which cannot say whether the growth follows reconnects, catch-up polls or events. This logs one line with the RSS and cumulative
counters at start, every ten minutes and at shutdown, so a journal read shows the activity that preceded any growth. Observability only: it changes
nothing the listener does, and a failure to read or write is swallowed.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable
from typing import TextIO

DEFAULT_INTERVAL_SEC = 600.0
_MIN_INTERVAL_SEC = 1.0
_COUNTERS = ("connects", "events", "catchups", "catchup_failures")


def _usable_interval(value: float) -> bool:
    """A finite interval of at least a second. 0, a negative number or NaN made the logger a busy loop, and inf killed its thread."""
    return math.isfinite(value) and value >= _MIN_INTERVAL_SEC


def stats_interval_from_env() -> float:
    """The stats interval from EMPIRICA_LISTENER_STATS_INTERVAL_SEC, or the default when it is unset, not a number, or not usable.

    Read when a logger is built, not at import: the package imports the listener eagerly, so a bad value raised at import time failed every command."""
    try:
        value = float(os.environ.get("EMPIRICA_LISTENER_STATS_INTERVAL_SEC", DEFAULT_INTERVAL_SEC))
    except ValueError:
        return DEFAULT_INTERVAL_SEC
    return value if _usable_interval(value) else DEFAULT_INTERVAL_SEC


def parse_statm_rss_mb(text: str | None, page_size: int) -> float | None:
    """Resident set size in MB from the text of /proc/self/statm (second field, in pages). None when it cannot be read."""
    try:
        resident_pages = int((text or "").split()[1])
    except (IndexError, ValueError):
        return None
    return resident_pages * page_size / (1024 * 1024)


def current_rss_mb() -> float | None:
    """This process's current RSS in MB on Linux, None elsewhere. (getrusage gives the PEAK, which is the wrong quantity for a growth question.)"""
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            return parse_statm_rss_mb(f.read(), os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError):
        return None


class ListenerStats:
    """Cumulative counters for one listener process, plus a formatter. `bump` is called from the loop's thread only."""

    def __init__(self, rss_fn: Callable[[], float | None] = current_rss_mb):
        self._rss_fn = rss_fn
        self._started = time.monotonic()
        self.counts = dict.fromkeys(_COUNTERS, 0)

    def bump(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1

    def line(self, why: str) -> str:
        rss = self._rss_fn()
        parts = [
            f"rss_mb={'unknown' if rss is None else f'{rss:.1f}'}",
            f"threads={threading.active_count()}",
            f"uptime_s={time.monotonic() - self._started:.0f}",
            *(f"{name}={self.counts.get(name, 0)}" for name in _COUNTERS),
        ]
        return f"listener: stats ({why}) " + " ".join(parts)


class StatsLogger:
    """Writes the stats line to `stream` every `interval_sec` on a daemon thread. Never raises into the listener."""

    def __init__(self, stats: ListenerStats, stream: TextIO, interval_sec: float | None = None):
        self._stats = stats
        self._stream = stream
        self._interval = (
            stats_interval_from_env()
            if interval_sec is None
            else interval_sec
            if _usable_interval(interval_sec)
            else DEFAULT_INTERVAL_SEC
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def emit(self, why: str) -> None:
        try:
            self._stream.write(self._stats.line(why) + "\n")
            self._stream.flush()
        except Exception:  # noqa: S110 — a stats line must never take the listener down
            pass

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="listener-stats")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.emit("heartbeat")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
