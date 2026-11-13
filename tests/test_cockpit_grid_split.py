"""A cockpit group can be a grid: `split: grid` with `rows`, filled row by row (prop_pny6loj4nvbndbhrrdkys6qh2e).

David wanted one window of six panes as three over three on a 4K TV. `split` took only horizontal or vertical, so six panes
became six columns. tmux's own `tiled` layout grows rows first and gives six panes three rows of two, not two rows of three,
so `grid` builds the rows and columns itself with percentage splits. `tiled` is accepted too.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec, ProjectSpec, _parse_groups
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
    proj = tmp_path / "proj"
    proj.mkdir()
    yield proj
    private = sock_dir / f"tmux-{os.getuid()}" / "default"
    subprocess.run(["tmux", "-S", str(private), "kill-server"], capture_output=True)


def _window(proj, session, n, split, rows=None):
    group = GroupSpec(name="g", panes=[PaneSpec(inline_command="sleep 300") for _ in range(n)], split=split, rows=rows)
    cfg = LauncherConfig(
        session_name=session, projects=[ProjectSpec(name="p", path=str(proj), launch="true")], groups=[group]
    )
    return group, cfg


def _layout(session):
    """{top: [pane key, ...] left to right}, from the live window."""
    out = subprocess.run(
        ["tmux", "list-panes", "-t", f"={session}:g", "-F", "#{pane_top} #{pane_left} #{@empirica_pane}"],
        capture_output=True,
        text=True,
    ).stdout.split("\n")
    rows: dict[int, list[tuple[int, str]]] = {}
    for line in filter(None, out):
        top, left, key = line.split(" ")
        rows.setdefault(int(top), []).append((int(left), key))
    return [[key for _, key in sorted(cells)] for _, cells in sorted(rows.items())]


def test_six_panes_in_two_rows_are_three_over_three_in_config_order(tmux_env):
    group, cfg = _window(tmux_env, "grid6", 6, "grid", rows=2)
    assert _create_group_window(group, cfg, "grid6", True, {}) == (True, 6, None)
    assert _layout("grid6") == [["g/0", "g/1", "g/2"], ["g/3", "g/4", "g/5"]]


def test_an_uneven_count_fills_the_rows_in_order(tmux_env):
    group, cfg = _window(tmux_env, "grid5", 5, "grid", rows=2)
    assert _create_group_window(group, cfg, "grid5", True, {}) == (True, 5, None)
    assert _layout("grid5") == [["g/0", "g/1", "g/2"], ["g/3", "g/4"]]


def test_grid_defaults_to_two_rows(tmux_env):
    group, cfg = _window(tmux_env, "gridd", 4, "grid")
    _create_group_window(group, cfg, "gridd", True, {})
    assert _layout("gridd") == [["g/0", "g/1"], ["g/2", "g/3"]]


def test_horizontal_is_unchanged_one_row(tmux_env):  # control: the default still gives columns
    group, cfg = _window(tmux_env, "horiz", 3, "horizontal")
    _create_group_window(group, cfg, "horiz", True, {})
    assert _layout("horiz") == [["g/0", "g/1", "g/2"]]


def test_tiled_is_accepted(tmux_env):
    group, cfg = _window(tmux_env, "tiled", 6, "tiled")
    assert _create_group_window(group, cfg, "tiled", True, {}) == (True, 6, None)
    assert sum(len(r) for r in _layout("tiled")) == 6


def test_rows_round_trips_through_the_config_file():
    groups = _parse_groups(
        [{"name": "tv", "split": "grid", "rows": 2, "panes": [{"command": "bash"}, {"command": "bash"}]}]
    )
    assert (groups[0].split, groups[0].rows) == ("grid", 2)
