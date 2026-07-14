"""Serve daemon version-drift self-heal (mesh-support prop_rxhboler/prop_wuevnxev).

The daemon serves stale code after a pip/editable upgrade until restarted. It
now (1) always SURFACES drift on GET /health, and (2) self-exits ONLY when
supervised — serve is often standalone, so a blind self-exit would kill an
unsupervised daemon permanently. These tests mock the version compare + os.kill
so they run offline and never actually signal the test process.
"""

from __future__ import annotations

import importlib.metadata
import os
import signal
import threading

import empirica.api.serve_app as sa
import empirica.core.version_drift as vd


# ── pure compare ─────────────────────────────────────────────────────
def test_version_drift_none_when_matched(monkeypatch):
    from empirica import __version__ as real

    monkeypatch.setattr(importlib.metadata, "version", lambda _n: real)
    assert vd.version_drift() is None


def test_version_drift_tuple_when_mismatched(monkeypatch):
    from empirica import __version__ as real

    monkeypatch.setattr(importlib.metadata, "version", lambda _n: "0.0.0-test")
    assert vd.version_drift() == (real, "0.0.0-test")


def test_version_drift_none_on_error(monkeypatch):
    def boom(_n):
        raise importlib.metadata.PackageNotFoundError("empirica")

    monkeypatch.setattr(importlib.metadata, "version", boom)
    assert vd.version_drift() is None


# ── supervised-exit guard ────────────────────────────────────────────
def test_exit_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EMPIRICA_SERVE_DRIFT_EXIT", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert sa._serve_drift_exit_enabled() is False


def test_exit_enabled_explicit_optin(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setenv("EMPIRICA_SERVE_DRIFT_EXIT", "1")
    assert sa._serve_drift_exit_enabled() is True


def test_exit_enabled_under_systemd(monkeypatch):
    monkeypatch.delenv("EMPIRICA_SERVE_DRIFT_EXIT", raising=False)
    monkeypatch.setenv("INVOCATION_ID", "abc123")  # systemd sets this
    assert sa._serve_drift_exit_enabled() is True


# ── watch loop decision ──────────────────────────────────────────────
def test_watch_loop_self_exits_when_supervised(monkeypatch):
    monkeypatch.setattr(vd, "version_drift", lambda: ("1.0.0", "1.0.1"))
    monkeypatch.setattr(sa, "_serve_drift_exit_enabled", lambda: True)
    killed = {}
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.update(pid=pid, sig=sig))

    sa._drift_watch_loop(0.001, threading.Event())
    assert killed == {"pid": os.getpid(), "sig": signal.SIGTERM}


def test_watch_loop_does_not_exit_when_unsupervised(monkeypatch):
    monkeypatch.setattr(vd, "version_drift", lambda: ("1.0.0", "1.0.1"))
    monkeypatch.setattr(sa, "_serve_drift_exit_enabled", lambda: False)
    killed = {"n": 0}
    monkeypatch.setattr(os, "kill", lambda *a: killed.__setitem__("n", killed["n"] + 1))

    sa._drift_watch_loop(0.001, threading.Event())  # returns after surfacing
    assert killed["n"] == 0


def test_watch_loop_stops_on_event(monkeypatch):
    # stop set before entry → loop never checks drift, returns immediately.
    monkeypatch.setattr(vd, "version_drift", lambda: ("1.0.0", "1.0.1"))
    monkeypatch.setattr(os, "kill", lambda *a: (_ for _ in ()).throw(AssertionError("must not exit")))
    stop = threading.Event()
    stop.set()
    sa._drift_watch_loop(60.0, stop)  # returns fast, no kill


# ── /health surfaces drift ───────────────────────────────────────────
def test_health_surfaces_drift(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("EMPIRICA_SERVE_DRIFT_CHECK_SEC", "0")  # disable bg watcher
    monkeypatch.setattr(vd, "version_drift", lambda: ("1.12.21", "1.12.22"))
    with TestClient(sa.create_serve_app()) as client:
        body = client.get("/api/v1/health").json()
    assert body["version_drift"] == {"in_process": "1.12.21", "installed": "1.12.22"}


def test_health_null_drift_when_matched(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("EMPIRICA_SERVE_DRIFT_CHECK_SEC", "0")
    monkeypatch.setattr(vd, "version_drift", lambda: None)
    with TestClient(sa.create_serve_app()) as client:
        body = client.get("/api/v1/health").json()
    assert body["version_drift"] is None
