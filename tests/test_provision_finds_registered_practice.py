"""provision-practice NAME finds a practice already registered under that name before defaulting to ~/empirica/NAME
(goal b45387bd, finding 2026-11-13: a practice in ~/empirical-ai got a second directory)."""

from __future__ import annotations

import sqlite3

from empirica.cli.command_handlers import provision_practice_commands as pp


def _registry(home, name, practice_dir):
    db = home / ".empirica" / "workspace" / "workspace.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE global_projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, trajectory_path TEXT)")
        conn.execute("INSERT INTO global_projects VALUES ('p1', ?, ?)", (name, str(practice_dir / ".empirica")))


def test_a_registered_practice_is_found_where_it_lives(tmp_path, monkeypatch):
    home = tmp_path / "home"
    practice = tmp_path / "elsewhere" / "acme-app"
    (practice / ".empirica").mkdir(parents=True)
    _registry(home, "acme-app", practice)
    monkeypatch.setenv("HOME", str(home))
    assert pp._registered_practice_dir("acme-app") == practice


def test_an_unregistered_or_moved_practice_is_not_found(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _registry(home, "gone", tmp_path / "gone")  # registered, but the directory no longer exists
    monkeypatch.setenv("HOME", str(home))
    assert pp._registered_practice_dir("gone") is None
    assert pp._registered_practice_dir("never") is None
    monkeypatch.setenv("HOME", str(tmp_path / "no-registry"))
    assert pp._registered_practice_dir("gone") is None


def test_from_protocol_does_not_clone_a_registered_practice_again(tmp_path, monkeypatch):
    home = tmp_path / "home"
    practice = tmp_path / "elsewhere" / "shared"
    (practice / ".git").mkdir(parents=True)
    _registry(home, "shared", practice)
    monkeypatch.setenv("HOME", str(home))
    step = pp._clone_step(
        {"name": "shared", "repository": "ssh://git@h:2222/a/shared.git"},
        pp._registered_practice_dir("shared") or tmp_path / "practices" / "shared",
        dry_run=True,
    )
    assert step == {"step": "clone", "changed": False, "note": "directory already exists"}
