"""Due triggers, part 2 (goal 1d9040d6): an item can come back up when a release is tagged, not only on a date.

``--due after:<tag>`` stores the condition in ``due_at`` itself. It sorts above every ISO date, so the date query never
fires it; due_items fires it once the tag exists in the project's repository. Everything that already carries ``due_at``
(update-artifacts, notes, rebuild) carries the condition unchanged.
"""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import date

import pytest

from empirica.data.due import DueDateError, due_items, due_lines, parse_due
from tests.schema_shapes import build_db

TODAY = date(2026, 11, 12)


@pytest.fixture
def repo(tmp_path):
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "commit.gpgsign", "false"],
        ["git", "config", "tag.gpgsign", "false"],
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(build_db(tmp_path / "sessions.db", "current"))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _goal(conn, gid, due):
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, status, created_timestamp, is_completed, project_id, archived, due_at, goal_data) "
        "VALUES (?, 's', ?, '{}', 'planned', 0, 0, 'p', 0, ?, '{}')",
        (gid, f"objective {gid}", due),
    )
    conn.commit()


@pytest.mark.parametrize("text", ["after:v1.16.1", " after:v1.16.1 ", "after:release/2026-12"])
def test_parse_due_takes_a_release_condition(text):
    assert parse_due(text, today=TODAY) == text.strip()


@pytest.mark.parametrize("text", ["after:", "after:-rf", "after:v1 2", "after:a..b", "before:v1.0"])
def test_parse_due_refuses_a_condition_it_cannot_check(text):
    with pytest.raises(DueDateError):
        parse_due(text, today=TODAY)


def test_an_item_waiting_on_a_release_is_due_once_the_tag_exists(conn, repo):
    _goal(conn, "g-release", "after:v9.9.9")
    _goal(conn, "g-date", "2026-11-01")
    assert [i["id"] for i in due_items(conn, "p", today=TODAY, root=str(repo))] == ["g-date"]

    subprocess.run(["git", "tag", "v9.9.9"], cwd=repo, check=True)
    items = due_items(conn, "p", today=TODAY, root=str(repo))
    assert [i["id"] for i in items] == ["g-date", "g-release"]
    assert items[1]["due_at"] == "after:v9.9.9" and date.fromisoformat(items[1]["fired"])  # the day the tag was made
    assert "after:v9.9.9" in due_lines(items)[1]


def test_without_a_repository_a_condition_does_not_fire_and_dates_still_do(conn):
    _goal(conn, "g-release", "after:v9.9.9")
    _goal(conn, "g-date", "2026-11-01")
    assert [i["id"] for i in due_items(conn, "p", today=TODAY)] == ["g-date"]


def test_a_snooze_to_a_release_goes_through_update_artifacts():
    from empirica.data.artifact_fields import normalized_values, value_problems

    assert value_problems("goal", {"due_at": "after:v1.17.0"}) == {}
    assert normalized_values({"due_at": "after:v1.17.0"})["due_at"] == "after:v1.17.0"


def test_bootstrap_fires_a_release_condition_from_the_project_root(repo, monkeypatch):
    # Without the root, due_items cannot ask git, and a condition waits forever without a word: bootstrap passes it.
    from empirica.data.session_database import SessionDatabase

    db_path = repo / ".empirica" / "sessions" / "sessions.db"  # where bootstrap's other readers look
    db_path.parent.mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db_path))
    db = SessionDatabase(db_path=str(db_path))
    try:
        pid = db.create_project(name="due-root")
        db.conn.execute(
            "INSERT INTO goals (id, session_id, objective, scope, status, created_timestamp, is_completed, project_id, archived, due_at, goal_data) "
            "VALUES ('g-rel', 's', 'after the release', '{}', 'planned', 0, 0, ?, 0, 'after:v2.0.0', '{}')",
            (pid,),
        )
        db.conn.commit()
        subprocess.run(["git", "tag", "v2.0.0"], cwd=repo, check=True)
        out = db.bootstrap_project_breadcrumbs(pid, project_root=str(repo))
    finally:
        db.close()
    assert [i["id"] for i in out["due"]] == ["g-rel"]


@pytest.mark.parametrize("text", ["after:v1.0+build.1", "after:release/v2"])
def test_valid_git_tag_names_are_accepted(text):
    assert parse_due(text, today=TODAY) == text


@pytest.mark.parametrize("text", ["after:a//b", "after:x.lock", "after:rel/.hidden"])
def test_names_git_cannot_create_are_refused(text):
    with pytest.raises(DueDateError):
        parse_due(text, today=TODAY)


def test_a_tag_nothing_resembles_gets_a_note_and_an_existing_one_says_due(repo, monkeypatch):
    from empirica.data.due import condition_note
    from empirica.data.session_database import SessionDatabase

    db_path = repo / ".empirica" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True)
    subprocess.run(["git", "tag", "v1.16.1"], cwd=repo, check=True)
    db = SessionDatabase(db_path=str(db_path))
    try:
        assert "no tag here looks like '1.17.0'" in condition_note(db.conn, "after:1.17.0")
        assert condition_note(db.conn, "after:v1.17.0") is None  # looks like the others: fine
        assert "due now" in condition_note(db.conn, "after:v1.16.1")
        assert condition_note(db.conn, "2026-12-01") is None
    finally:
        db.close()
