"""doctor reports notify-backend failures; the cockpit banner is no longer the only consumer.

The judgement "a backend failed within the last hour" lived in the cockpit
view module with one caller: the banner. Nothing without a screen could ask
it. It now lives beside the audit readers (`failure_within_window`) and doctor
asks it too. All state under tmp_path.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from empirica.cli.command_handlers import doctor as doctor_mod
from empirica.core.notify import audit as audit_mod


def _row(ok=True, fell_back=False, age_seconds=0, backend="ntfy", detail=""):
    ts = datetime.now(tz=timezone.utc) - timedelta(seconds=age_seconds)
    return json.dumps(
        {
            "ts": ts.isoformat(),
            "source": "test",
            "severity": "info",
            "topic": "t",
            "resolved_backend": backend,
            "fell_back": fell_back,
            "fallback_reason": None,
            "ok": ok,
            "response_code": 200 if ok else 500,
            "detail": detail,
            "project_id": None,
        }
    )


@pytest.fixture
def audit(tmp_path, monkeypatch):
    path = tmp_path / "notify-dispatcher.jsonl"
    monkeypatch.setattr(audit_mod, "AUDIT_PATH", path)
    return path


def test_no_audit_is_skip_not_pass(audit):
    c = doctor_mod.check_notify_dispatcher()
    assert c.status == doctor_mod.SKIP
    assert "nothing emitted" in c.detail


def test_clean_audit_passes(audit):
    audit.write_text(_row() + "\n" + _row() + "\n")
    c = doctor_mod.check_notify_dispatcher()
    assert c.status == doctor_mod.PASS
    assert "2 emission(s)" in c.detail
    assert c.data["fell_back_count_24h"] == 0


def test_recent_failure_warns_with_backend_and_detail(audit):
    audit.write_text(_row() + "\n" + _row(ok=False, age_seconds=120, backend="slack", detail="401 bad token") + "\n")
    c = doctor_mod.check_notify_dispatcher()
    assert c.status == doctor_mod.WARN
    assert "backend slack failed" in c.detail
    assert "401 bad token" in c.detail
    assert c.data["recent_failure"]["age_seconds"] >= 120


def test_old_failure_outside_the_window_does_not_warn(audit):
    audit.write_text(_row(ok=False, age_seconds=2 * 3600) + "\n" + _row() + "\n")
    c = doctor_mod.check_notify_dispatcher()
    assert c.status == doctor_mod.PASS


def test_fallbacks_warn_even_when_every_emit_succeeded(audit):
    audit.write_text(_row(fell_back=True) + "\n" + _row() + "\n")
    c = doctor_mod.check_notify_dispatcher()
    assert c.status == doctor_mod.WARN
    assert "1 of 2 emission(s) in 24h fell back" in c.detail


def test_unreadable_audit_warns_not_passes(audit, monkeypatch):
    audit.write_text(_row() + "\n")
    real_open = open

    def denied(path, *a, **k):
        if str(path) == str(audit):
            raise PermissionError("denied")
        return real_open(path, *a, **k)

    import builtins

    monkeypatch.setattr(builtins, "open", denied)
    c = doctor_mod.check_notify_dispatcher()
    assert c.status == doctor_mod.WARN
    assert "cannot be read" in c.detail


def test_banner_and_doctor_share_one_window_and_one_judgement():
    """The view no longer owns the rule; it imports the audit module's."""
    from empirica.core.cockpit import notify_dispatcher_view as view

    assert view.FAILURE_BANNER_WINDOW_SECONDS == audit_mod.FAILURE_WINDOW_SECONDS
    assert view.failure_within_window is audit_mod.failure_within_window
    now = datetime.now(tz=timezone.utc)
    row = {"ts": (now - timedelta(seconds=30)).isoformat(), "ok": False}
    assert audit_mod.failure_within_window(row, now)["age_seconds"] == 30
    assert audit_mod.failure_within_window(row, now + timedelta(hours=2)) is None
    assert audit_mod.failure_within_window({"ts": "not-a-date"}, now) is None
    assert audit_mod.failure_within_window(None, now) is None
