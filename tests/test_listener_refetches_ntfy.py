"""A seat whose ntfy account appears after sign-in recovers without a new sign-in (goal 489c7fa0, prop_z2qcr27 #5).

auth login fetches the ntfy credentials once. When Cortex answered 404 (no notification account yet), nothing fetched again,
so every listener exited 2 and restarted forever until the person signed in again. The listener now fetches them itself,
at most once per interval per machine: the endpoint allows 5 calls an hour and the supervisor restarts every few minutes.
"""

from __future__ import annotations

import io
import os
import time

from empirica.core.auth import cortex_oauth


def _signed_in(monkeypatch):
    monkeypatch.setattr(
        cortex_oauth,
        "cortex_bearer",
        lambda *a, **k: {"url": "https://cortex.test", "bearer": "tok", "source": "oauth"},
    )


def test_fetches_when_not_attempted_recently(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    calls = []
    monkeypatch.setattr(
        cortex_oauth,
        "_fetch_ntfy_credentials",
        lambda loader, url, tok, **k: calls.append(url) or {"written": True, "reason": None},
    )
    stamp = tmp_path / "stamp"
    out = cortex_oauth.refetch_ntfy_credentials(stamp_path=stamp)
    assert out["written"] is True and calls == ["https://cortex.test"] and stamp.exists()


def test_throttled_within_the_interval(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    monkeypatch.setattr(
        cortex_oauth, "_fetch_ntfy_credentials", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called"))
    )
    stamp = tmp_path / "stamp"
    stamp.write_text("")
    out = cortex_oauth.refetch_ntfy_credentials(stamp_path=stamp, min_interval=1800)
    assert out["written"] is False and "throttled" in out["reason"]


def test_an_old_attempt_does_not_throttle(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    monkeypatch.setattr(cortex_oauth, "_fetch_ntfy_credentials", lambda *a, **k: {"written": False, "reason": "404"})
    stamp = tmp_path / "stamp"
    stamp.write_text("")
    old = time.time() - 3600
    os.utime(stamp, (old, old))
    assert cortex_oauth.refetch_ntfy_credentials(stamp_path=stamp, min_interval=1800)["reason"] == "404"


def test_no_sign_in_says_so_without_calling(tmp_path, monkeypatch):
    monkeypatch.setattr(cortex_oauth, "cortex_bearer", lambda *a, **k: {"url": None, "bearer": None, "source": "none"})
    out = cortex_oauth.refetch_ntfy_credentials(stamp_path=tmp_path / "stamp")
    assert out["written"] is False and "sign" in out["reason"]


def test_listener_uses_refetched_credentials(monkeypatch):
    from empirica.core.loop_scheduler import listener

    fresh = {"url": "https://ntfy.test", "topic": "t", "token": "tk_new"}
    err = io.StringIO()
    got = listener._ntfy_after_refetch(err, refetch=lambda: {"written": True, "reason": None}, reload=lambda: fresh)
    assert got == fresh


def test_listener_reports_why_it_could_not_refetch():
    from empirica.core.loop_scheduler import listener

    err = io.StringIO()
    got = listener._ntfy_after_refetch(
        err, refetch=lambda: {"written": False, "reason": "throttled: tried 3 min ago"}, reload=dict
    )
    assert got is None and "throttled" in err.getvalue()
