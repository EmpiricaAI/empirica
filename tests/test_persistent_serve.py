"""Tests for the persistent serve daemon service (persistent_serve.py).

Mirrors test_persistent_listener.py. The generic OS helpers (_systemctl,
is_systemd_available, ...) are imported INTO persistent_serve's namespace, so we
patch them there (the lookup site), not on persistent_listener.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from empirica.core.loop_scheduler.persistent_serve import (
    PersistentServeService,
    restart_serve_service_if_running,
)

_MOD = "empirica.core.loop_scheduler.persistent_serve"
_OK = subprocess.CompletedProcess([], 0, "", "")


# ─── Backend detection ──────────────────────────────────────────────────


def test_backend_unavailable_when_no_systemd_no_launchd():
    with (
        patch(f"{_MOD}.is_systemd_available", return_value=False),
        patch(f"{_MOD}.is_launchd_available", return_value=False),
    ):
        assert PersistentServeService().backend == "unavailable"


def test_backend_systemd_when_only_systemd():
    with (
        patch(f"{_MOD}.is_systemd_available", return_value=True),
        patch(f"{_MOD}.is_launchd_available", return_value=False),
    ):
        assert PersistentServeService().backend == "systemd"


def test_backend_launchd_on_darwin():
    with (
        patch(f"{_MOD}.is_launchd_available", return_value=True),
        patch(f"{_MOD}.sys") as mock_sys,
    ):
        mock_sys.platform = "darwin"
        assert PersistentServeService().backend == "launchd"


# ─── Install: systemd ───────────────────────────────────────────────────


def test_install_systemd_writes_supervised_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fake = MagicMock(return_value=_OK)
    with (
        patch(f"{_MOD}.is_systemd_available", return_value=True),
        patch(f"{_MOD}.is_launchd_available", return_value=False),
        patch(f"{_MOD}._systemctl", side_effect=lambda *a, **kw: fake(*a, **kw)),
    ):
        svc = PersistentServeService(empirica_bin="/home/u/.local/bin/empirica")
        unit = svc.install(tmp_path / "proj", port=8000, host="127.0.0.1")

    content = unit.read_text()
    # ExecStart runs the (editable-develop) empirica the caller passed
    assert "ExecStart=/home/u/.local/bin/empirica serve --port 8000 --host 127.0.0.1" in content
    # Circuit breaker present (the 1849-flap guard) — MUST be in [Unit]
    assert "StartLimitIntervalSec=60" in content
    assert "StartLimitBurst=5" in content
    # Reboot-restart
    assert "Restart=always" in content
    assert "WantedBy=default.target" in content
    # Project binding
    assert f"WorkingDirectory={tmp_path / 'proj'}" in content
    # daemon-reload + enable --now
    assert fake.call_count >= 2


def test_install_systemd_includes_port_reaper(tmp_path, monkeypatch):
    """The ExecStartPre reaper (kills a non-cgroup process on :port) must be
    present and port-parameterized — the hand-built unit's key safety."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch(f"{_MOD}.is_systemd_available", return_value=True),
        patch(f"{_MOD}.is_launchd_available", return_value=False),
        patch(f"{_MOD}._systemctl", return_value=_OK),
    ):
        unit = PersistentServeService(empirica_bin="empirica").install(tmp_path / "p", port=8123)
    content = unit.read_text()
    assert "ExecStartPre=" in content
    assert "[reaper]" in content
    assert "sport = :8123" in content  # port threaded into the reaper
    assert "empirica-serve.service" in content  # cgroup guard string


def test_install_systemd_adopts_not_clobbers_existing(tmp_path, monkeypatch):
    """A pre-existing unit (e.g. hand-built) is backed up to <unit>.bak before
    overwrite so it stays recoverable."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    existing = unit_dir / "empirica-serve.service"
    existing.write_text("# hand-built unit with a custom reaper\n")

    with (
        patch(f"{_MOD}.is_systemd_available", return_value=True),
        patch(f"{_MOD}.is_launchd_available", return_value=False),
        patch(f"{_MOD}._systemctl", return_value=_OK),
    ):
        PersistentServeService(empirica_bin="empirica").install(tmp_path / "p")

    backup = unit_dir / "empirica-serve.service.bak"
    assert backup.exists()
    assert "hand-built unit with a custom reaper" in backup.read_text()
    # The new unit replaced the old content
    assert "ExecStart=" in existing.read_text()


# ─── Install: launchd ───────────────────────────────────────────────────


def test_install_launchd_sets_drift_exit_signal(tmp_path, monkeypatch):
    """launchd does not set INVOCATION_ID, so the supervised drift-exit signal is
    supplied via EMPIRICA_SERVE_DRIFT_EXIT=1. The systemd-only reaper is absent."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch(f"{_MOD}.is_systemd_available", return_value=False),
        patch(f"{_MOD}.is_launchd_available", return_value=True),
        patch(f"{_MOD}.sys") as mock_sys,
        patch(f"{_MOD}._launchctl", return_value=_OK),
    ):
        mock_sys.platform = "darwin"
        plist = PersistentServeService(empirica_bin="/p/empirica").install(tmp_path / "proj", port=8000)
    content = plist.read_text()
    assert "<string>com.empirica.serve</string>" in content
    assert "EMPIRICA_SERVE_DRIFT_EXIT" in content
    assert "<key>KeepAlive</key>" in content
    assert "<key>RunAtLoad</key>" in content
    assert f"<string>{tmp_path / 'proj'}</string>" in content  # WorkingDirectory
    assert "[reaper]" not in content  # reaper is systemd-only


# ─── Restart-on-update helper ───────────────────────────────────────────


def test_restart_noop_when_service_inactive():
    """The update hook is a safe no-op where the service isn't active."""
    with (
        patch(f"{_MOD}.is_systemd_available", return_value=True),
        patch(f"{_MOD}.is_launchd_available", return_value=False),
        patch(f"{_MOD}._systemctl") as sc,
    ):
        # is-active returns non-'active' → status().active is False
        sc.return_value = subprocess.CompletedProcess([], 0, "inactive", "")
        assert restart_serve_service_if_running() is False


def test_restart_issues_systemctl_restart_when_active(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "empirica-serve.service").write_text("[Service]\n")
    calls = []

    def fake_systemctl(*args, **kw):
        calls.append(args)
        # is-active → active; restart → success
        return subprocess.CompletedProcess([], 0, "active", "")

    with (
        patch(f"{_MOD}.is_systemd_available", return_value=True),
        patch(f"{_MOD}.is_launchd_available", return_value=False),
        patch(f"{_MOD}._systemctl", side_effect=fake_systemctl),
    ):
        assert restart_serve_service_if_running() is True
    assert any("restart" in a for a in calls)


# ─── Uninstall ──────────────────────────────────────────────────────────


def test_uninstall_noop_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch(f"{_MOD}.is_systemd_available", return_value=True),
        patch(f"{_MOD}.is_launchd_available", return_value=False),
        patch(f"{_MOD}._systemctl", return_value=_OK),
    ):
        assert PersistentServeService().uninstall() is False
