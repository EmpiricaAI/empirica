"""Cockpit defects found by the v1.14.3..HEAD broccoli sweep, each reproduced before it was fixed.

Profile writer (pure, HOME pinned under tmp_path): a legacy projects-only profile must not flip to
groups mode, a re-provisioned name at a new path is a conflict, a practice named like an existing
window is refused, and a failed write is a ProfileError, not a traceback.

Launcher (a private tmux server, torn down with an explicit socket): tmux matches a bare session or
window target by PREFIX, so every target the launcher builds is spelled exact.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from empirica.core.cockpit.launcher.config import (
    GroupSpec,
    LauncherConfig,
    PaneSpec,
    ProfileError,
    ProjectSpec,
    add_practice_to_profile,
    load_config,
    profile_path,
)
from empirica.core.cockpit.launcher.tmux import (
    _adopt,
    _create_group_window,
    cockpit_session_exists,
    exact_session,
    refresh_cockpit,
    resume_command,
)
from tests.test_cockpit_identity_refresh import tmux_env as tmux_env  # re-export: pytest finds fixtures by name


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


# ── profile writer ───────────────────────────────────────────────────────────


def _legacy(profile: str) -> None:
    f = profile_path(profile)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "session_name: x\nsurface: tmux\nprojects:\n"
        "- {name: a, path: /tmp/a, launch: claude}\n- {name: b, path: /tmp/b, launch: claude}\n"
    )


def test_a_legacy_projects_only_profile_stays_projects_only(tmp_path):
    """Adding a group flipped it to groups mode: only the new group was a window, a and b vanished."""
    _legacy("old")
    assert not load_config(profile_path("old")).is_groups_mode()  # control: it starts legacy

    add_practice_to_profile("old", "z", str(tmp_path / "z"))

    cfg = load_config(profile_path("old"))
    assert not cfg.is_groups_mode() and [p.name for p in cfg.projects] == ["a", "b", "z"]


def test_a_known_name_at_a_different_path_is_a_conflict_not_a_silent_no_op(tmp_path):
    add_practice_to_profile("me", "x", str(tmp_path / "old"))
    before = profile_path("me").read_text()

    with pytest.raises(ProfileError, match=r"already in .* not "):
        add_practice_to_profile("me", "x", str(tmp_path / "new"))

    assert profile_path("me").read_text() == before


def test_the_same_name_at_the_same_path_is_still_idempotent_with_a_tilde(tmp_path):
    """Control for the conflict test: ~ and an absolute spelling of one directory are the same path."""
    add_practice_to_profile("me", "x", "~/proj-x")
    _, changed = add_practice_to_profile("me", "x", str(os.path.expanduser("~/proj-x")))

    assert changed is False


def test_a_practice_named_like_an_existing_window_is_refused(tmp_path):
    """`monitor` is the default TUI window: two groups both keyed monitor/0, and the practice pane
    was never created while the writer reported success."""
    add_practice_to_profile("me", "a", str(tmp_path / "a"))

    with pytest.raises(ProfileError, match="window named 'monitor'"):
        add_practice_to_profile("me", "monitor", str(tmp_path / "m"))


def test_the_first_backup_is_kept_across_later_runs(tmp_path):
    f, _ = add_practice_to_profile("me", "a", str(tmp_path / "a"))
    f.write_text(f.read_text() + "# my note\n")
    add_practice_to_profile("me", "b", str(tmp_path / "b"))
    add_practice_to_profile("me", "c", str(tmp_path / "c"))

    assert "# my note" in f.with_suffix(".yaml.bak").read_text(), "the second run overwrote the user's backup"


def test_an_unwritable_profile_dir_is_a_profile_error(tmp_path, monkeypatch):
    add_practice_to_profile("me", "a", str(tmp_path / "a"))
    d = profile_path("me").parent
    d.chmod(0o500)
    try:
        if os.access(d, os.W_OK):
            pytest.skip("running as a user that ignores directory permissions")
        with pytest.raises(ProfileError, match="could not write"):
            add_practice_to_profile("me", "b", str(tmp_path / "b"))
    finally:
        d.chmod(0o700)
    assert not list(d.glob("*.tmp")), "a failed write must not leave its temp file behind"


# ── pure launcher units ──────────────────────────────────────────────────────


def test_exact_session_spells_the_equals_form():
    assert exact_session("cockpit") == "=cockpit"


@pytest.mark.parametrize(
    ("launch", "expected"),
    [
        ("claude --resume", "claude --continue"),
        ("claude --add-dir ~/notes --resume", "claude --add-dir ~/notes --continue"),
        (
            'claude --append-system-prompt "$(cat ~/p.md)" -r',
            'claude --append-system-prompt "$(cat ~/p.md)" --continue',
        ),
        ("claude --add-dir ~/notes", "claude --add-dir ~/notes --continue"),
    ],
)
def test_resume_command_keeps_the_shell_syntax_of_the_launch_line(launch, expected):
    """A shlex split/join round trip quoted `~`, `$VAR` and `$(...)`, so a refreshed pane lost them."""
    assert resume_command(ProjectSpec(name="p", path="/x", launch=launch)) == expected


def _rows(*panes):
    return [
        {"pane_id": f"%{i}", "dead": "0", "current": "bash", "key": k, "iid": "", "window": w, "path": p}
        for i, (k, w, p) in enumerate(panes)
    ]


def test_a_spare_shell_beside_the_claude_pane_is_not_adopted_as_it(tmp_path):
    """The spare shell was split from the claude pane, so window and directory both match. Two untracked
    panes fit one configured key: neither may take it."""
    d = tmp_path / "proj"
    d.mkdir()
    cfg = LauncherConfig(
        session_name="s",
        projects=[ProjectSpec(name="p", path=str(d), launch="claude")],
        groups=[GroupSpec(name="g", panes=[PaneSpec(project_ref="p")])],
    )
    from empirica.core.cockpit.launcher.tmux import _pane_keys

    specs = dict(_pane_keys(cfg))
    rows = _rows(("", "g", str(d)), ("", "g", str(d)))

    assert _adopt(rows, specs, cfg, {"g/0": "p"}) == []


def test_a_single_untracked_pane_is_still_adopted(tmp_path, monkeypatch):
    """Control for the test above: exactly one fitting pane keeps working."""
    from empirica.core.cockpit.launcher import tmux as t

    d = tmp_path / "proj"
    d.mkdir()
    cfg = LauncherConfig(
        session_name="s",
        projects=[ProjectSpec(name="p", path=str(d), launch="claude")],
        groups=[GroupSpec(name="g", panes=[PaneSpec(project_ref="p")])],
    )
    monkeypatch.setattr(t, "_tmux", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    specs = dict(t._pane_keys(cfg))

    assert _adopt(_rows(("", "g", str(d))), specs, cfg, {"g/0": "p"}) == ["g/0"]


# ── real tmux ────────────────────────────────────────────────────────────────


def _cfg(session, proj_dir, *groups):
    return LauncherConfig(
        session_name=session,
        projects=[ProjectSpec(name="alpha", path=str(proj_dir), launch="claude")],
        groups=list(groups),
    )


def test_a_session_that_does_not_exist_is_not_found_through_a_longer_one(tmux_env):
    proj_dir, _ = tmux_env
    cfg = _cfg("cockpit2", proj_dir, GroupSpec(name="g", panes=[PaneSpec(inline_command="sleep 300")]))
    assert _create_group_window(cfg.groups[0], cfg, "cockpit2", True, {})[0] is True

    assert cockpit_session_exists("cockpit2")  # positive control: the exact name is found
    assert not cockpit_session_exists("cockpit"), "`has-session -t cockpit` matched cockpit2 by prefix"


def test_a_window_is_not_taken_for_a_group_named_by_its_prefix(tmux_env):
    """`cockpit2:api` used to match a window named `api-server`, so group `api` was believed to exist."""
    proj_dir, _ = tmux_env
    server = GroupSpec(name="api-server", panes=[PaneSpec(inline_command="sleep 300")])
    api = GroupSpec(name="api", panes=[PaneSpec(inline_command="sleep 300")])
    cfg = _cfg("wtest", proj_dir, server, api)

    assert _create_group_window(server, cfg, "wtest", True, {}) == (True, 1, None)
    created, panes, err = _create_group_window(api, cfg, "wtest", False, {})

    assert (created, panes, err) == (True, 1, None), "group `api` was adopted from window `api-server`"
    names = subprocess.run(
        ["tmux", "list-windows", "-t", "=wtest", "-F", "#{window_name}"], capture_output=True, text=True
    ).stdout.split()
    assert sorted(names) == ["api", "api-server"]


def test_a_split_that_fails_is_an_error_not_a_success(tmux_env):
    """A detached 80x24 window refused 10 of 13 splits ("no space for new pane") and the launch
    reported success. Thirty panes cannot fit in any window."""
    proj_dir, _ = tmux_env
    g = GroupSpec(name="g", panes=[PaneSpec(inline_command="sleep 300") for _ in range(40)])
    cfg = _cfg("ftest", proj_dir, g)

    created, panes, err = _create_group_window(g, cfg, "ftest", True, {})

    assert created is True and 1 < panes < 40
    assert err is not None and f"{40 - panes} of 39 pane(s) in 'g' not created" in err


def test_refresh_will_not_respawn_a_claude_into_a_directory_that_is_gone(tmux_env):
    """tmux starts the new process in $HOME when -c names a missing directory."""
    proj_dir, _ = tmux_env
    doomed = proj_dir / "gone"
    doomed.mkdir()
    cfg = LauncherConfig(
        session_name="rtest",
        projects=[ProjectSpec(name="alpha", path=str(doomed), launch="claude")],
        groups=[GroupSpec(name="g", panes=[PaneSpec(project_ref="alpha")])],
    )
    assert _create_group_window(cfg.groups[0], cfg, "rtest", True, {"g/0": "alpha"})[2] is None
    pane = subprocess.run(
        ["tmux", "list-panes", "-s", "-t", "=rtest", "-F", "#{pane_id} #{pane_pid}"], capture_output=True, text=True
    ).stdout.split()
    subprocess.run(["kill", pane[1]])
    for _ in range(100):
        dead = subprocess.run(
            ["tmux", "list-panes", "-s", "-t", "=rtest", "-F", "#{pane_dead}"], capture_output=True, text=True
        ).stdout.strip()
        if dead == "1":
            break
        import time

        time.sleep(0.05)
    doomed.rmdir()

    res = refresh_cockpit(cfg)

    assert len(res.respawned) == 1 and res.respawned[0]["error"] and "does not exist" in res.respawned[0]["error"]
    assert res.respawned[0]["command"] is None


def test_the_launch_command_does_not_attach_a_cockpit_that_is_missing_panes(tmux_env, monkeypatch, capsys):
    """`Cockpit ready` then attach, with the warning lost to tmux taking the terminal, hid the failure."""
    from empirica.cli.command_handlers import cockpit_launcher_commands as cmds

    proj_dir, _ = tmux_env
    g = GroupSpec(name="g", panes=[PaneSpec(inline_command="sleep 300") for _ in range(40)])
    cfg = _cfg("atest", proj_dir, g)
    cfg.attach_on_launch = True
    monkeypatch.setattr(os, "execvp", lambda *a, **k: pytest.fail("attached a cockpit with missing panes"))

    rc = cmds._handle_groups_in_terminal(cfg, "human", no_attach=False)

    out = capsys.readouterr().out
    assert rc == 1 and "Cockpit incomplete" in out and "tmux attach -t =atest" in out
