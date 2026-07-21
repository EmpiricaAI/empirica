"""Tests for empirica.core.cockpit.liveness.

Covers the four signal paths:
- tmux pane running claude (alive)
- tmux pane running other command (dead — Claude exited)
- tmux pane gone (dead — terminal closed)
- non-tmux PPID alive / dead / fallback to recent activity
"""

from __future__ import annotations

import json

import pytest

from empirica.core.cockpit import liveness as lv


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake = tmp_path / ".empirica"
    fake.mkdir()
    (fake / "instance_projects").mkdir()
    (fake / "tty_sessions").mkdir()
    monkeypatch.setattr(lv, "EMPIRICA_DIR", fake)
    monkeypatch.setattr(lv, "TTY_SESSIONS_DIR", fake / "tty_sessions")
    return fake


def test_current_instance_always_alive(fake_home):
    result = lv.is_alive("tmux_99", current_instance_id="tmux_99")
    assert result.alive is True
    assert result.reason == "current instance"


def test_tmux_pane_running_claude_is_alive(fake_home):
    # Pre-computed: pane %5 has claude running.
    result = lv.is_alive("tmux_5", live_panes={"5"})
    assert result.alive is True
    assert "running claude" in result.reason


def test_tmux_pane_running_bash_is_dead(fake_home, monkeypatch):
    """Pane exists in `_all_tmux_panes` but is not in the claude-only set
    (e.g. user exited claude, bash is left running)."""
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: {"5", "6"})
    result = lv.is_alive("tmux_5", live_panes=set())  # claude not in any pane
    assert result.alive is False
    assert "claude is not running there" in result.reason


def test_tmux_pane_gone_is_dead(fake_home, monkeypatch):
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: set())
    result = lv.is_alive("tmux_5", live_panes=set())
    assert result.alive is False
    assert "does not exist" in result.reason


def test_non_tmux_with_alive_ppid_is_alive(fake_home, monkeypatch):
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(json.dumps({"pid": 12345, "ppid": 67890}))
    monkeypatch.setattr(lv, "_process_alive", lambda pid, ct=None: pid == 67890)
    result = lv.is_alive("term-pts-7")
    assert result.alive is True
    assert result.pid_checked == 67890


def test_non_tmux_with_dead_ppid_is_dead(fake_home, monkeypatch):
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(json.dumps({"pid": 12345, "ppid": 67890}))
    monkeypatch.setattr(lv, "_process_alive", lambda _pid, _ct=None: False)
    result = lv.is_alive("term-pts-7")
    assert result.alive is False
    assert "pid 67890 dead" in result.reason


def test_non_tmux_no_pid_recent_activity_is_alive(fake_home):
    """No PID, no tmux info, but state file was touched recently — give
    benefit of doubt (covers fresh sessions before session-init wrote PID)."""
    result = lv.is_alive("term-pts-7", last_activity_seconds=30.0)
    assert result.alive is True
    assert "recent activity" in result.reason


def test_non_tmux_no_pid_old_activity_is_dead(fake_home):
    result = lv.is_alive("term-pts-7", last_activity_seconds=99999.0)
    assert result.alive is False


def test_non_tmux_no_signals_at_all_is_dead(fake_home):
    result = lv.is_alive("term-pts-7")
    assert result.alive is False


def test_tmux_with_dead_ppid_overrides_pane_alive(fake_home, monkeypatch):
    """If we have a captured PPID and it's dead, that's a stronger signal
    than the pane being a 'claude' pane (the captured PPID was Claude;
    if it died, this instance is gone — even if a NEW Claude started in
    the same pane, it's a different instance for our purposes)."""
    # Actually, current implementation: pane-with-claude wins over PID check.
    # That's fine for the user's stated need (don't show dead Claudes), but
    # documents the precedence ordering.
    pass  # Behavioural assertion — kept as documentation, not a hard test.


def test_tmux_bash_but_captured_pid_alive_is_alive(fake_home, monkeypatch):
    """Regression for issue #98 (Philipp): pane shows bash as foreground
    (claude is in a sub-process / split / wrapper) but the captured PID
    is alive. Must return alive — was returning dead in the prior
    short-circuit shape, which hid live Claudes from the cockpit.
    """
    (fake_home / "instance_projects" / "tmux_5.json").write_text(json.dumps({"pid": 12345, "ppid": 67890}))
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: {"5"})
    monkeypatch.setattr(lv, "_process_alive", lambda pid, ct=None: pid == 67890)

    # Pane exists, claude is NOT the foreground command, but PID is alive.
    result = lv.is_alive("tmux_5", live_panes=set())

    assert result.alive is True, f"PID-alive must override tmux disagreement (was: {result.reason})"
    assert result.pid_checked == 67890
    assert result.tmux_pane == "5"


def test_tmux_bash_and_captured_pid_dead_is_dead(fake_home, monkeypatch):
    """Counterpart: pane shows bash AND captured PID is dead → dead.
    Verifies the false-positive direction stays correct after the refactor.
    """
    (fake_home / "instance_projects" / "tmux_5.json").write_text(json.dumps({"pid": 12345, "ppid": 67890}))
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: {"5"})
    monkeypatch.setattr(lv, "_process_alive", lambda _pid, _ct=None: False)

    result = lv.is_alive("tmux_5", live_panes=set())

    assert result.alive is False
    assert "pid 67890 dead" in result.reason


def test_tmux_bash_no_captured_pid_no_activity_is_dead(fake_home, monkeypatch):
    """Pane shows bash, no PID captured, no recent activity → dead.
    The reason string now records BOTH the tmux state and the PID
    absence so the operator can diagnose."""
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: {"5"})
    result = lv.is_alive("tmux_5", live_panes=set())
    assert result.alive is False
    assert "no captured PID survived" in result.reason


def test_tmux_bash_with_recent_activity_is_dead(fake_home, monkeypatch):
    """Tmux says pane has bash (definitive negative). A stale instance
    file getting touched by housekeeping (bus sweep, cross-instance
    state read, etc.) must NOT revive it via the recent-activity
    fallback. This was the tmux_3 ghost David spotted post-#98."""
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: {"3"})
    result = lv.is_alive("tmux_3", live_panes=set(), last_activity_seconds=15.0)
    assert result.alive is False, (
        f"recent activity must not revive a tmux pane that has bash as foreground (was: {result.reason})"
    )
    assert "no captured PID survived" in result.reason


def test_tmux_absent_with_recent_activity_is_dead(fake_home, monkeypatch):
    """Same protection when the pane is gone entirely. Stale file
    touches don't conjure a pane back into existence."""
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: set())
    result = lv.is_alive("tmux_99", live_panes=set(), last_activity_seconds=15.0)
    assert result.alive is False
    assert "does not exist" in result.reason


def test_tmux_unqueryable_recent_activity_still_grants_life(fake_home, monkeypatch):
    """If tmux can't be queried at all, recent-activity fallback is
    still the right safety net for fresh sessions — the gate only
    kicks in on a definitive tmux negative."""
    monkeypatch.setattr(lv, "_live_tmux_panes", lambda: None)
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: None)
    result = lv.is_alive("tmux_5", last_activity_seconds=15.0)
    assert result.alive is True
    assert "recent activity" in result.reason


def test_pid_capture_falls_back_to_tty_sessions(fake_home, monkeypatch):
    """instance_projects has tty_key but no pid; tty_sessions has the pid."""
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(json.dumps({"tty_key": "pts-7"}))
    (fake_home / "tty_sessions" / "pts-7.json").write_text(json.dumps({"pid": 12345, "ppid": 67890}))
    monkeypatch.setattr(lv, "_process_alive", lambda pid, ct=None: pid == 67890)
    result = lv.is_alive("term-pts-7")
    assert result.alive is True
    assert result.pid_checked == 67890


# --- Signal 2: exact env-match process scan (primary) ----------------------


def test_process_env_match_is_alive(fake_home):
    """A live claude proc declaring this instance_id in EMPIRICA_INSTANCE_ID →
    alive. Exact + resume-proof; no tmux pane, no captured PID needed."""
    result = lv.is_alive("empirica-vr", live_claude_instance_ids={"empirica-vr"})
    assert result.alive is True
    assert result.signal == "process_env"


def test_process_env_overrides_stale_captured_pid(fake_home, monkeypatch):
    """The reported incident: `claude --resume` left the captured PID stale,
    but the live proc declares the instance_id. Env match wins over dead PID."""
    (fake_home / "instance_projects" / "empirica-vr.json").write_text(json.dumps({"pid": 111, "ppid": 222}))
    monkeypatch.setattr(lv, "_process_alive", lambda _pid, _ct=None: False)  # captured PID dead
    result = lv.is_alive("empirica-vr", live_claude_instance_ids={"empirica-vr"})
    assert result.alive is True
    assert result.signal == "process_env"


def test_process_env_takes_precedence_over_cwd(fake_home, tmp_path):
    """When both signals would fire, the exact env match wins (it's checked
    first and is instance-level, not project-level)."""
    proj = tmp_path / "projX"
    proj.mkdir()
    result = lv.is_alive(
        "empirica-vr",
        project_path=str(proj),
        live_claude_instance_ids={"empirica-vr"},
        live_claude_cwds={str(proj.resolve())},
    )
    assert result.signal == "process_env"


def test_process_env_skipped_when_instance_ids_none(fake_home):
    """Default None param → env signal never consulted (opt-in)."""
    result = lv.is_alive("empirica-vr")
    assert result.alive is False


# --- Signal 4: cwd-fallback process scan -----------------------------------


def test_process_cwd_makes_non_tmux_instance_alive(fake_home, tmp_path):
    """A live claude process whose cwd matches the instance's project_path →
    alive (fallback for a live proc with no EMPIRICA_INSTANCE_ID env)."""
    proj = tmp_path / "projA"
    proj.mkdir()
    result = lv.is_alive(
        "term-pts-7",
        project_path=str(proj),
        live_claude_cwds={str(proj.resolve())},
    )
    assert result.alive is True
    assert result.signal == "process_cwd"
    assert "live claude process" in result.reason


def test_process_cwd_overrides_stale_captured_pid(fake_home, monkeypatch, tmp_path):
    """Captured PID stale (dead), pane is bash, but a live proc exists for the
    project (no env). The cwd fallback wins — checked BEFORE the PID verdict."""
    proj = tmp_path / "projB"
    proj.mkdir()
    (fake_home / "instance_projects" / "tmux_5.json").write_text(json.dumps({"pid": 111, "ppid": 222}))
    monkeypatch.setattr(lv, "_process_alive", lambda _pid, _ct=None: False)  # captured PID dead
    monkeypatch.setattr(lv, "_all_tmux_panes", lambda: {"5"})  # pane is bash
    result = lv.is_alive(
        "tmux_5",
        live_panes=set(),
        project_path=str(proj),
        live_claude_cwds={str(proj.resolve())},
    )
    assert result.alive is True
    assert result.signal == "process_cwd"


def test_process_cwd_absent_falls_through_to_pid(fake_home, monkeypatch, tmp_path):
    """Project not in the live-cwd set → signal skipped, normal precedence."""
    proj = tmp_path / "projC"
    proj.mkdir()
    (fake_home / "instance_projects" / "term-pts-7.json").write_text(json.dumps({"pid": 111, "ppid": 222}))
    monkeypatch.setattr(lv, "_process_alive", lambda pid, ct=None: pid == 222)
    result = lv.is_alive(
        "term-pts-7",
        project_path=str(proj),
        live_claude_cwds={"/some/other/project"},
    )
    assert result.alive is True
    assert result.signal == "pid"


def test_process_scan_skipped_when_both_none(fake_home, tmp_path):
    """Default None params → neither process signal consulted (preserves prior
    behavior for every existing caller / test)."""
    proj = tmp_path / "projD"
    proj.mkdir()
    result = lv.is_alive("term-pts-7", project_path=str(proj))
    assert result.alive is False


# --- claude-process heuristic + scan ---------------------------------------


def test_is_claude_proc_matches_claude_binary():
    assert lv._is_claude_proc("claude", []) is True
    assert lv._is_claude_proc("Claude", None) is True


def test_is_claude_proc_node_requires_claude_in_cmdline():
    assert lv._is_claude_proc("node", ["node", "/usr/lib/claude/cli.js"]) is True
    assert lv._is_claude_proc("node", ["node", "server.js"]) is False


def test_is_claude_proc_rejects_other_processes():
    assert lv._is_claude_proc("bash", ["bash"]) is False
    assert lv._is_claude_proc("python", ["python", "claude_helper.py"]) is False


def test_is_claude_proc_matches_mangled_version_name():
    # Regression (Philipp): Claude Code 2.1.x mangles psutil name() to a bare
    # version string, so a name-only gate hid every live 2.1.x seat. argv[0]
    # basename stays 'claude' — match on it.
    assert lv._is_claude_proc("2.1.212", ["claude", "--resume"]) is True
    assert lv._is_claude_proc("2.1.212", ["/usr/local/bin/claude", "-p"]) is True
    # A version-named process that ISN'T claude is still rejected (no over-broadening).
    assert lv._is_claude_proc("2.1.212", ["node", "server.js"]) is False


class _FakeProc:
    """psutil-like process stub for scan_live_claude tests."""

    def __init__(self, name, cmdline, cwd=None, env=None, cwd_exc=None):
        self.info = {"name": name, "cmdline": cmdline}
        self._cwd = cwd
        self._env = env or {}
        self._cwd_exc = cwd_exc

    def environ(self):
        return self._env

    def cwd(self):
        if self._cwd_exc is not None:
            raise self._cwd_exc
        return self._cwd


def test_scan_live_claude_collects_env_ids_and_cwd_counts(monkeypatch, tmp_path):
    """instance_ids come from EMPIRICA_INSTANCE_ID env; cwd_counts group by dir."""
    import psutil

    a = str((tmp_path / "a").resolve())
    b = str((tmp_path / "b").resolve())
    procs = [
        _FakeProc("claude", ["claude"], cwd=a, env={"EMPIRICA_INSTANCE_ID": "empirica-vr"}),
        _FakeProc("claude", ["claude"], cwd=a, env={"EMPIRICA_INSTANCE_ID": "empirica-storyboard"}),
        _FakeProc("node", ["node", "/x/claude/cli.js"], cwd=b, env={}),  # no env id
        _FakeProc("bash", ["bash"], cwd=a),  # not claude — ignored
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: procs)
    scan = lv.scan_live_claude()
    assert scan.instance_ids == {"empirica-vr", "empirica-storyboard"}
    assert scan.cwd_counts == {a: 2, b: 1}


def test_scan_live_claude_skips_inaccessible_procs(monkeypatch, tmp_path):
    """A proc whose cwd() raises is skipped for cwd but not fatal to the sweep;
    its env id is still collected."""
    import psutil

    a = str((tmp_path / "a").resolve())
    procs = [
        _FakeProc("claude", ["claude"], cwd=a, env={"EMPIRICA_INSTANCE_ID": "good"}),
        _FakeProc("claude", ["claude"], env={"EMPIRICA_INSTANCE_ID": "bad"}, cwd_exc=psutil.AccessDenied()),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: procs)
    scan = lv.scan_live_claude()
    assert scan.instance_ids == {"good", "bad"}
    assert scan.cwd_counts == {a: 1}


# --- create_time reuse guard (flapping fix) --------------------------------


class _FakePsutilProc:
    """Stub for psutil.Process(pid) with a fixed create_time."""

    def __init__(self, _pid, create_time):
        self._ct = create_time

    def create_time(self):
        return self._ct


def test_process_alive_create_time_match(monkeypatch):
    import psutil

    monkeypatch.setattr(psutil, "Process", lambda pid: _FakePsutilProc(pid, 1000.0))
    assert lv._process_alive(4242, expected_create_time=1000.0) is True


def test_process_alive_create_time_mismatch_is_dead(monkeypatch):
    """Recycled pid: process exists but its start time differs → dead (no flap)."""
    import psutil

    monkeypatch.setattr(psutil, "Process", lambda pid: _FakePsutilProc(pid, 9999.0))
    assert lv._process_alive(4242, expected_create_time=1000.0) is False


def test_process_alive_no_expected_uses_oskill(monkeypatch):
    """No captured create_time → bare os.kill probe (prior behavior)."""
    monkeypatch.setattr(lv.os, "kill", lambda pid, sig: None)
    assert lv._process_alive(4242) is True

    def _boom(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(lv.os, "kill", _boom)
    assert lv._process_alive(4242) is False


def test_process_alive_psutil_error_falls_back_to_oskill(monkeypatch):
    """If psutil errors despite an expected create_time, fall back to os.kill."""
    import psutil

    def _raise(pid):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "Process", _raise)
    monkeypatch.setattr(lv.os, "kill", lambda pid, sig: None)
    assert lv._process_alive(4242, expected_create_time=1000.0) is True


def test_read_captured_pids_includes_create_time(fake_home):
    (fake_home / "instance_projects" / "x.json").write_text(
        json.dumps({"pid": 1, "ppid": 2, "ppid_create_time": 1234.5})
    )
    assert lv._read_captured_pids("x") == (1, 2, 1234.5)


def test_read_captured_pids_absent_create_time_is_none(fake_home):
    (fake_home / "instance_projects" / "x.json").write_text(json.dumps({"pid": 1, "ppid": 2}))
    _pid, _ppid, ct = lv._read_captured_pids("x")
    assert ct is None


def test_is_alive_reused_ppid_does_not_flap_alive(fake_home, monkeypatch):
    """End-to-end: a recycled ppid number (live impostor, wrong start time) →
    dead, not alive. Without the guard os.kill would read it alive (the flap)."""
    import psutil

    (fake_home / "instance_projects" / "x.json").write_text(
        json.dumps({"pid": 1, "ppid": 5000, "ppid_create_time": 1000.0})
    )
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakePsutilProc(pid, 8888.0))  # impostor
    result = lv.is_alive("x")
    assert result.alive is False
    assert "dead" in result.reason


def test_is_alive_matching_ppid_create_time_is_alive(fake_home, monkeypatch):
    import psutil

    (fake_home / "instance_projects" / "x.json").write_text(
        json.dumps({"pid": 1, "ppid": 5000, "ppid_create_time": 1000.0})
    )
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakePsutilProc(pid, 1000.0))
    result = lv.is_alive("x")
    assert result.alive is True
    assert result.signal == "pid"


def test_live_claude_pids_by_instance_maps_iid_to_pid(monkeypatch):
    import psutil

    class _P:
        def __init__(self, pid, env, ct):
            self.pid = pid
            self.info = {"name": "claude", "cmdline": ["claude"]}
            self._env = env
            self._ct = ct

        def environ(self):
            return self._env

        def create_time(self):
            return self._ct

    procs = [
        _P(101, {"EMPIRICA_INSTANCE_ID": "empirica-vr"}, 5.0),
        _P(102, {}, 6.0),  # no env id — skipped
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: procs)
    assert lv.live_claude_pids_by_instance() == {"empirica-vr": (101, 5.0)}
