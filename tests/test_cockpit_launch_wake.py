"""`cockpit launch` wakes the claude panes it opened, the way `cockpit refresh` does.

After the 2026-11-02 restart, mesh-support measured 0 of 6 resumed claude panes armed before a human typed. David confirmed the restart
was `tmux kill-server` then `cockpit launch`, so the wake that `refresh` runs never ran. His ruling (2026-11-05): launch runs the same
wake.

Launch hands the terminal to tmux (`os.execvp`), so nothing can run in that process once the layout is attached. The wake therefore
runs in a detached process, through the same `wake_resumed` function, and writes what happened to a file `cockpit status` reads. A
wake that never finished is shown as that, not as nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from empirica.core.cockpit import launch_wake
from empirica.core.cockpit.instance_actions import WakeResult
from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec, ProjectSpec


@pytest.fixture(autouse=True)
def cockpit_dir(tmp_path, monkeypatch):
    """Keep every cockpit state file (the launcher's and the wake's) off the real ~/.empirica/cockpit."""
    from empirica.core.cockpit.launcher import state

    d = tmp_path / "cockpit-state"
    d.mkdir()
    monkeypatch.setattr(state, "COCKPIT_DIR", d)
    monkeypatch.setattr(state, "LAST_SESSION_START_PATH", d / "last_session_start")
    monkeypatch.setattr(state, "LAST_CLEAN_SHUTDOWN_PATH", d / "last_clean_shutdown")
    monkeypatch.setattr(state, "LOCK_PATH", d / "active.lock")
    return d


def _entry(pane_id="%12", key="g/0", command="claude --continue", error=None):
    return {"key": key, "pane_id": pane_id, "instance_id": "x", "command": command, "error": error}


class Spawned:
    """Stands in for subprocess.Popen: records how the wake process would have been started."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))


# ── starting it ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_nothing_is_started_when_launch_opened_no_pane():
    spawn = Spawned()
    out = launch_wake.start([], "s", popen=spawn)
    assert out["started"] is False and spawn.calls == []
    assert launch_wake.read("s") is None


def test_nothing_is_started_when_no_opened_pane_runs_claude():
    spawn = Spawned()
    out = launch_wake.start([_entry(command="bash"), _entry("%13", "g/1", command="ecodex")], "s", popen=spawn)
    assert out["started"] is False and out["panes"] == 0 and spawn.calls == []


def test_a_detached_process_is_started_for_the_claude_panes_and_the_file_says_running():
    spawn = Spawned()
    out = launch_wake.start([_entry("%12", "g/0"), _entry("%13", "g/1", command="bash")], "s", popen=spawn)

    assert out == {"started": True, "panes": 1, "detail": out["detail"]}
    ((argv, kwargs),) = spawn.calls
    assert argv[0] == sys.executable
    assert argv[-1] == str(launch_wake.state_path("s"))
    assert kwargs["start_new_session"] is True, "it must outlive the launch that hands the terminal to tmux"
    for stream in ("stdin", "stdout", "stderr"):
        assert kwargs[stream] == subprocess.DEVNULL, "it must not hold the terminal"

    state = launch_wake.read("s")
    assert state["status"] == "running" and [e["key"] for e in state["entries"]] == ["g/0"]


def test_a_spawn_that_fails_is_reported_and_recorded():
    def boom(*_a, **_k):
        raise OSError("no fork for you")

    out = launch_wake.start([_entry()], "s", popen=boom)
    assert out["started"] is False and "no fork for you" in out["detail"]
    state = launch_wake.read("s")
    assert state["status"] == "failed" and "no fork for you" in state["error"]


def test_the_session_name_cannot_escape_the_cockpit_directory(cockpit_dir):
    path = launch_wake.state_path("../../etc/x y")
    assert path.parent == cockpit_dir
    assert "/" not in path.name and " " not in path.name


# ── the process itself ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _started(entries):
    launch_wake.start(entries, "s", popen=Spawned())
    return launch_wake.state_path("s")


def test_each_pane_is_woken_once_and_the_file_says_done():
    path = _started([_entry("%12", "g/0"), _entry("%13", "g/1")])
    calls = []

    def wake(pane):
        calls.append(pane)
        return WakeResult(pane, True, "woken", "tmux-send-keys")

    launch_wake.run(str(path), wake=wake, sleep=lambda _s: None)

    assert calls == ["tmux_12", "tmux_13"]
    state = launch_wake.read("s")
    assert state["status"] == "done" and state["finished_at"]
    assert [(r["key"], r["woken"]) for r in state["results"]] == [("g/0", True), ("g/1", True)]


def test_a_busy_or_mid_draft_pane_is_never_typed_into_and_is_reported():
    path = _started([_entry("%12", "g/0"), _entry("%13", "g/1")])
    clock = {"t": 0.0}

    def wake(pane):
        if pane == "tmux_13":
            return WakeResult(pane, False, "not woken: the pane has a draft", "refused")
        return WakeResult(pane, True, "woken", "tmux-send-keys")

    def sleep(seconds):
        clock["t"] += seconds

    launch_wake.run(str(path), wake=wake, sleep=sleep, now=lambda: clock["t"], timeout=9.0, interval=3.0)

    results = {r["key"]: r for r in launch_wake.read("s")["results"]}
    assert results["g/0"]["woken"] is True
    assert results["g/1"]["woken"] is False and "draft" in results["g/1"]["detail"]


def test_a_crash_in_the_wake_is_recorded_as_failed():
    path = _started([_entry()])

    def explode(_entries, **_kw):
        raise RuntimeError("tmux went away")

    launch_wake.run(str(path), wake_resumed=explode)
    state = launch_wake.read("s")
    assert state["status"] == "failed" and "tmux went away" in state["error"]


def test_a_wake_that_never_finished_reads_as_stalled_not_as_running():
    path = _started([_entry()])
    state = json.loads(path.read_text())
    state["started_at"] = time.time() - 3600
    path.write_text(json.dumps(state))
    assert launch_wake.read("s")["status"] == "stalled"


def test_a_file_that_cannot_be_read_is_reported_not_hidden():
    path = launch_wake.state_path("s")
    path.write_text("{not json")
    assert launch_wake.read("s")["status"] == "unreadable"


# ── what a person sees ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_summary_line_counts_what_was_woken_and_names_what_was_not():
    path = _started([_entry("%12", "g/0"), _entry("%13", "g/1")])
    clock = {"t": 0.0}

    def wake(pane):
        ok = pane == "tmux_12"
        detail = "woken" if ok else "not woken: the pane is busy"
        return WakeResult(pane, ok, detail, "tmux-send-keys" if ok else "refused")

    def sleep(seconds):
        clock["t"] += seconds

    launch_wake.run(str(path), wake=wake, sleep=sleep, now=lambda: clock["t"], timeout=9.0, interval=3.0)
    lines = launch_wake.summary_lines(launch_wake.read("s"))
    assert "1 of 2" in lines[0]
    assert any("g/1" in line and "busy" in line for line in lines[1:])


def test_no_file_means_no_lines():
    assert launch_wake.summary_lines(None) == []


# ── the launcher reports the panes it opened (real tmux, private server) ────────────────────────────────────────────────────

FAKE_CLAUDE = """#!/bin/sh
exec sleep 300
"""


@pytest.fixture
def tmux_env(tmp_path, monkeypatch):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    monkeypatch.setenv("TMUX_TMPDIR", str(sock_dir))  # a private tmux server
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("EMPIRICA_INSTANCE_ID", raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    yield proj_dir
    # Explicit socket: -S ignores $TMUX and TMUX_TMPDIR, so this cannot reach a live server.
    private = sock_dir / f"tmux-{os.getuid()}" / "default"
    subprocess.run(["tmux", "-S", str(private), "kill-server"], capture_output=True)


def _config(proj_dir, session):
    return LauncherConfig(
        session_name=session,
        projects=[
            ProjectSpec(name="alpha", path=str(proj_dir), launch="claude --continue"),
            ProjectSpec(name="beta", path=str(proj_dir), launch="claude --continue"),
        ],
        groups=[
            GroupSpec(
                name="g",
                panes=[
                    PaneSpec(project_ref="alpha"),
                    PaneSpec(project_ref="beta"),
                    PaneSpec(inline_command="sleep 300"),
                ],
            )
        ],
    )


def test_a_first_launch_reports_every_pane_it_opened_and_a_second_reports_none(tmux_env):
    from empirica.core.cockpit.launcher.tmux import launch_groups

    cfg = _config(tmux_env, "lw1")
    first = launch_groups(cfg, spawn_terminal=False)
    assert first.error is None
    assert [e["key"] for e in first.opened] == ["g/0", "g/1", "g/2"]
    assert all(e["pane_id"].startswith("%") and e["error"] is None for e in first.opened)
    assert [e["command"] for e in first.opened][:2] == ["claude --continue", "claude --continue"]

    second = launch_groups(cfg, spawn_terminal=False)
    assert second.opened == [], "a running practice was not restarted, so it is not woken"


def test_a_relaunch_reports_only_the_pane_it_added_back(tmux_env):
    from empirica.core.cockpit.launcher.tmux import launch_groups

    cfg = _config(tmux_env, "lw2")
    first = launch_groups(cfg, spawn_terminal=False)
    alpha = next(e["pane_id"] for e in first.opened if e["key"] == "g/0")
    subprocess.run(["tmux", "kill-pane", "-t", alpha], check=True)

    again = launch_groups(cfg, spawn_terminal=False)
    assert [e["key"] for e in again.opened] == ["g/0"]


def test_the_one_window_per_project_layout_reports_its_panes_too(tmux_env):
    from empirica.core.cockpit.launcher.tmux import launch_cockpit

    cfg = LauncherConfig(
        session_name="lw3",
        projects=[
            ProjectSpec(name="alpha", path=str(tmux_env), launch="claude --continue"),
            ProjectSpec(name="beta", path=str(tmux_env), launch="claude --continue"),
        ],
    )
    result = launch_cockpit(cfg)
    assert result.error is None
    assert [e["key"] for e in result.opened] == ["alpha/0", "beta/0"]
    assert launch_cockpit(cfg).opened == []


# ── the launch command starts it and says so ────────────────────────────────────────────────────────────────────────────────


def test_launch_starts_the_wake_for_the_panes_it_opened_and_reports_it(tmux_env, monkeypatch, capsys):
    import empirica.cli.command_handlers.cockpit_launcher_commands as cmd

    started = []
    real_start = launch_wake.start

    def recording_start(opened, session_name, **_kw):
        started.append(([e["key"] for e in opened], session_name))
        return real_start(opened, session_name, popen=Spawned())  # the real rule, without a real process

    monkeypatch.setattr(launch_wake, "start", recording_start)
    cfg = _config(tmux_env, "lw4")
    cfg.surface = "tmux"

    rc = cmd._handle_groups_in_terminal(cfg, "json", no_attach=True)

    assert rc == 0
    assert started == [(["g/0", "g/1", "g/2"], "lw4")]
    wake = json.loads(capsys.readouterr().out)["wake"]
    assert wake["started"] is True and wake["panes"] == 2, "two claude panes; the sleep pane is not woken"

    # A second launch opened nothing, so nothing is started.
    cmd._handle_groups_in_terminal(cfg, "json", no_attach=True)
    assert started[-1] == ([], "lw4")
    assert json.loads(capsys.readouterr().out)["wake"]["started"] is False


def test_status_shows_the_last_launch_wake(tmux_env, capsys):
    """After a launch whose wake finished, status says how many panes were woken and names the one that was not."""
    import empirica.cli.command_handlers.cockpit_launcher_commands as cmd

    path = _started([_entry("%12", "g/0"), _entry("%13", "g/1")])

    def wake(pane):
        ok = pane == "tmux_12"
        return WakeResult(pane, ok, "woken" if ok else "not woken: the pane has a draft", "x" if ok else "unreachable")

    launch_wake.run(str(path), wake=wake, sleep=lambda _s: None)

    lines = cmd._launch_wake_lines(cmd._last_launch_wake("s"))
    assert "1 of 2" in lines[0] and any("g/1" in line and "draft" in line for line in lines)
    assert cmd._last_launch_wake("never-launched") is None
    assert cmd._launch_wake_lines(None) == []


def test_the_detached_command_really_runs_and_writes_the_outcome():
    """The command line `start` hands to Popen, run for real: it imports, reads the file and writes a status."""
    path = launch_wake.state_path("real")
    launch_wake._write(path, {"session_name": "real", "status": "running", "started_at": time.time(), "entries": []})

    subprocess.run([sys.executable, "-c", launch_wake._CHILD, str(path)], check=True, timeout=120)

    record = json.loads(path.read_text())
    assert record["status"] == "done" and record["results"] == [] and record["finished_at"]
