"""The cockpit's tmux server does not inherit a stale user manager's groups (goal 41f000f0).

ensure_server starts tmux with systemd-run --user, so its panes get the groups of the long-running systemd --user manager,
not the login's. After a later group grant (practice folders are group-restricted, 2770), the manager still had the old
set and three of five practice panes opened in the home folder (mesh-support, measured on a shared host).
"""

from __future__ import annotations

import subprocess

from empirica.core.cockpit.launcher import tmux


def _proc(tmp_path, pid, groups):
    d = tmp_path / str(pid)
    d.mkdir()
    (d / "status").write_text(f"Name:\tsystemd\nGroups:\t{' '.join(map(str, groups))} \n")
    return tmp_path


def _run_pid(pid):
    return lambda argv, **kw: subprocess.CompletedProcess(argv, 0, f"{pid}\n", "")


def test_groups_the_login_has_and_the_manager_lacks(tmp_path):
    proc = _proc(tmp_path, 4242, [100, 200])
    missing = tmux.manager_missing_groups(run=_run_pid(4242), proc_root=proc, login_groups={100, 200, 300})
    assert missing == {300}


def test_a_current_manager_lacks_nothing(tmp_path):
    proc = _proc(tmp_path, 4242, [100, 200, 300])
    assert tmux.manager_missing_groups(run=_run_pid(4242), proc_root=proc, login_groups={100, 200, 300}) == set()


def test_no_manager_pid_is_not_a_difference(tmp_path):
    assert tmux.manager_missing_groups(run=_run_pid(0), proc_root=tmp_path, login_groups={1}) == set()
