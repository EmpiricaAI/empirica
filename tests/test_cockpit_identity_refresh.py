"""Cockpit panes keep their practitioner identity, and `refresh` brings dead ones back.

Two ports from the per-seat cockpit scripts, which core now replaces
(David, 2026-09-28):

- **Identity.** A pane's EMPIRICA_INSTANCE_ID is what keeps a relaunched claude the
  same practitioner instead of a generic tmux_N. The launcher binds it on the
  pane's creation and records it as the pane option @empirica_instance_id.
- **Refresh.** A claude pane whose claude exited is respawned where it was, with
  the same identity and its conversation resumed. Live panes are never touched.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time

import pytest

from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec, ProjectSpec
from empirica.core.cockpit.launcher.tmux import (
    _wanted_identity,
    assign_identities,
    resume_command,
    slot_id,
)

# ── pure units ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("launch", "expected"),
    [
        ("claude", "claude --continue"),
        ("claude --dangerously-skip-permissions --resume", "claude --dangerously-skip-permissions --continue"),
        ("claude -r", "claude --continue"),
        ("claude --resume 1234-abcd", "claude --resume 1234-abcd"),
        ("claude --continue", "claude --continue"),
        ("ecodex", "ecodex"),
        ("bash", "bash"),
    ],
)
def test_resume_command(launch, expected):
    assert resume_command(ProjectSpec(name="p", path="/x", launch=launch)) == expected


def test_an_explicit_resume_wins():
    proj = ProjectSpec(name="p", path="/x", launch="claude", resume="claude --resume abc")
    assert resume_command(proj) == "claude --resume abc"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("empirica-cortex", "empirica-cortex"), ("My Project", "my-project"), ("2fast", "p-2fast"), ("", "pane")],
)
def test_slot_id_is_slot_shaped(name, expected):
    assert slot_id(name) == expected


def _cfg(*projects, panes=None):
    projs = list(projects)
    panes = panes or [PaneSpec(project_ref=p.name) for p in projs]
    return LauncherConfig(session_name="t", projects=projs, groups=[GroupSpec(name="g", panes=panes)])


def test_claude_and_shell_panes_are_bound_other_programs_are_not():
    """ecodex puts its own UUID into EMPIRICA_INSTANCE_ID; a preset value would override it."""
    cfg = _cfg(
        ProjectSpec(name="a", path="/a", launch="claude --resume"),
        ProjectSpec(name="b", path="/b", launch="bash"),
        ProjectSpec(name="c", path="/c", launch="ecodex"),
    )
    got = {p.project_ref: _wanted_identity(p, cfg) for p in cfg.groups[0].panes}
    assert got == {"a": "a", "b": "b", "c": None}


def test_explicit_instance_id_overrides_and_empty_disables():
    cfg = _cfg(
        ProjectSpec(name="a", path="/a", launch="ecodex", instance_id="lab"),
        ProjectSpec(name="b", path="/b", launch="claude", instance_id=""),
    )
    got = {p.project_ref: _wanted_identity(p, cfg) for p in cfg.groups[0].panes}
    assert got == {"a": "lab", "b": None}


def test_inline_command_panes_are_not_bound_unless_asked():
    cfg = _cfg(panes=[PaneSpec(inline_command="empirica tui"), PaneSpec(inline_command="bash", instance_id="spare")])
    assert [_wanted_identity(p, cfg) for p in cfg.groups[0].panes] == [None, "spare"]


def test_identities_are_unique_within_a_config_and_against_other_sessions(monkeypatch):
    """Two panes sharing an id would share transaction files."""
    from empirica.core.cockpit.launcher import tmux as t

    listing = subprocess.CompletedProcess([], 0, stdout="other\ta\nt\tb\n", stderr="")
    monkeypatch.setattr(t, "_tmux", lambda *a, **k: listing)
    a = ProjectSpec(name="a", path="/a", launch="claude")
    b = ProjectSpec(name="b", path="/b", launch="claude")
    cfg = _cfg(a, b, panes=[PaneSpec(project_ref="a"), PaneSpec(project_ref="a"), PaneSpec(project_ref="b")])

    got = assign_identities(cfg, "t")

    # `a` is live in session "other", so this session's gets suffixed; the second
    # `a` pane gets -2; `b` is live only in THIS session, so it keeps its name.
    assert got == {"g/0": "a-t", "g/1": "a-t-2", "g/2": "b"}


# ── real tmux, private server ────────────────────────────────────────────────

FAKE_CLAUDE = """#!/bin/sh
echo "$EMPIRICA_INSTANCE_ID $*" >> "$FAKE_CLAUDE_LOG"
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
    log = tmp_path / "claude.log"
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    monkeypatch.setenv("TMUX_TMPDIR", str(sock_dir))  # a private tmux server
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("EMPIRICA_INSTANCE_ID", raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    yield proj_dir, log
    subprocess.run(["tmux", "kill-server"], capture_output=True)


def _opt(pane_id: str, name: str) -> str:
    return subprocess.run(
        ["tmux", "show-option", "-pqv", "-t", pane_id, name], capture_output=True, text=True
    ).stdout.strip()


def _panes(session: str) -> list[list[str]]:
    out = subprocess.run(
        ["tmux", "list-panes", "-s", "-t", session, "-F", "#{pane_id}\t#{pane_pid}\t#{pane_dead}\t#{@empirica_pane}"],
        capture_output=True,
        text=True,
    ).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_bind_die_refresh_end_to_end(tmux_env):
    from empirica.core.cockpit.launcher.tmux import _create_group_window, refresh_cockpit

    proj_dir, log = tmux_env
    cfg = LauncherConfig(
        session_name="ctest",
        projects=[ProjectSpec(name="alpha", path=str(proj_dir), launch="claude --resume")],
        groups=[GroupSpec(name="g", panes=[PaneSpec(project_ref="alpha"), PaneSpec(inline_command="sleep 300")])],
    )
    ids = assign_identities(cfg, "ctest")
    created, n, err = _create_group_window(cfg.groups[0], cfg, "ctest", True, ids)
    assert (created, n, err) == (True, 2, None)

    rows = {r[3]: r for r in _panes("ctest")}
    claude_pane = rows["g/0"][0]
    assert _opt(claude_pane, "@empirica_instance_id") == "alpha"
    assert _opt(claude_pane, "remain-on-exit") == "on"
    assert _opt(rows["g/1"][0], "@empirica_instance_id") == ""  # inline pane: not bound
    assert _wait(lambda: log.exists() and log.read_text().strip())
    assert log.read_text().splitlines()[0] == "alpha --resume"

    # A live claude is left alone.
    first = refresh_cockpit(cfg)
    assert first.respawned == [] and first.alive == ["g/0"]

    # claude exits: the pane stays in the layout, dead.
    os.kill(int(rows["g/0"][1]), signal.SIGTERM)
    assert _wait(lambda: {r[3]: r for r in _panes("ctest")}["g/0"][2] == "1")

    second = refresh_cockpit(cfg)
    assert [r["key"] for r in second.respawned] == ["g/0"]
    assert second.respawned[0]["error"] is None
    assert _wait(lambda: len(log.read_text().splitlines()) == 2)
    assert log.read_text().splitlines()[1] == "alpha --continue", "same identity, conversation resumed"
    assert {r[3]: r for r in _panes("ctest")}["g/0"][0] == claude_pane, "respawned in place"


def test_relaunch_adds_back_only_the_pane_that_is_gone(tmux_env):
    from empirica.core.cockpit.launcher.tmux import _create_group_window, refresh_cockpit

    proj_dir, _log = tmux_env
    cfg = LauncherConfig(
        session_name="ctest2",
        projects=[
            ProjectSpec(name="alpha", path=str(proj_dir), launch="claude"),
            ProjectSpec(name="beta", path=str(proj_dir), launch="claude"),
        ],
        groups=[GroupSpec(name="g", panes=[PaneSpec(project_ref="alpha"), PaneSpec(project_ref="beta")])],
    )
    ids = assign_identities(cfg, "ctest2")
    _create_group_window(cfg.groups[0], cfg, "ctest2", True, ids)
    alpha = {r[3]: r for r in _panes("ctest2")}["g/0"][0]
    subprocess.run(["tmux", "kill-pane", "-t", alpha], check=True)

    assert refresh_cockpit(cfg).missing == ["g/0"]

    # The old count-based adopt would have re-added pane 1's spec (beta) here.
    _create_group_window(cfg.groups[0], cfg, "ctest2", False, assign_identities(cfg, "ctest2"))
    keys = sorted(r[3] for r in _panes("ctest2"))
    assert keys == ["g/0", "g/1"]
    new_alpha = {r[3]: r for r in _panes("ctest2")}["g/0"][0]
    assert _opt(new_alpha, "@empirica_instance_id") == "alpha"


def test_panes_from_an_older_launch_are_adopted_only_when_unambiguous(tmux_env, tmp_path):
    """Live cockpits predate the keys; adopting them must not require killing every claude."""
    from empirica.core.cockpit.launcher.tmux import refresh_cockpit

    proj_dir, _ = tmux_env
    other = tmp_path / "other"
    other.mkdir()
    cfg = LauncherConfig(
        session_name="old",
        projects=[
            ProjectSpec(name="alpha", path=str(proj_dir), launch="claude"),
            ProjectSpec(name="beta", path=str(other), launch="claude"),
            ProjectSpec(name="gamma", path=str(other), launch="claude"),
        ],
        groups=[
            GroupSpec(name="g", panes=[PaneSpec(project_ref="alpha")]),
            GroupSpec(name="h", panes=[PaneSpec(project_ref="beta"), PaneSpec(project_ref="gamma")]),
        ],
    )
    # An older launch: same layout, no stamps.
    subprocess.run(["tmux", "new-session", "-d", "-s", "old", "-n", "g", "-c", str(proj_dir), "claude"], check=True)
    subprocess.run(["tmux", "new-window", "-t", "old", "-n", "h", "-c", str(other), "claude"], check=True)

    def settled():
        out = subprocess.run(
            ["tmux", "list-panes", "-s", "-t", "old", "-F", "#{pane_current_command}"], capture_output=True, text=True
        ).stdout.split()
        return len(out) == 2 and "tmux" not in out  # a just-forked pane still reports tmux and the old cwd

    assert _wait(settled)
    result = refresh_cockpit(cfg)

    assert result.adopted == ["g/0"]
    assert result.unkeyed == 1, "beta and gamma share a directory: ambiguous, left untracked"
    assert result.missing == [], "absence cannot be claimed while a pane is untracked"
    alpha = {r[3]: r for r in _panes("old")}["g/0"][0]
    assert _opt(alpha, "@empirica_instance_id") == "alpha"
