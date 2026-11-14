"""The cockpit's tmux server runs in its own user service, not inside the terminal that launched it (prop_kuswn7yepvepdewzvjtxhqicfy).

A tmux server inherits the systemd unit of whatever process first started it. On AIworkhorse that was an
app-Alacritty@<id>.service: when the terminal's main process ended, systemd stopped the unit and killed the server with all 15
practice panes in it (twice on 2026-11-14). When no server is running, launch now starts `tmux -D` (a foreground server) through
`systemd-run --user --unit=empirica-tmux --collect`, waits until it answers, then builds the sessions against it. A running
server is left alone; without a systemd user manager it falls back to today's behaviour and says so.

No test here starts a real unit: subprocess and the tmux probe are replaced.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.core.cockpit.launcher import tmux as T


class Box:
    """A fake machine: whether a tmux server answers, and what systemd-run does."""

    def __init__(self, server_up=False, comes_up_after=1, systemd_rc=0):
        self.up, self.comes_up_after, self.systemd_rc = server_up, comes_up_after, systemd_rc
        self.probes, self.runs = 0, []

    def tmux(self, *args, **_kw):
        self.probes += 1
        if not self.up and self.runs and self.probes > self.comes_up_after:
            self.up = True
        return subprocess.CompletedProcess(["tmux", *args], 0 if self.up else 1, "4242\n" if self.up else "", "")

    def run(self, argv, **_kw):
        self.runs.append(argv)
        return subprocess.CompletedProcess(
            argv, self.systemd_rc, "", "" if self.systemd_rc == 0 else "Unit empirica-tmux exists"
        )


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("EMPIRICA_COCKPIT_SERVER_UNIT", raising=False)  # the suite turns it off; these tests test it
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1001")
    monkeypatch.setenv("PATH", "/home/u/.local/bin:/usr/bin")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1001/default,1,0")
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.setenv("EMPIRICA_INSTANCE_ID", "empirica")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("INVOCATION_ID", "unit-of-the-terminal")


def _ensure(monkeypatch, box, systemd=True):
    monkeypatch.setattr(T, "_tmux", box.tmux)
    return T.ensure_server(
        run=box.run, which=lambda name: "/usr/bin/systemd-run" if systemd else None, sleep=lambda _s: None
    )


def test_a_running_server_is_left_alone(env, monkeypatch):
    box = Box(server_up=True)
    assert _ensure(monkeypatch, box)["mode"] == "existing" and box.runs == []


def test_with_no_server_it_starts_one_in_its_own_user_service_and_waits_for_it(env, monkeypatch):
    box = Box(comes_up_after=3)
    out = _ensure(monkeypatch, box)
    assert out["mode"] == "systemd" and out["unit"] == T.SERVER_UNIT
    (argv,) = box.runs
    assert argv[:2] == ["systemd-run", "--user"] and f"--unit={T.SERVER_UNIT}" in argv and "--collect" in argv
    assert argv[-2:] == ["tmux", "-D"], "a foreground server, so the unit lives as long as the server"
    assert "--setenv=PATH=/home/u/.local/bin:/usr/bin" in argv, "panes need the caller's PATH, not the manager's"
    for leaked in ("TMUX=", "TMUX_PANE=", "EMPIRICA_INSTANCE_ID=", "CLAUDE_CODE_SESSION_ID=", "INVOCATION_ID="):
        assert not any(a.startswith(f"--setenv={leaked}") for a in argv), f"{leaked} must not reach every pane"
    assert box.up, "it returned only once the server answered"


@pytest.mark.parametrize(
    ("box", "systemd", "why"),
    [
        (Box(), False, "no systemd"),
        (Box(systemd_rc=1), True, "Unit empirica-tmux exists"),
        (Box(comes_up_after=10_000), True, "did not answer"),
    ],
)
def test_otherwise_it_falls_back_to_starting_the_server_inline_and_says_why(env, monkeypatch, box, systemd, why):
    out = _ensure(monkeypatch, box, systemd=systemd)
    assert out["mode"] == "inline" and why in out["detail"]


def test_no_user_manager_without_a_runtime_dir(env, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    box = Box()
    assert _ensure(monkeypatch, box)["mode"] == "inline" and box.runs == []


def test_both_launch_paths_ensure_the_server_first():
    import inspect

    for fn in (T.launch_groups, T.launch_cockpit):
        src = inspect.getsource(fn)
        assert "ensure_server()" in src, fn.__name__
        assert src.index("ensure_server()") < src.index("write_session_start()"), "before anything starts tmux"


def test_the_switch_turns_it_off(env, monkeypatch):
    monkeypatch.setenv("EMPIRICA_COCKPIT_SERVER_UNIT", "off")
    box = Box()
    assert _ensure(monkeypatch, box)["mode"] == "inline" and box.runs == []
