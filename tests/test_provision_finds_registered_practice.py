"""provision-practice NAME finds a practice already registered under that name before defaulting to ~/empirica/NAME
(goal b45387bd, finding 2026-11-13: a practice in ~/empirical-ai got a second directory)."""

from __future__ import annotations

import sqlite3

from empirica.cli.command_handlers import provision_practice_commands as pp


def _registry(home, name, practice_dir):
    db = home / ".empirica" / "workspace" / "workspace.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE global_projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, trajectory_path TEXT, status TEXT)"
        )
        conn.execute(
            "INSERT INTO global_projects VALUES ('p1', ?, ?, 'active')", (name, str(practice_dir / ".empirica"))
        )


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
    (practice / ".empirica").mkdir()
    _registry(home, "shared", practice)
    monkeypatch.setenv("HOME", str(home))
    step = pp._clone_step(
        {"name": "shared", "repository": "ssh://git@h:2222/a/shared.git"},
        pp._registered_practice_dir("shared") or tmp_path / "practices" / "shared",
        dry_run=True,
    )
    assert step == {"step": "clone", "changed": False, "note": "directory already exists"}


def test_through_the_cli_parser_a_registered_practice_is_used(tmp_path, monkeypatch):
    """The sweep found the lookup dead from the CLI: the parser defaulted --base-path to ~/empirica."""
    import argparse

    from empirica.cli.parsers.provision_practice_parsers import add_provision_practice_parsers

    home = tmp_path / "home"
    practice = tmp_path / "elsewhere" / "acme-app"
    (practice / ".empirica").mkdir(parents=True)
    _registry(home, "Acme App", practice)  # a display name; the directory carries the practice name, which selects
    monkeypatch.setenv("HOME", str(home))
    parser = argparse.ArgumentParser()
    add_provision_practice_parsers(parser.add_subparsers())
    args = parser.parse_args(["provision-practice", "acme-app", "--dry-run", "--no-cortex", "--output", "json"])
    assert args.base_path is None
    seen = {}
    monkeypatch.setattr(pp, "_init_step", lambda a, proj_dir, *_r: (seen.setdefault("dir", proj_dir), "stop"))
    pp.handle_provision_practice_command(args)
    assert seen["dir"] == practice.resolve()


def test_a_legacy_row_storing_the_root_resolves_to_that_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    practice = tmp_path / "legacy" / "mdview"
    (practice / ".empirica").mkdir(parents=True)
    db = home / ".empirica" / "workspace" / "workspace.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE global_projects (id TEXT PRIMARY KEY, name TEXT, trajectory_path TEXT, status TEXT)")
        conn.execute("INSERT INTO global_projects VALUES ('a', 'mdview', ?, 'active')", (str(practice),))
        conn.execute("INSERT INTO global_projects VALUES ('b', 'mdview', ?, 'active')", (str(practice / ".empirica"),))
    monkeypatch.setenv("HOME", str(home))
    assert pp._registered_practice_dir("mdview") == practice.resolve()


def test_a_display_name_never_selects_a_practice(tmp_path, monkeypatch):
    """Second pass (goal d6a5f502): `provision-practice Empirica` matched the display name and would rewrite the
    practice's ai_id to it. Only the directory name selects."""
    home = tmp_path / "home"
    practice = tmp_path / "elsewhere" / "empirica"
    (practice / ".empirica").mkdir(parents=True)
    _registry(home, "Empirica", practice)
    monkeypatch.setenv("HOME", str(home))
    assert pp._registered_practice_dir("Empirica") is None
    assert pp._registered_practice_dir("empirica") == practice.resolve()


def test_an_older_registry_without_a_status_column_still_answers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    practice = tmp_path / "elsewhere" / "old"
    (practice / ".empirica").mkdir(parents=True)
    db = home / ".empirica" / "workspace" / "workspace.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE global_projects (id TEXT PRIMARY KEY, name TEXT, trajectory_path TEXT)")
        conn.execute("INSERT INTO global_projects VALUES ('a', 'Old', ?)", (str(practice / ".empirica"),))
    monkeypatch.setenv("HOME", str(home))
    assert pp._registered_practice_dir("old") == practice.resolve()
