"""Tests for HeartbeatEmitter (prop_5rlp6tkclvhhhdqjs5nhcmnvni).

Heartbeat-emit lives in the persistent OS service (empirica loop listen
body), not in the in-session Monitor — per the option-b answer to Q4
on prop_hs55f5px. Reasons + tradeoffs in heartbeat.py module docstring.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from empirica.core.loop_scheduler import listener as listener_mod
from empirica.core.loop_scheduler.heartbeat import (
    _HEARTBEAT_ENDPOINT_PATH,
    HeartbeatEmitter,
)


def _record_post():
    """Fake _post_fn that records every call. Returns (recorder, fn)."""
    calls = []

    def fn(url, body, headers, timeout):
        calls.append(
            {
                "url": url,
                "body": json.loads(body.decode("utf-8")) if body else None,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return 200

    return calls, fn


def _creds_fn(url="https://cortex.example.com", key="ctx_test"):
    return lambda: (url, key)


# ─── emit_once shape ──────────────────────────────────────────────────


def test_emit_once_posts_correct_shape():
    calls, post = _record_post()
    e = HeartbeatEmitter(
        ai_id="empirica",
        instance_id="testhost",
        capabilities=["chrome"],
        _post_fn=post,
        _resolve_creds_fn=_creds_fn(),
    )

    status = e.emit_once()

    assert status == 200
    assert len(calls) == 1
    assert calls[0]["url"] == "https://cortex.example.com" + _HEARTBEAT_ENDPOINT_PATH
    assert calls[0]["body"] == {
        "ai_id": "empirica",
        "instance_id": "testhost",
        "capabilities": ["chrome"],
    }
    assert calls[0]["headers"]["Authorization"] == "Bearer ctx_test"
    assert calls[0]["headers"]["Content-Type"] == "application/json"


def test_emit_once_strips_trailing_slash_from_url():
    calls, post = _record_post()
    e = HeartbeatEmitter(
        ai_id="cortex",
        _post_fn=post,
        _resolve_creds_fn=_creds_fn(url="https://cortex.example.com/"),
    )
    e.emit_once()
    assert calls[0]["url"] == "https://cortex.example.com" + _HEARTBEAT_ENDPOINT_PATH


def test_emit_once_returns_zero_when_no_creds():
    calls, post = _record_post()
    e = HeartbeatEmitter(
        ai_id="empirica",
        _post_fn=post,
        _resolve_creds_fn=lambda: (None, None),
    )
    status = e.emit_once()
    assert status == 0  # SKIP — no creds
    assert len(calls) == 0  # never POSTed


def test_emit_once_returns_zero_when_partial_creds():
    """URL but no key, or key but no URL — both SKIP."""
    e1 = HeartbeatEmitter("x", _post_fn=lambda *a: 200, _resolve_creds_fn=lambda: ("https://c.example", None))
    e2 = HeartbeatEmitter("x", _post_fn=lambda *a: 200, _resolve_creds_fn=lambda: (None, "ctx_test"))
    assert e1.emit_once() == 0
    assert e2.emit_once() == 0


def test_emit_once_defaults_instance_id_to_hostname():
    """No instance_id passed → falls back to socket.gethostname()."""
    import socket

    calls, post = _record_post()
    e = HeartbeatEmitter(
        ai_id="empirica",
        _post_fn=post,
        _resolve_creds_fn=_creds_fn(),
    )
    e.emit_once()
    assert calls[0]["body"]["instance_id"] == socket.gethostname()


def test_emit_once_capabilities_defaults_to_empty_list():
    calls, post = _record_post()
    e = HeartbeatEmitter(
        ai_id="empirica",
        _post_fn=post,
        _resolve_creds_fn=_creds_fn(),
    )
    e.emit_once()
    assert calls[0]["body"]["capabilities"] == []


# ─── Thread lifecycle ─────────────────────────────────────────────────


def test_start_spawns_thread_and_stop_joins():
    calls, post = _record_post()
    e = HeartbeatEmitter(
        ai_id="empirica",
        interval_sec=0.05,  # fast cadence for test
        _post_fn=post,
        _resolve_creds_fn=_creds_fn(),
    )
    e.start()
    time.sleep(0.15)  # allow 2-3 emits
    e.stop(timeout=1.0)
    # At least one emit; thread joined
    assert len(calls) >= 1
    assert e._thread is None


def test_start_is_idempotent():
    """Calling start() twice should not spawn a second thread."""
    e = HeartbeatEmitter(
        ai_id="empirica",
        interval_sec=10.0,
        _post_fn=lambda *a: 200,
        _resolve_creds_fn=_creds_fn(),
    )
    e.start()
    t1 = e._thread
    e.start()  # idempotent
    t2 = e._thread
    assert t1 is t2
    e.stop()


def test_stop_is_idempotent_and_safe_when_never_started():
    """stop() on never-started emitter should not raise."""
    e = HeartbeatEmitter(ai_id="empirica")
    e.stop()
    e.stop()  # double-stop
    assert e._thread is None


def test_stop_interrupts_long_interval():
    """Even with interval=60s, stop() should return quickly via threading.Event."""
    e = HeartbeatEmitter(
        ai_id="empirica",
        interval_sec=60.0,  # long
        _post_fn=lambda *a: 200,
        _resolve_creds_fn=_creds_fn(),
    )
    e.start()
    t0 = time.monotonic()
    e.stop(timeout=2.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5  # interrupted, didn't wait full 60s


def test_emit_failures_dont_crash_loop():
    """Post raises every time → loop should keep ticking, not crash the thread."""
    call_count = [0]

    def failing_post(*args):
        call_count[0] += 1
        raise RuntimeError("simulated network failure")

    e = HeartbeatEmitter(
        ai_id="empirica",
        interval_sec=0.05,
        _post_fn=failing_post,
        _resolve_creds_fn=_creds_fn(),
    )
    e.start()
    time.sleep(0.2)
    # Thread is still alive — didn't crash
    assert e._thread is not None
    assert e._thread.is_alive()
    e.stop(timeout=1.0)
    # Multiple emits attempted despite failures
    assert call_count[0] >= 2


def test_cortex_returns_4xx_does_not_crash_loop():
    """HTTP 401/500 returns from _post_fn → loop continues."""

    def post_401(*args):
        return 401

    e = HeartbeatEmitter(
        ai_id="empirica",
        interval_sec=0.05,
        _post_fn=post_401,
        _resolve_creds_fn=_creds_fn(),
    )
    e.start()
    time.sleep(0.15)
    assert e._thread.is_alive()
    e.stop(timeout=1.0)


# ─── Run_listener wiring ──────────────────────────────────────────────


def test_run_listener_starts_and_stops_heartbeat(monkeypatch, tmp_path):
    """End-to-end: when listener starts, heartbeat starts; when it stops, heartbeat stops."""
    from empirica.core.loop_scheduler import listener as listener_mod

    started = threading.Event()
    stopped = threading.Event()

    class FakeEmitter:
        def __init__(self, ai_id, **kwargs):
            self.ai_id = ai_id

        def start(self):
            started.set()

        def stop(self, timeout=2.0):
            stopped.set()

    # Patch HeartbeatEmitter at import site inside listener
    import empirica.core.loop_scheduler.heartbeat as hb_mod

    monkeypatch.setattr(hb_mod, "HeartbeatEmitter", FakeEmitter)

    # Patch credentials_loader so we don't need real ntfy creds
    class FakeLoader:
        def get_ntfy_config(self):
            return {
                "url": "https://ntfy.example.com",
                "topic": "test",
                "user": None,
                "password": None,
                "token": "tk_test",
            }

    monkeypatch.setattr(
        "empirica.config.credentials_loader.get_credentials_loader",
        lambda: FakeLoader(),
    )

    # Stream factory that immediately raises ListenerStopped so we exit fast
    def factory(url, headers):
        raise listener_mod.ListenerStopped("test exit")

    err_log = tmp_path / "err.log"
    with err_log.open("w") as err_stream:
        rc = listener_mod.run_listener(
            instance_id="empirica",
            _stream_factory=factory,
            _sleep=lambda x: None,
            _initial_catchup=False,
            err_stream=err_stream,
        )

    assert rc == 0
    assert started.is_set(), "heartbeat.start() should be called by run_listener"
    assert stopped.is_set(), "heartbeat.stop() should be called in finally"


def test_default_resolve_creds_handles_missing_loader(monkeypatch):
    """If credentials_loader import fails, _default_resolve_creds returns (None, None)."""
    import empirica.core.loop_scheduler.heartbeat as hb_mod

    def boom():
        raise RuntimeError("loader not available")

    monkeypatch.setattr(
        "empirica.config.credentials_loader.get_credentials_loader",
        boom,
    )
    url, key = hb_mod._default_resolve_creds()
    assert url is None
    assert key is None


def test_default_post_returns_neg_one_on_network_error():
    """_default_post should return -1 (not raise) on network failure."""
    from empirica.core.loop_scheduler.heartbeat import _default_post

    # Use a definitely-unreachable URL
    status = _default_post(
        "http://127.0.0.1:1/heartbeat",
        b"{}",
        {"Authorization": "Bearer x", "Content-Type": "application/json"},
        timeout=0.5,
    )
    assert status == -1


@pytest.fixture(autouse=True)
def _canonical_tag_resolves(monkeypatch):
    """These tests are about the listener's other behaviour. The box has no cortex roster, and with no canonical tag the
    listener now (correctly) refuses to run, so give it one. The refusal itself is tested in
    tests/test_listener_never_subscribes_as_basename.py."""
    monkeypatch.setattr(listener_mod, "resolve_tag_filter", lambda *_a, **_k: "empirica.test.instance")
