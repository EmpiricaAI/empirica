"""`cockpit refresh` brings back a dead codex or ecodex pane too (ecodex's sweep of 1.14.15, goal 87772ac4).

`resume_command` already knows codex and ecodex (`<launch> resume --last`), but refresh gated every pane on `_runs_claude`, so a dead ecodex pane was
never respawned and was not even reported as missing. Two more gates sat behind that one: a pane is kept after its program exits (remain-on-exit) only
when an identity is bound and the program is claude, and ecodex gets no identity (it puts its own UUID in EMPIRICA_INSTANCE_ID), so a dead ecodex pane
simply disappeared and there was nothing to respawn. The fake tmux below answers list-panes from a table and records every other call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from empirica.core.cockpit.launcher import tmux as T
from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec, ProjectSpec


def _cfg(tmp_path: Path, **launches: str) -> LauncherConfig:
    projects = []
    for name, launch in launches.items():
        (tmp_path / name).mkdir(exist_ok=True)
        projects.append(ProjectSpec(name=name, path=str(tmp_path / name), launch=launch))
    panes = [PaneSpec(project_ref=p.name) for p in projects]
    return LauncherConfig(session_name="t", projects=projects, groups=[GroupSpec(name="g", panes=panes)])


class FakeTmux:
    def __init__(self, rows: list[tuple[str, ...]]):
        self.rows = rows
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, check: bool = False):
        self.calls.append(args)
        out = "\n".join("\t".join(r) for r in self.rows) if args and args[0] == "list-panes" else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    def respawns(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c and c[0] == "respawn-pane"]


@pytest.fixture
def fake(monkeypatch):
    def install(rows):
        tmux = FakeTmux(rows)
        monkeypatch.setattr(T, "_tmux", tmux)
        monkeypatch.setattr(T, "tmux_available", lambda: True)
        monkeypatch.setattr(T, "cockpit_session_exists", lambda _name: True)
        monkeypatch.setattr(T, "strip_session_identity", lambda _name: None)
        return tmux

    return install


def _row(pane_id, dead, current, key, window="g", path=""):
    return (pane_id, dead, current, key, "", window, path)


def test_a_dead_ecodex_pane_is_respawned_with_its_resume_command(fake, tmp_path):
    cfg = _cfg(tmp_path, eco="ecodex")
    tmux = fake([_row("%1", "1", "ecodex", "g/0", path=str(tmp_path / "eco"))])
    result = T.refresh_cockpit(cfg)
    assert [r["command"] for r in result.respawned] == ["ecodex resume --last"], result
    (call,) = tmux.respawns()
    assert call[-1] == "ecodex resume --last" and "%1" in call


def test_a_dead_codex_pane_is_respawned_too(fake, tmp_path):
    cfg = _cfg(tmp_path, cx="codex --model x")
    fake([_row("%1", "1", "codex", "g/0")])
    assert [r["command"] for r in T.refresh_cockpit(cfg).respawned] == ["codex --model x resume --last"]


def test_a_live_ecodex_pane_is_left_alone_and_reported_alive(fake, tmp_path):
    cfg = _cfg(tmp_path, eco="ecodex")
    tmux = fake([_row("%1", "0", "ecodex", "g/0")])
    result = T.refresh_cockpit(cfg)
    assert result.alive == ["g/0"] and result.respawned == [] and tmux.respawns() == []


def test_a_configured_ecodex_pane_that_is_gone_is_reported_missing(fake, tmp_path):
    cfg = _cfg(tmp_path, eco="ecodex")
    fake([])
    assert T.refresh_cockpit(cfg).missing == ["g/0"]


def test_a_pane_that_runs_something_else_is_still_never_touched(fake, tmp_path):
    """Control: widening the gate to the programs resume_command knows does not make every dead pane respawnable."""
    cfg = _cfg(tmp_path, sh="bash", eco="ecodex")
    tmux = fake([_row("%1", "1", "bash", "g/0"), _row("%2", "0", "ecodex", "g/1")])
    result = T.refresh_cockpit(cfg)
    assert tmux.respawns() == [] and result.alive == ["g/1"] and result.missing == []


def test_a_dead_claude_pane_is_still_respawned(fake, tmp_path):
    cfg = _cfg(tmp_path, c="claude")
    fake([_row("%1", "1", "claude", "g/0")])
    assert [r["command"] for r in T.refresh_cockpit(cfg).respawned] == ["claude --continue"]


@pytest.mark.parametrize(
    ("launch", "iid", "keeps"),
    [
        ("claude", "c", True),
        ("claude", None, False),  # an explicit empty instance_id disables identity, and with it the respawn
        ("ecodex", None, True),  # no identity is bound for ecodex, and its pane must still be kept to be respawned
        ("codex", None, True),
        ("bash", "b", False),
    ],
)
def test_which_panes_are_kept_after_their_program_exits(tmp_path, launch, iid, keeps):
    cfg = _cfg(tmp_path, p=launch)
    assert T._keeps_on_exit(cfg.groups[0].panes[0], iid, cfg) is keeps
