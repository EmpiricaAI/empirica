"""A live cockpit pane is matched to its configured pane by what it is, not by where it sits (prop_6k4mvcdb7bb45orydga7ejexum).

Panes are keyed `<group>/<index>`. Augmenting a live window took every configured key with no stamped pane as missing, so a
pane inserted in the middle of a group shifted every key after it: the live ecodex-lab pane (lab/1) answered for the new
pane's key, ecodex-lab's own key (now lab/2) looked missing, a second ecodex-lab pane was spawned, the new pane never came up,
and launch reported success. Removing or reordering a pane had the same flaw.

Each pane now also carries what it is (@empirica_pane_ident: project, label or command, with an ordinal for repeats). On
launch and refresh, live panes are re-keyed to the configured pane with the same identity, a pane that left the config loses
its key, and a missing pane is split next to its configured neighbour. A pane stamped before identities existed keeps its key
only when its directory still fits that slot; otherwise it is adopted by directory, as before.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec, ProjectSpec
from empirica.core.cockpit.launcher.tmux import _create_group_window


@pytest.fixture
def tmux_env(tmp_path, monkeypatch):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    monkeypatch.setenv("TMUX_TMPDIR", str(sock_dir))  # a private tmux server
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("EMPIRICA_INSTANCE_ID", raising=False)
    dirs = {}
    for name in ("alpha", "beta", "gamma"):
        dirs[name] = tmp_path / name
        dirs[name].mkdir()
    yield dirs
    private = sock_dir / f"tmux-{os.getuid()}" / "default"
    subprocess.run(["tmux", "-S", str(private), "kill-server"], capture_output=True)


def _cfg(dirs, session, refs):
    projects = [ProjectSpec(name=n, path=str(p), launch="sleep 300") for n, p in dirs.items()]
    group = GroupSpec(name="g", panes=[PaneSpec(project_ref=r) for r in refs])
    return group, LauncherConfig(session_name=session, projects=projects, groups=[group])


def _panes(session):
    """[(key, directory name)] in tmux's pane order.

    A pane split a moment ago can still report the directory tmux itself runs in until its process has started, so this
    waits (up to 2 s) until every pane reports one of the projects' directories.
    """
    import time

    deadline = time.monotonic() + 2
    while True:
        out = subprocess.run(
            ["tmux", "list-panes", "-t", f"={session}:g", "-F", "#{@empirica_pane}\t#{pane_current_path}"],
            capture_output=True,
            text=True,
        ).stdout
        rows = [(k, os.path.basename(p)) for k, p in (line.split("\t") for line in out.splitlines() if line)]
        if all(d in ("alpha", "beta", "gamma") for _, d in rows) or time.monotonic() > deadline:
            return rows
        time.sleep(0.05)


def test_a_pane_inserted_mid_group_comes_up_and_nothing_is_duplicated(tmux_env):
    group, cfg = _cfg(tmux_env, "ins", ["alpha", "beta"])
    _create_group_window(group, cfg, "ins", True, {})
    group, cfg = _cfg(tmux_env, "ins", ["alpha", "gamma", "beta"])

    created, panes, err = _create_group_window(group, cfg, "ins", True, {})

    assert (created, panes, err) == (False, 3, None)
    assert _panes("ins") == [("g/0", "alpha"), ("g/1", "gamma"), ("g/2", "beta")]


def test_reordering_spawns_nothing_and_re_keys_the_live_panes(tmux_env):
    group, cfg = _cfg(tmux_env, "reo", ["alpha", "beta"])
    _create_group_window(group, cfg, "reo", True, {})
    group, cfg = _cfg(tmux_env, "reo", ["beta", "alpha"])

    assert _create_group_window(group, cfg, "reo", True, {}) == (False, 2, None)
    assert sorted(_panes("reo")) == [("g/0", "beta"), ("g/1", "alpha")]


def test_a_removed_pane_loses_its_key_and_is_not_mistaken_for_the_one_now_in_its_slot(tmux_env):
    group, cfg = _cfg(tmux_env, "rem", ["alpha", "beta"])
    _create_group_window(group, cfg, "rem", True, {})
    group, cfg = _cfg(tmux_env, "rem", ["beta"])

    assert _create_group_window(group, cfg, "rem", True, {}) == (False, 1, None)
    assert sorted(_panes("rem")) == [("", "alpha"), ("g/0", "beta")]  # alpha is left running, untracked


def test_an_unchanged_group_is_left_alone(tmux_env):  # control
    group, cfg = _cfg(tmux_env, "same", ["alpha", "beta"])
    _create_group_window(group, cfg, "same", True, {})
    assert _create_group_window(group, cfg, "same", True, {}) == (False, 2, None)
    assert _panes("same") == [("g/0", "alpha"), ("g/1", "beta")]


def test_a_pane_stamped_before_identities_keeps_its_key_only_where_it_still_fits(tmux_env):
    # The window mesh-support hit: positional stamps, no identity, then a pane inserted mid-group.
    group, cfg = _cfg(tmux_env, "old", ["alpha", "beta"])
    _create_group_window(group, cfg, "old", True, {})
    subprocess.run(
        ["tmux", "set-option", "-p", "-t", f"={'old'}:g.0", "-u", "@empirica_pane_ident"], capture_output=True
    )
    subprocess.run(
        ["tmux", "set-option", "-p", "-t", f"={'old'}:g.1", "-u", "@empirica_pane_ident"], capture_output=True
    )
    group, cfg = _cfg(tmux_env, "old", ["alpha", "gamma", "beta"])

    assert _create_group_window(group, cfg, "old", True, {}) == (False, 3, None)
    assert sorted(_panes("old")) == [("g/0", "alpha"), ("g/1", "gamma"), ("g/2", "beta")]
