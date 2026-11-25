"""An expired Cortex sign-in is named where the person looks, not only in the journal (goal 20e5ff77, prop_bgchkdc6).

Field report: the token refresher was down, the token expired, and four listeners restarted every few minutes (one about
1080 times). The listener's stderr named the cause, but its health marker said only canonical_tag_unresolved and doctor
said 'installed but inactive' with a reinstall hint, which fixes nothing.
"""

from __future__ import annotations

import io
import json

from empirica.core.loop_scheduler import listener

EXPIRED = "oauth token expired; its refresh belongs to the serve service, which has not renewed it (is `empirica serve` running?)"


def _refuse_with(monkeypatch, reason):
    import empirica.core.mesh_endpoint as me

    monkeypatch.setattr(me, "mesh_bearer", lambda *a, **k: {"url": None, "bearer": None, "reason": reason})


def test_refusal_reason_is_kept_and_expiry_gets_a_plain_line(monkeypatch):
    _refuse_with(monkeypatch, EXPIRED)
    err = io.StringIO()
    assert listener.resolve_tag_filter("empirica", err, sleep=lambda s: None) is None
    assert listener.last_tag_refusal() == EXPIRED
    assert "Cortex sign-in expired: run `empirica auth login`" in err.getvalue()


def test_other_refusals_get_no_signin_line(monkeypatch):
    _refuse_with(monkeypatch, "no cortex credential configured (no OAuth token, no api_key)")
    err = io.StringIO()
    listener.resolve_tag_filter("empirica", err, sleep=lambda s: None)
    assert "sign-in expired" not in err.getvalue()


def test_doctor_names_a_degraded_listener_cause_before_service_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    proj = tmp_path / "proj" / ".empirica"
    proj.mkdir(parents=True)
    (proj / "project.yaml").write_text("ai_id: demo\n")
    from datetime import datetime, timezone

    (tmp_path / ".empirica" / "listener_health_demo.json").write_text(
        json.dumps(
            {
                "status": "degraded",
                "reason": f"canonical_tag_unresolved: {EXPIRED}",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    from empirica.cli.command_handlers import doctor

    check = doctor.check_listener_service(tmp_path / "proj")
    assert check.status == doctor.WARN
    assert "oauth token expired" in check.detail
    assert "empirica auth login" in (check.hint or "")
