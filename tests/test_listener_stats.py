"""The listener says how much memory it holds next to what it has been doing (goal b1c28f60; mesh-support's RSS reports).

mesh-support measured `empirica loop listen` at 137 to 471 MB across ten listeners, front-loaded and not monotonic, and one instance at 1,014 MB after
29 h. A single `ps` sample cannot say whether that tracks reconnects, catch-up polls or events. The listener now logs a stats line with its RSS and
cumulative counters at start, every ten minutes and at shutdown, so the growth can be read against the activity that preceded it. Observability only:
no listener behaviour changes.
"""

from __future__ import annotations

import io
import json

import pytest

import empirica.core.loop_scheduler.listener as listener_mod
from empirica.core.loop_scheduler.listener import ListenerStopped, run_listener
from empirica.core.loop_scheduler.listener_stats import ListenerStats, StatsLogger, parse_statm_rss_mb

# ── the pieces ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_rss_is_the_resident_pages_times_the_page_size():
    assert parse_statm_rss_mb("25600 12800 100 10 0 5 0", page_size=4096) == pytest.approx(50.0)


@pytest.mark.parametrize("bad", ["", "12", "a b c", None])
def test_an_unreadable_statm_is_unknown_not_zero(bad):
    assert parse_statm_rss_mb(bad, page_size=4096) is None


def test_the_line_carries_rss_uptime_and_every_counter():
    stats = ListenerStats(rss_fn=lambda: 321.5)
    for name in ("connects", "connects", "events", "catchups", "catchups", "catchups", "catchup_failures"):
        stats.bump(name)
    line = stats.line("heartbeat")
    assert line.startswith("listener: stats (heartbeat) ")
    for part in ("rss_mb=321.5", "connects=2", "events=1", "catchups=3", "catchup_failures=1", "uptime_s="):
        assert part in line


def test_an_unknown_rss_prints_as_unknown_not_as_zero():
    assert "rss_mb=unknown" in ListenerStats(rss_fn=lambda: None).line("start")


def test_threads_are_counted_so_a_thread_leak_would_show():
    assert "threads=" in ListenerStats(rss_fn=lambda: 1.0).line("start")


def test_the_logger_writes_a_line_and_stops_promptly():
    out = io.StringIO()
    logger = StatsLogger(ListenerStats(rss_fn=lambda: 1.0), out, interval_sec=3600)
    logger.emit("start")
    logger.start()
    logger.stop(timeout=2.0)
    assert out.getvalue().count("listener: stats (start)") == 1
    assert logger._thread is None or not logger._thread.is_alive()


# ── wired into the loop ────────────────────────────────────────────────────────────────────────────────────────


class _Proc:
    def __init__(self, lines):
        self._it = iter(lines)
        self.stdout = self

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)

    def readline(self):
        try:
            return next(self._it)
        except StopIteration:
            return ""

    def terminate(self):
        pass

    def wait(self, timeout=None):
        _ = timeout
        return 0

    def kill(self):
        pass


def test_the_shutdown_line_counts_what_the_loop_did(monkeypatch):
    from empirica.config.credentials_loader import get_credentials_loader

    monkeypatch.setenv("EMPIRICA_LISTENER_NO_DRIFT_EXIT", "1")
    monkeypatch.setattr(
        get_credentials_loader(),
        "get_ntfy_config",
        lambda: {"url": "https://ntfy.test", "topic": "t", "user": "u", "password": "p"},
    )
    monkeypatch.setattr(listener_mod, "resolve_tag_filter", lambda *_a, **_k: "empirica.test.instance")
    calls = []

    def catchup(*_a, **_k):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError(
                "boom"
            )  # the post-drop catch-up of the first stream fails: the loop survives it, and it is counted
        return 0

    monkeypatch.setattr(listener_mod, "_emit_catchup_events", catchup)
    streams = iter(
        [
            _Proc(["HTTP/1.1 200 OK\n", "\n", json.dumps({"event": "message", "id": "abcdefghijklmnop"}) + "\n"]),
            _Proc(["HTTP/1.1 200 OK\n", "\n"]),
        ]
    )
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise ListenerStopped("test")

    err = io.StringIO()
    run_listener(
        "cortex",
        output_stream=io.StringIO(),
        err_stream=err,
        _stream_factory=lambda _u, _h: next(streams),
        _sleep=fake_sleep,
    )
    final = [ln for ln in err.getvalue().splitlines() if ln.startswith("listener: stats (stop)")]
    assert len(final) == 1, err.getvalue()[-800:]
    line = final[0]
    assert "connects=2" in line and "events=1" in line
    assert "catchup_failures=1" in line
    assert f"catchups={len(calls)}" in line and len(calls) >= 3
