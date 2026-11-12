"""Tests for the persistent listener service install (prop_flrtxxn32japbazq).

Covers:
  - OS detection / backend resolution
  - Systemd unit template rendering
  - Launchd plist template rendering
  - install / uninstall / status idempotency
  - is_listener_running fail-safe (always returns bool, never raises)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from empirica.core.loop_scheduler.persistent_listener import (
    ListenerServiceUnavailable,
    PersistentListenerService,
    _safe,
    _unit_name,
    is_listener_running,
)

# ─── Helper sanitization ────────────────────────────────────────────────


def test_safe_sanitizes_special_chars():
    assert _safe("cortex") == "cortex"
    assert _safe("empirica-workspace") == "empirica-workspace"
    assert _safe("foo/bar") == "foo-bar"
    assert _safe("foo bar") == "foo-bar"
    assert _safe("") == "default"


def test_unit_name_format():
    assert _unit_name("cortex") == "empirica-listener-cortex"
    assert _unit_name("foo/bar") == "empirica-listener-foo-bar"


# ─── Backend detection ──────────────────────────────────────────────────


def test_backend_unavailable_when_no_systemd_no_launchd():
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
    ):
        service = PersistentListenerService()
    assert service.backend == "unavailable"


def test_backend_systemd_when_only_systemd_available():
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
    ):
        service = PersistentListenerService()
    assert service.backend == "systemd"


def test_backend_launchd_when_only_launchd_available():
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.sys") as mock_sys,
    ):
        mock_sys.platform = "darwin"
        service = PersistentListenerService()
    assert service.backend == "launchd"


def test_empirica_bin_resolves_to_absolute_path():
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.shutil.which", return_value="/usr/local/bin/empirica"),
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
    ):
        service = PersistentListenerService()
    assert service.empirica_bin == "/usr/local/bin/empirica"


def test_empirica_bin_explicit_override():
    with patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True):
        service = PersistentListenerService(empirica_bin="/custom/path/empirica")
    assert service.empirica_bin == "/custom/path/empirica"


# ─── Path resolution ────────────────────────────────────────────────────


def test_unit_path_systemd(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
    path = service.unit_path("cortex")
    assert path is not None
    assert path.name == "empirica-listener-cortex.service"
    assert ".config/systemd/user" in str(path)


def test_unit_path_unavailable_returns_none():
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
    assert service.unit_path("cortex") is None


# ─── Install ────────────────────────────────────────────────────────────


def test_install_raises_when_unavailable():
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
    import pytest

    with pytest.raises(ListenerServiceUnavailable):
        service.install("cortex")


def test_install_systemd_writes_unit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fake_run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch(
            "empirica.core.loop_scheduler.persistent_listener._systemctl",
            side_effect=lambda *a, **kw: fake_run(*a, **kw),
        ),
    ):
        service = PersistentListenerService(empirica_bin="/path/to/empirica")
        unit_file = service.install("cortex")

    assert unit_file.exists()
    content = unit_file.read_text()
    assert "Description=Empirica persistent listener — cortex" in content
    assert "ExecStart=/path/to/empirica loop listen --instance cortex" in content
    assert "Restart=always" in content
    # Systemctl was called: daemon-reload + enable --now
    assert fake_run.call_count >= 2


def test_install_systemd_log_path_in_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch(
            "empirica.core.loop_scheduler.persistent_listener._systemctl",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
        unit_file = service.install("cortex")
    content = unit_file.read_text()
    assert "/.empirica/logs/listener-cortex.log" in content


def test_install_launchd_writes_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.sys") as mock_sys,
        patch(
            "empirica.core.loop_scheduler.persistent_listener._launchctl",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ),
    ):
        mock_sys.platform = "darwin"
        service = PersistentListenerService(empirica_bin="/path/to/empirica")
        plist_file = service.install("cortex")

    assert plist_file.exists()
    content = plist_file.read_text()
    assert "<key>Label</key>" in content
    assert "<string>com.empirica.listener.cortex</string>" in content
    assert "<string>/path/to/empirica</string>" in content
    assert "loop" in content
    assert "<key>KeepAlive</key>" in content
    assert "<key>RunAtLoad</key>" in content


# ─── Legacy stripped-form self-heal ─────────────────────────────────────
#
# Pre-strict-canonical convention: InstanceResolver.ai_id() stripped the
# `empirica-` prefix, so `empirica-autonomy` resolved to `autonomy` and
# the systemd unit was `empirica-listener-autonomy.service`. After
# f7cd0433a the prefix is kept; new installs create
# `empirica-listener-empirica-autonomy.service`. The install path now
# self-heals: it removes any pre-canonical stripped-form unit before
# installing the canonical one so the host doesn't carry two
# subscribers (which causes ntfy double-delivery — autonomy practitioner
# hit this 2026-06-08).


def test_legacy_short_form_derives_stripped_basename():
    assert PersistentListenerService._legacy_short_form("empirica-autonomy") == "autonomy"
    assert PersistentListenerService._legacy_short_form("empirica-cortex") == "cortex"
    assert PersistentListenerService._legacy_short_form("empirica-mesh-support") == "mesh-support"


def test_legacy_short_form_returns_none_for_root_and_non_empirica():
    # Root practice basename `empirica` has no prefix to strip
    assert PersistentListenerService._legacy_short_form("empirica") is None
    # Non-empirica project: no stripped form
    assert PersistentListenerService._legacy_short_form("myproject") is None
    # Edge case: literal `empirica-` would strip to empty
    assert PersistentListenerService._legacy_short_form("empirica-") is None


def test_install_systemd_purges_legacy_stripped_unit(tmp_path, monkeypatch):
    """The autonomy practitioner's case: legacy `empirica-listener-autonomy.service`
    already on disk from pre-canonical install. Installing the new
    canonical `empirica-listener-empirica-autonomy.service` must purge
    the legacy one first so the host doesn't end up with two listeners."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    legacy_dir = tmp_path / ".config" / "systemd" / "user"
    legacy_dir.mkdir(parents=True)
    legacy_unit = legacy_dir / "empirica-listener-autonomy.service"
    legacy_unit.write_text("[Unit]\nDescription=legacy stripped-form\n")

    systemctl_calls = []

    def _fake_systemctl(*args, **_kw):
        systemctl_calls.append(args)
        return subprocess.CompletedProcess([], 0, "", "")

    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener._systemctl", side_effect=_fake_systemctl),
    ):
        service = PersistentListenerService(empirica_bin="/path/to/empirica")
        service.install("empirica-autonomy")

    # Legacy unit was disabled + removed before canonical install
    assert not legacy_unit.exists()
    disable_calls = [a for a in systemctl_calls if "disable" in a and "empirica-listener-autonomy.service" in a]
    assert disable_calls, f"legacy unit not disabled (calls: {systemctl_calls})"

    # Canonical unit now exists
    canonical_unit = legacy_dir / "empirica-listener-empirica-autonomy.service"
    assert canonical_unit.exists()


def test_install_systemd_skips_purge_when_no_legacy_unit(tmp_path, monkeypatch):
    """Fresh install (no legacy unit) — purge is a no-op, canonical install proceeds."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    systemctl_calls = []

    def _fake_systemctl(*args, **_kw):
        systemctl_calls.append(args)
        return subprocess.CompletedProcess([], 0, "", "")

    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener._systemctl", side_effect=_fake_systemctl),
    ):
        service = PersistentListenerService(empirica_bin="/path/to/empirica")
        service.install("empirica-cortex")

    # No legacy disable call — would target empirica-listener-cortex.service
    disable_calls = [a for a in systemctl_calls if "disable" in a and "empirica-listener-cortex.service" in a]
    assert disable_calls == [], "purge fired despite no legacy unit on disk"


def test_install_systemd_skips_purge_for_root_practice(tmp_path, monkeypatch):
    """Root practice (`empirica`) has no stripped form — no purge attempt."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    systemctl_calls = []

    def _fake_systemctl(*args, **_kw):
        systemctl_calls.append(args)
        return subprocess.CompletedProcess([], 0, "", "")

    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener._systemctl", side_effect=_fake_systemctl),
    ):
        service = PersistentListenerService(empirica_bin="/path/to/empirica")
        service.install("empirica")

    # Canonical install only — no disable on a stripped form
    disable_calls = [
        a for a in systemctl_calls if "disable" in a and a != ("disable", "--now", "empirica-listener-empirica.service")
    ]
    assert disable_calls == []


# ─── Uninstall ──────────────────────────────────────────────────────────


def test_uninstall_returns_false_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
        result = service.uninstall("cortex")
    assert result is False


def test_uninstall_removes_systemd_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Pre-create a unit file
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit_file = unit_dir / "empirica-listener-cortex.service"
    unit_file.write_text("[Unit]")

    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch(
            "empirica.core.loop_scheduler.persistent_listener._systemctl",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
        result = service.uninstall("cortex")

    assert result is True
    assert not unit_file.exists()


# ─── Status ─────────────────────────────────────────────────────────────


def test_status_unavailable_backend():
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
    status = service.status("cortex")
    assert status.backend == "unavailable"
    assert status.installed is False
    assert status.active is False


def test_status_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
    status = service.status("cortex")
    assert status.backend == "systemd"
    assert status.installed is False
    assert status.active is False


def test_status_installed_and_active(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "empirica-listener-cortex.service").write_text("[Unit]")

    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch(
            "empirica.core.loop_scheduler.persistent_listener._systemctl",
            return_value=subprocess.CompletedProcess([], 0, "active\n", ""),
        ),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
        status = service.status("cortex")

    assert status.installed is True
    assert status.active is True


def test_status_installed_but_inactive(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "empirica-listener-cortex.service").write_text("[Unit]")

    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch(
            "empirica.core.loop_scheduler.persistent_listener._systemctl",
            return_value=subprocess.CompletedProcess([], 3, "inactive\n", ""),
        ),
    ):
        service = PersistentListenerService(empirica_bin="empirica")
        status = service.status("cortex")

    assert status.installed is True
    assert status.active is False


# ─── launchd liveness: decoupled from plist-file location ──────────────


def test_status_launchd_active_when_loaded_but_plist_elsewhere(tmp_path, monkeypatch):
    """Regression (duplicate-listener bug, prop_f4holded): launchd can load a
    service from a domain/dir other than ~/Library/LaunchAgents (symptom: PPID
    1), so the plist isn't at unit_path — but `launchctl list <label>` shows it
    loaded. status() must report active=True regardless of plist-file existence,
    else session-monitor-arm reads active=False and arms a DUPLICATE listener."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Deliberately do NOT create the plist under ~/Library/LaunchAgents.
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.sys") as mock_sys,
        patch(
            "empirica.core.loop_scheduler.persistent_listener._launchctl",
            return_value=subprocess.CompletedProcess([], 0, "12345\t0\tcom.empirica.listener.cortex\n", ""),
        ),
    ):
        mock_sys.platform = "darwin"
        service = PersistentListenerService(empirica_bin="empirica")
        status = service.status("cortex")

    assert status.backend == "launchd"
    assert status.active is True  # liveness from the label, not the plist file
    assert status.installed is True  # loaded-but-plist-elsewhere still counts as installed


def test_status_launchd_inactive_when_label_not_loaded(tmp_path, monkeypatch):
    """launchctl list of an unknown label returns non-zero → not active."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.sys") as mock_sys,
        patch(
            "empirica.core.loop_scheduler.persistent_listener._launchctl",
            return_value=subprocess.CompletedProcess([], 113, "", "Could not find service"),
        ),
    ):
        mock_sys.platform = "darwin"
        service = PersistentListenerService(empirica_bin="empirica")
        status = service.status("cortex")

    assert status.active is False
    assert status.installed is False


def test_status_launchd_never_raises_on_launchctl_timeout(tmp_path, monkeypatch):
    """A slow launchctl must not propagate TimeoutExpired out of status()
    (would otherwise be swallowed by is_listener_running → silent false-negative)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.sys") as mock_sys,
        patch(
            "empirica.core.loop_scheduler.persistent_listener._launchctl",
            side_effect=subprocess.TimeoutExpired(cmd="launchctl", timeout=5),
        ),
    ):
        mock_sys.platform = "darwin"
        service = PersistentListenerService(empirica_bin="empirica")
        status = service.status("cortex")  # must not raise

    assert status.active is False


# ─── Fail-safe wrappers ────────────────────────────────────────────────


def test_is_listener_running_never_raises_on_error():
    """The module-level is_listener_running is used by hooks — must never raise."""
    with patch(
        "empirica.core.loop_scheduler.persistent_listener.PersistentListenerService",
        side_effect=RuntimeError("simulated failure"),
    ):
        result = is_listener_running("cortex")
    assert result is False


def test_is_listener_running_returns_true_when_active(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "empirica-listener-cortex.service").write_text("[Unit]")

    with (
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=False),
        patch(
            "empirica.core.loop_scheduler.persistent_listener._systemctl",
            return_value=subprocess.CompletedProcess([], 0, "active\n", ""),
        ),
    ):
        assert is_listener_running("cortex") is True


# ── mesh off / mesh on on launchd (mesh-support prop_isfyt5634vg6hmuuyb25ivog7m) ─────────────────────────────────────
# `mesh off` acted only on systemd and printed "listener stopped" on every backend: on macOS launchd kept the listener
# running, label enabled, and it came back at boot.


def _launchd_service(tmp_path, monkeypatch, calls, answers=None):
    import os

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(os, "getuid", lambda: 501)
    answers = answers or {}

    def fake(*args, check=False):
        calls.append(args)
        return subprocess.CompletedProcess(args, answers.get(args[0], 0), "", "")

    patches = [
        patch("empirica.core.loop_scheduler.persistent_listener.is_systemd_available", return_value=False),
        patch("empirica.core.loop_scheduler.persistent_listener.is_launchd_available", return_value=True),
        patch("empirica.core.loop_scheduler.persistent_listener._launchctl", side_effect=fake),
    ]
    for p in patches:
        p.start()
    mock_sys = patch("empirica.core.loop_scheduler.persistent_listener.sys").start()
    mock_sys.platform = "darwin"
    service = PersistentListenerService(empirica_bin="/path/to/empirica")
    return service


@pytest.fixture
def stop_patches():
    yield
    patch.stopall()


def test_stop_on_launchd_boots_out_and_disables_the_label(tmp_path, monkeypatch, stop_patches):
    calls = []
    service = _launchd_service(tmp_path, monkeypatch, calls)
    service.stop("ffp-archive")
    assert ("bootout", "gui/501/com.empirica.listener.ffp-archive") in calls
    assert ("disable", "gui/501/com.empirica.listener.ffp-archive") in calls


def test_stop_falls_back_to_unload_when_bootout_fails(tmp_path, monkeypatch, stop_patches):
    calls = []
    service = _launchd_service(tmp_path, monkeypatch, calls, answers={"bootout": 3})
    plist = service.unit_path("ffp-archive")
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text("<plist/>")
    service.stop("ffp-archive")
    assert ("unload", "-w", str(plist)) in calls


def test_start_on_launchd_enables_then_bootstraps(tmp_path, monkeypatch, stop_patches):
    calls = []
    service = _launchd_service(tmp_path, monkeypatch, calls)
    plist = service.unit_path("ffp-archive")
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text("<plist/>")
    service.start("ffp-archive")
    assert calls[0] == ("enable", "gui/501/com.empirica.listener.ffp-archive")
    assert ("bootstrap", "gui/501", str(plist)) in calls


def test_mesh_off_says_so_when_the_listener_is_still_running(monkeypatch, capsys):
    from types import SimpleNamespace

    from empirica.cli.command_handlers import mesh_commands as mc
    from empirica.core.loop_scheduler.persistent_listener import ListenerStatus

    running = ListenerStatus(ai_id="ffp-archive", backend="launchd", installed=True, active=True)
    stopped = []
    monkeypatch.setattr(mc, "listener_status_for", lambda ai_id: running)
    monkeypatch.setattr(mc, "stop_listener_for", lambda ai_id: stopped.append(ai_id))
    rc = mc.handle_mesh_off_command(SimpleNamespace(instance="ffp-archive", uninstall=False))
    out, err = capsys.readouterr()
    assert rc == 1 and stopped == ["ffp-archive"]
    assert "listener stopped" not in out and "still running" in err and "launchctl bootout" in err


def test_mesh_off_reports_stopped_only_once_it_is(monkeypatch, capsys):
    from types import SimpleNamespace

    from empirica.cli.command_handlers import mesh_commands as mc
    from empirica.core.loop_scheduler.persistent_listener import ListenerStatus

    states = iter(
        [
            ListenerStatus(ai_id="x", backend="launchd", installed=True, active=True),
            ListenerStatus(ai_id="x", backend="launchd", installed=True, active=False),
        ]
    )
    monkeypatch.setattr(mc, "listener_status_for", lambda ai_id: next(states))
    monkeypatch.setattr(mc, "stop_listener_for", lambda ai_id: None)
    assert mc.handle_mesh_off_command(SimpleNamespace(instance="x", uninstall=False)) == 0
    assert "listener stopped" in capsys.readouterr().out


def test_a_launchctl_timeout_is_a_result_not_a_traceback(monkeypatch):
    # _launchctl said it never raised, but its 5 s timeout raised TimeoutExpired out of stop() (1.16.0 sweep, a34eef6d).
    import subprocess

    from empirica.core.loop_scheduler import persistent_listener as pl

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="launchctl", timeout=5)

    monkeypatch.setattr(pl.subprocess, "run", hang)
    r = pl._launchctl("list", "com.empirica.listener.x")
    assert r.returncode == pl.LAUNCHCTL_TIMEOUT


def test_mesh_off_cannot_claim_a_stop_it_could_not_confirm(monkeypatch, capsys):
    from types import SimpleNamespace

    from empirica.cli.command_handlers import mesh_commands as mc
    from empirica.core.loop_scheduler.persistent_listener import ListenerStatus

    states = iter(
        [
            ListenerStatus(ai_id="x", backend="launchd", installed=True, active=True),
            ListenerStatus(ai_id="x", backend="launchd", installed=True, active=False, uncertain=True),
        ]
    )
    monkeypatch.setattr(mc, "listener_status_for", lambda ai_id: next(states))
    monkeypatch.setattr(mc, "stop_listener_for", lambda ai_id: None)
    assert mc.handle_mesh_off_command(SimpleNamespace(instance="x", uninstall=False)) == 1
    out, err = capsys.readouterr()
    assert "listener stopped" not in out and "could not confirm" in err
