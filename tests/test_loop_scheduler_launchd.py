"""Tests for empirica.core.loop_scheduler.launchd.

Mocks subprocess.run + Path.home() so tests run on Linux CI. The launchd
backend mirrors SystemdLoopScheduler's interface — enable writes a plist,
launchctl load, disable unloads + removes; same tick logic (delegated to
systemd module since fire semantics are platform-independent).
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from empirica.core.loop_scheduler import launchd as launchd_mod
from empirica.core.loop_scheduler.launchd import (
    LaunchdLoopScheduler,
    LaunchdUnavailable,
    _label,
    _safe,
    parse_interval_seconds,
)

# ── Slug + label ─────────────────────────────────────────────────────────


def test_safe_sanitizes_dots():
    """Dots are reserved as label separators in reverse-DNS form."""
    assert _safe("a.b.c") == "a-b-c"


def test_label_uses_reverse_dns():
    assert _label("cortex", "mailbox-poll") == "com.empirica.loop.cortex.mailbox-poll"


def test_label_empty_instance_defaults():
    assert _label("", "x") == "com.empirica.loop.default.x"


# ── Interval parsing ─────────────────────────────────────────────────────


def test_parse_interval_seconds_systemd_strings():
    assert parse_interval_seconds("30s") == 30
    assert parse_interval_seconds("5min") == 300
    assert parse_interval_seconds("5m") == 300
    assert parse_interval_seconds("2h") == 7200
    assert parse_interval_seconds("1d") == 86400


def test_parse_interval_seconds_bare_number_is_seconds():
    assert parse_interval_seconds("90") == 90


def test_parse_interval_seconds_accepts_int():
    assert parse_interval_seconds(45) == 45


def test_parse_interval_rejects_zero_or_negative():
    with pytest.raises(ValueError):
        parse_interval_seconds("0s")
    with pytest.raises(ValueError):
        parse_interval_seconds(-5)


def test_parse_interval_rejects_garbage():
    with pytest.raises(ValueError):
        parse_interval_seconds("forever")
    with pytest.raises(ValueError):
        parse_interval_seconds("")


# ── Availability + constructor ───────────────────────────────────────────


def test_constructor_raises_when_launchd_unavailable(monkeypatch):
    monkeypatch.setattr(launchd_mod, "is_launchd_available", lambda: False)
    with pytest.raises(LaunchdUnavailable):
        LaunchdLoopScheduler()


def test_is_launchd_available_returns_false_on_non_darwin(monkeypatch):
    """Probe must not falsely succeed on Linux even if launchctl somehow
    exists (homebrew, container weirdness)."""
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    from empirica.core.loop_scheduler.launchd import is_launchd_available

    assert is_launchd_available() is False


# ── Fixture: stub the macOS env ──────────────────────────────────────────


@pytest.fixture
def fake_launchd_env(tmp_path, monkeypatch):
    """Redirect ~/Library/LaunchAgents and Path.home() to tmp_path,
    stub is_launchd_available() to True."""
    home = tmp_path / "fake_home"
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(launchd_mod, "is_launchd_available", lambda: True)
    return {"home": home, "agents": agents}


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ── enable() ─────────────────────────────────────────────────────────────


def test_enable_writes_plist_with_program_arguments(fake_launchd_env):
    calls = []

    def fake_run(args, **kw):
        calls.append(tuple(args))
        return _fake_proc()

    with patch.object(subprocess, "run", fake_run):
        sched = LaunchdLoopScheduler(empirica_bin="/usr/local/bin/empirica")
        paths = sched.enable("cortex", "mailbox-poll", "30s")

    assert paths.plist.exists()
    with open(paths.plist, "rb") as f:
        data = plistlib.load(f)
    assert data["Label"] == "com.empirica.loop.cortex.mailbox-poll"
    assert data["ProgramArguments"] == [
        "/usr/local/bin/empirica",
        "loop",
        "tick",
        "cortex",
        "mailbox-poll",
    ]
    assert data["StartInterval"] == 30
    assert data["RunAtLoad"] is False
    assert data["KeepAlive"] is False

    # launchctl load -w must have been called
    flat = [" ".join(c) for c in calls]
    assert any("launchctl load -w" in c for c in flat)


def test_enable_converts_interval_string_to_seconds(fake_launchd_env):
    with patch.object(subprocess, "run", lambda *a, **kw: _fake_proc()):
        sched = LaunchdLoopScheduler()
        paths = sched.enable("i", "n", "5min")

    with open(paths.plist, "rb") as f:
        data = plistlib.load(f)
    assert data["StartInterval"] == 300


# ── disable() ────────────────────────────────────────────────────────────


def test_disable_is_idempotent_when_plist_missing(fake_launchd_env):
    with patch.object(subprocess, "run", lambda *a, **kw: _fake_proc()):
        sched = LaunchdLoopScheduler()
        result = sched.disable("ghost", "nonexistent")
    assert result is False


def test_disable_removes_plist_after_enable(fake_launchd_env):
    with patch.object(subprocess, "run", lambda *a, **kw: _fake_proc()):
        sched = LaunchdLoopScheduler()
        sched.enable("cortex", "poll", "30s")
        paths = sched.unit_paths("cortex", "poll")
        assert paths.plist.exists()
        result = sched.disable("cortex", "poll")
    assert result is True
    assert not paths.plist.exists()


# ── status() ─────────────────────────────────────────────────────────────


def test_status_reports_active_when_launchctl_shows_pid(fake_launchd_env):
    # Pre-create plist so 'enabled' returns True
    (fake_launchd_env["agents"] / "com.empirica.loop.cortex.poll.plist").touch()

    def fake_run(args, **kw):
        return _fake_proc(stdout='{ "Label" = "x"; "PID" = 12345; }')

    with patch.object(subprocess, "run", fake_run):
        sched = LaunchdLoopScheduler()
        st = sched.status("cortex", "poll")
    assert st.active is True
    assert st.enabled is True


def test_status_reports_active_when_last_exit_status_zero(fake_launchd_env):
    (fake_launchd_env["agents"] / "com.empirica.loop.cortex.poll.plist").touch()

    def fake_run(args, **kw):
        return _fake_proc(stdout='{ "Label" = "x"; "LastExitStatus" = 0; }')

    with patch.object(subprocess, "run", fake_run):
        sched = LaunchdLoopScheduler()
        st = sched.status("cortex", "poll")
    assert st.active is True


def test_status_reports_inactive_when_unit_missing(fake_launchd_env):
    """Loop never installed → not enabled, not active. launchctl list of
    an unknown label returns non-zero."""

    def fake_run(args, **kw):
        return _fake_proc(returncode=113, stderr="Could not find service")

    with patch.object(subprocess, "run", fake_run):
        sched = LaunchdLoopScheduler()
        st = sched.status("ghost", "absent")
    assert st.active is False
    assert st.enabled is False


# ── list_enabled() ────────────────────────────────────────────────────────


def test_list_enabled_returns_labels_from_filesystem(fake_launchd_env):
    agents = fake_launchd_env["agents"]
    (agents / "com.empirica.loop.cortex.mailbox-poll.plist").touch()
    (agents / "com.empirica.loop.empirica.engagement.plist").touch()
    (agents / "com.someone.else.daemon.plist").touch()  # not ours

    sched = LaunchdLoopScheduler()
    labels = sched.list_enabled()
    assert "com.empirica.loop.cortex.mailbox-poll" in labels
    assert "com.empirica.loop.empirica.engagement" in labels
    assert "com.someone.else.daemon" not in labels


# ── tick delegation (shared with systemd backend) ────────────────────────


def test_tick_delegates_to_systemd_tick_implementation(fake_launchd_env, monkeypatch):
    """Single tick implementation in systemd module — launchd's tick is a
    delegator. Same content-aware/throttle semantics across platforms."""
    from empirica.core.loop_scheduler.systemd import SystemdLoopScheduler

    called = []

    def fake_tick(instance_id, name, *, force=False):
        called.append((instance_id, name, force))
        return None  # silent (throttled or no content)

    monkeypatch.setattr(SystemdLoopScheduler, "tick", staticmethod(fake_tick))
    LaunchdLoopScheduler.tick("cortex", "poll")
    assert called == [("cortex", "poll", False)]


# ── Factory ──────────────────────────────────────────────────────────────


def test_factory_returns_launchd_on_macos(monkeypatch):
    """Factory + scheduler constructor both check is_launchd_available;
    patch both names (the package re-export + the source-module symbol)."""
    import sys

    import empirica.core.loop_scheduler as pkg

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(pkg, "is_launchd_available", lambda: True)
    monkeypatch.setattr(launchd_mod, "is_launchd_available", lambda: True)
    sched = pkg.get_loop_scheduler("/abs/empirica")
    assert sched.__class__.__name__ == "LaunchdLoopScheduler"


def test_factory_returns_systemd_on_linux(monkeypatch):
    import sys

    import empirica.core.loop_scheduler as pkg
    from empirica.core.loop_scheduler import systemd as systemd_mod

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pkg, "is_systemd_available", lambda: True)
    # The constructor checks the name in its own module, so patching only the package re-export passed where a systemd user manager exists
    # and failed on a runner without one (the nightly's clean-environment job, every night since 2026-10-28).
    monkeypatch.setattr(systemd_mod, "is_systemd_available", lambda: True)
    sched = pkg.get_loop_scheduler("/abs/empirica")
    assert sched.__class__.__name__ == "SystemdLoopScheduler"


def test_factory_raises_when_no_supported_scheduler(monkeypatch):
    import sys

    import empirica.core.loop_scheduler as pkg

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(pkg, "is_systemd_available", lambda: False)
    monkeypatch.setattr(pkg, "is_launchd_available", lambda: False)
    with pytest.raises(pkg.LoopSchedulerUnavailable):
        pkg.get_loop_scheduler("empirica")
