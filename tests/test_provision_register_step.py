"""provision-practice's project-register step reports a change only for a new registration (gardening 2026-11-12).

The step set changed to the command's success, so every idempotent re-run said changed:true (unknown 599327d0).
"""

from __future__ import annotations

import sqlite3
import types

from empirica.cli.command_handlers import provision_practice_commands as pp


def _registry(home, traj=None):
    db = home / ".empirica" / "workspace" / "workspace.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE global_projects (id TEXT PRIMARY KEY, trajectory_path TEXT)")
    if traj:
        conn.execute("INSERT INTO global_projects VALUES ('p', ?)", (traj,))
    conn.commit()
    conn.close()


def _ok(cmd, cwd, dry_run):
    return True, "{}"


def test_a_new_registration_is_a_change(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _registry(tmp_path / "home")
    step = pp._register_step(types.SimpleNamespace(no_cortex=True), tmp_path / "demo", False, run=_ok)
    assert step["changed"] is True and "already_registered" not in step


def test_a_re_run_over_a_registered_practice_is_not_a_change(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proj = tmp_path / "demo"
    (proj / ".empirica").mkdir(parents=True)
    _registry(tmp_path / "home", str((proj / ".empirica").resolve()))
    step = pp._register_step(types.SimpleNamespace(no_cortex=True), proj, False, run=_ok)
    assert step["changed"] is False and step["already_registered"] is True


def test_a_failed_registration_is_no_change_and_carries_the_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    step = pp._register_step(
        types.SimpleNamespace(no_cortex=False), tmp_path / "demo", False, run=lambda *a: (False, "boom")
    )
    assert step == {"step": "project-register", "changed": False, "error": "boom"}
