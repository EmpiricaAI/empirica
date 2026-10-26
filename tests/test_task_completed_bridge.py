"""The task-to-goal bridge completes the goal a task was for, in the project it was for.

task-completed.py auto-completes a goal when a Claude Code task finishes. It searched ~/.empirica/sessions/sessions.db
rather than the active project's database, selected `is_completed = 0` (which includes abandoned and planned goals: the
schema says readers must filter `status` literally), matched `metadata.claude_task_id` against the task SUBJECT although
nothing ever writes that key, and let a short subject that was a substring of any objective score 0.9. A goal closed by
accident has no evidence behind it, so every one of those is a way to close the wrong goal.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "task-completed.py"
)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # the hook opens a log file under Path.home() at import
    (tmp_path / "home").mkdir()
    spec = importlib.util.spec_from_file_location("task_completed_bridge", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _db(tmp_path, goals):
    path = tmp_path / "proj" / ".empirica" / "sessions" / "sessions.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE goals (id TEXT PRIMARY KEY, objective TEXT, goal_data TEXT, status TEXT, "
        "is_completed INTEGER DEFAULT 0, created_timestamp REAL)"
    )
    for i, (objective, status, metadata) in enumerate(goals):
        conn.execute(
            "INSERT INTO goals VALUES (?, ?, ?, ?, 0, ?)",
            (f"goal-{i}", objective, json.dumps({"metadata": metadata}), status, float(i)),
        )
    conn.commit()
    conn.close()
    return path


OBJECTIVE = "Fix the sentinel gate remote classifier"


def test_only_a_goal_in_progress_can_be_completed_by_a_task(bridge, tmp_path):
    db = _db(tmp_path, [(OBJECTIVE, "abandoned", {}), (OBJECTIVE, "planned", {})])
    assert bridge._find_matching_goal(db, OBJECTIVE, "") is None


def test_an_in_progress_goal_with_the_same_objective_is_still_found(bridge, tmp_path):
    db = _db(tmp_path, [(OBJECTIVE, "abandoned", {}), (OBJECTIVE, "in_progress", {})])
    match = bridge._find_matching_goal(db, OBJECTIVE, "")
    assert match and match["goal_id"] == "goal-1" and match["match_type"] == "fuzzy"


def test_a_goal_carrying_a_claude_task_id_in_its_metadata_is_not_matched_by_it(bridge, tmp_path):
    """The exact-id branch is gone (nothing ever wrote the key): only the objective can match, so an unrelated objective never does."""
    db = _db(tmp_path, [("Something unrelated entirely", "in_progress", {"claude_task_id": "task-7"})])
    assert bridge._find_matching_goal(db, "a different subject", "") is None
    assert bridge._find_matching_goal(db, "task-7", "") is None


def test_a_short_subject_inside_a_long_objective_does_not_complete_it(bridge, tmp_path):
    db = _db(tmp_path, [(OBJECTIVE, "in_progress", {})])
    assert bridge._find_matching_goal(db, "gate", "") is None


def test_a_fuller_subject_inside_the_objective_still_matches(bridge, tmp_path):
    db = _db(tmp_path, [(OBJECTIVE, "in_progress", {})])
    match = bridge._find_matching_goal(db, "fix the sentinel gate remote", "")
    assert match and match["goal_id"] == "goal-0"


def test_the_bridge_reads_the_active_projects_database_not_the_home_one(bridge, tmp_path, monkeypatch):
    db = _db(tmp_path, [(OBJECTIVE, "in_progress", {})])
    home_db = tmp_path / "home" / ".empirica" / "sessions" / "sessions.db"
    home_db.parent.mkdir(parents=True)
    home_db.write_text("not the project's database")
    monkeypatch.setattr(bridge, "get_active_project_path", lambda sid=None: str(tmp_path / "proj"))
    assert bridge._get_db_path("cc-session") == db


def test_with_no_resolvable_project_the_bridge_has_no_database(bridge, tmp_path, monkeypatch):
    home_db = tmp_path / "home" / ".empirica" / "sessions" / "sessions.db"
    home_db.parent.mkdir(parents=True)
    home_db.write_text("x")
    monkeypatch.setattr(bridge, "get_active_project_path", lambda sid=None: None)
    assert bridge._get_db_path("cc-session") is None
