"""A held stream that connected and then went quiet is a drop, not an auth failure.

`connected_ok` became true only when a `message` event arrived. A stream that opened (HTTP 200), carried keepalives for hours and was then closed by
the server or a proxy ended with `connected_ok = False`, and the listener took the auth-failure branch: catch-up, then a 300 s sleep with no curl child
running. In that window `mesh status` reads "curl subscription dead - cortex bridge broken" while the parent is healthy, and a push waits for the next
catch-up. mesh-support measured 163 "stream ended" lines in one log, 126 of them `connected_ok=False`.

A 2xx response means the connection was established. Only a response that was never a 2xx (no headers, or 4xx/5xx) is a failure to connect.
"""

from __future__ import annotations

import io

import pytest

import empirica.core.loop_scheduler.listener as listener_mod
from empirica.core.loop_scheduler.listener import ListenerStopped, run_listener

AUTH_FAIL = listener_mod._AUTH_FAIL_BACKOFF_SEC


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


OK_HEADERS = ["HTTP/1.1 200 OK\n", "content-type: application/json\n", "\n"]


def _first_sleep(monkeypatch, lines):
    from empirica.config.credentials_loader import get_credentials_loader

    monkeypatch.setenv("EMPIRICA_LISTENER_NO_DRIFT_EXIT", "1")
    monkeypatch.setattr(
        get_credentials_loader(),
        "get_ntfy_config",
        lambda: {"url": "https://ntfy.test", "topic": "t", "user": "u", "password": "p"},
    )
    # The box has no cortex roster and the listener refuses to run without a canonical tag (tests/test_listener_never_subscribes_as_basename.py
    # pins the refusal), so give it one, as tests/test_loop_listener.py does.
    monkeypatch.setattr(listener_mod, "resolve_tag_filter", lambda *_a, **_k: "empirica.test.instance")
    monkeypatch.setattr(listener_mod, "_emit_catchup_events", lambda *_a, **_k: 0)
    sleeps: list[float] = []

    def fake_sleep(s):
        sleeps.append(s)
        raise ListenerStopped("test")

    err = io.StringIO()
    rc = run_listener(
        "cortex",
        output_stream=io.StringIO(),
        err_stream=err,
        _stream_factory=lambda _url, _headers: _Proc(lines),
        _sleep=fake_sleep,
    )
    assert sleeps, f"the listener never reached a post-drop sleep (rc={rc}): {err.getvalue()[-600:]}"
    return sleeps[0]


def test_a_stream_that_connected_and_carried_only_keepalives_gets_the_short_backoff(monkeypatch):
    keepalives = ['{"event": "open"}\n', '{"event": "keepalive"}\n', '{"event": "keepalive"}\n']
    assert _first_sleep(monkeypatch, OK_HEADERS + keepalives) < AUTH_FAIL


def test_an_immediately_closed_200_stream_also_gets_the_short_backoff(monkeypatch):
    assert _first_sleep(monkeypatch, OK_HEADERS) < AUTH_FAIL


@pytest.mark.parametrize("status", ["401 Unauthorized", "403 Forbidden", "500 Internal Server Error"])
def test_a_4xx_or_5xx_response_keeps_the_long_backoff(monkeypatch, status):
    """Positive control: the auth-failure branch still exists and is still reached by a response that was never a 2xx."""
    assert _first_sleep(monkeypatch, [f"HTTP/1.1 {status}\n", "\n"]) == AUTH_FAIL


def test_a_stream_that_never_produced_headers_keeps_the_long_backoff(monkeypatch):
    assert _first_sleep(monkeypatch, []) == AUTH_FAIL
