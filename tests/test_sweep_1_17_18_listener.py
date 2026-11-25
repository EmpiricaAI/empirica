"""Findings from the 1.17.18 pre-publish sweep, listener area (goal c42e1294)."""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from empirica.core.auth import cortex_oauth
from empirica.core.loop_scheduler import listener
from empirica.core.loop_scheduler import persistent_listener as pl


@pytest.mark.parametrize(
    "reason, expired",
    [
        ("oauth token expired; its refresh belongs to the serve service", True),
        ("oauth token expired and no refresh token is stored", True),
        ("OAuth Token Expired", True),
        ("certificate has expired", False),  # a TLS error: auth login is the wrong fix
        ("not expired; token revoked", False),
        ("unexpired", False),
        ("no cortex credential configured", False),
    ],
)
def test_the_sign_in_expired_matcher_and_its_near_misses(reason, expired):
    assert listener.is_signin_expired(reason) is expired


def test_restart_after_signin_reports_only_what_was_restarted(tmp_path):
    (tmp_path / ".config/systemd/user").mkdir(parents=True)
    for ai in ("running", "stopped"):
        (tmp_path / f".config/systemd/user/empirica-listener-{ai}.service").write_text("")

    class Svc:
        def restart_if_running(self, ai_id):
            return ai_id == "running"

    out = pl.restart_after_signin(service=Svc(), home=tmp_path)
    assert out["restarted"] == ["running"]


def test_restart_if_running_is_false_for_an_inactive_unit(monkeypatch):
    import subprocess

    calls = []

    def fake(*args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 3 if args[0] == "is-active" else 0, "", "")

    monkeypatch.setattr(pl, "_systemctl", fake)
    svc = pl.PersistentListenerService.__new__(pl.PersistentListenerService)
    svc.backend = "systemd"
    assert svc.restart_if_running("empirica") is False
    assert not any(a[0] == "try-restart" for a in calls)


@pytest.mark.parametrize("reason", ["liveness_probe_stale_900s", "cortex_unreachable"])
def test_doctor_ignores_degraded_markers_that_are_not_a_refusal(tmp_path, monkeypatch, reason):
    from empirica.cli.command_handlers import doctor

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "listener_health_demo.json").write_text(
        json.dumps({"status": "degraded", "reason": reason, "ts": datetime.now(timezone.utc).isoformat()})
    )
    assert doctor._listener_degraded_reason("demo") is None


def test_doctor_hint_names_no_platform_tool(tmp_path, monkeypatch):
    from empirica.cli.command_handlers import doctor

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "listener_health_demo.json").write_text(
        json.dumps(
            {"status": "degraded", "reason": "topic_unresolved_403_guard", "ts": datetime.now(timezone.utc).isoformat()}
        )
    )
    proj = tmp_path / "p" / ".empirica"
    proj.mkdir(parents=True)
    (proj / "project.yaml").write_text("ai_id: demo\n")
    check = doctor.check_listener_service(tmp_path / "p")
    assert "refusing to subscribe" in check.detail and "journalctl" not in (check.hint or "")


def test_the_no_ntfy_credentials_exit_writes_a_degraded_marker(monkeypatch):
    degraded = []
    monkeypatch.setattr(listener, "resolve_tag_filter", lambda *a, **k: "empirica.david.x")
    monkeypatch.setattr(listener, "_emit_fail_heartbeat", lambda *a, **k: degraded.append(k.get("reason")))
    monkeypatch.setattr(listener, "_ntfy_after_refetch", lambda err, **k: None)
    monkeypatch.setenv("ORCHESTRATION_NTFY_TOPIC", "t")
    for module, name in (
        ("empirica.core.loop_scheduler.liveness_probe", "LivenessProbe"),
        ("empirica.core.loop_scheduler.heartbeat", "HeartbeatEmitter"),
        ("empirica.core.loop_scheduler.practitioner_heartbeat", "PractitionerHeartbeatEmitter"),
    ):
        monkeypatch.setattr(f"{module}.{name}", MagicMock())
    loader = MagicMock()
    loader.get_ntfy_config.return_value = {"url": "https://ntfy.example", "topic": "t"}
    with patch("empirica.config.credentials_loader.get_credentials_loader", return_value=loader):
        rc = listener.run_listener(
            "x", err_stream=io.StringIO(), _stream_factory=lambda *a, **k: pytest.fail("opened"), _sleep=lambda s: None,
            _initial_catchup=False,
        )  # fmt: skip
    assert rc == 2 and degraded and degraded[0].startswith("no_ntfy_credentials")


def test_concurrent_refetches_call_the_endpoint_once(tmp_path, monkeypatch):
    monkeypatch.setattr(cortex_oauth, "cortex_bearer", lambda *a, **k: {"url": "https://c", "bearer": "t"})
    calls = []
    monkeypatch.setattr(
        cortex_oauth, "_fetch_ntfy_credentials", lambda *a, **k: calls.append(1) or {"written": False, "reason": "404"}
    )
    stamp = tmp_path / "stamp"
    first = cortex_oauth.refetch_ntfy_credentials(stamp_path=stamp)
    second = cortex_oauth.refetch_ntfy_credentials(stamp_path=stamp)
    assert len(calls) == 1 and "throttled" in second["reason"] and first["reason"] == "404"


def test_a_stale_stamp_is_claimed_by_one_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(cortex_oauth, "cortex_bearer", lambda *a, **k: {"url": "https://c", "bearer": "t"})
    monkeypatch.setattr(cortex_oauth, "_fetch_ntfy_credentials", lambda *a, **k: {"written": False, "reason": "404"})
    stamp = tmp_path / "stamp"
    stamp.write_text("")
    os.utime(stamp, (0, 0))
    assert cortex_oauth.refetch_ntfy_credentials(stamp_path=stamp)["reason"] == "404"
    assert "throttled" in cortex_oauth.refetch_ntfy_credentials(stamp_path=stamp)["reason"]


def test_the_local_tail_command_matches_the_short_name_literally(monkeypatch, capsys):
    """The wake log carries the short name; a 3-form ai_id never matched, and a regex/shell character broke the grep."""
    import types

    from empirica.cli.command_handlers import cockpit_commands as cc

    monkeypatch.setattr("empirica.core.mesh_endpoint.mesh_bearer", lambda *a, **k: {"edition": "local"})
    assert cc._local_server_tail(types.SimpleNamespace(output="json"), "empirica.david.my-app") == 0
    cmd = json.loads(capsys.readouterr().out)["next_step"]["args"]["command"]
    assert "grep -F" in cmd and '"instance_id": "my-app"' in cmd and "empirica.david" not in cmd
