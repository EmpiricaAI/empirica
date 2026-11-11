"""A rebuild from the git notes restores a goal's tasks, completed ones as completed (goal 25f4ab9a).

Task notes were written only on completion, under one ref per goal with ``add -f`` on HEAD, so two tasks of one goal
completed on the same commit kept only the last, and open tasks had no note at all. Goal notes now carry the goal's task
list, rewritten whenever a task is added or completed. Round trip in a throwaway repo: a goal, three tasks, two completed
on the same commit, rebuild from the notes alone.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.cli.command_handlers.sync_commands import _rebuild_ensure_goals
from empirica.core.tasks.repository import TaskRepository
from empirica.core.tasks.types import SubTask, TaskStatus
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def practice(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / ".empirica" / "sessions" / "sessions.db"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_tasks_added_and_completed_come_back_from_the_notes(practice, tmp_path):
    db = SessionDatabase()
    try:
        session_id = db.create_session(ai_id="t")
        goal_id = db.create_goal(session_id=session_id, objective="ship x")
    finally:
        db.close()
    repo = TaskRepository()
    ids = []
    for text in ("read", "write", "test"):
        task = SubTask.create(goal_id=goal_id, description=text)
        assert repo.save_subtask(task)
        ids.append(task.id)
    assert repo.update_subtask_status(ids[0], TaskStatus.COMPLETED, "commit abc")
    assert repo.update_subtask_status(ids[1], TaskStatus.COMPLETED, "commit abc")  # same HEAD as the first

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    fresh.conn.execute("PRAGMA foreign_keys=OFF")
    try:
        rebuilt: dict = {"goals": 0}
        _rebuild_ensure_goals(fresh, 0.0, rebuilt)
        rows = {
            r[0]: r[1:]
            for r in fresh.conn.execute(
                "SELECT id, goal_id, description, status, completion_evidence FROM subtasks"
            ).fetchall()
        }
        goal_data = fresh.conn.execute("SELECT goal_data FROM goals WHERE id = ?", (goal_id,)).fetchone()[0]
    finally:
        fresh.close()
    assert rebuilt["tasks"] == 3
    assert tuple(rows[ids[0]]) == (goal_id, "read", "completed", "commit abc")
    assert tuple(rows[ids[1]]) == (goal_id, "write", "completed", "commit abc")
    assert tuple(rows[ids[2]]) == (goal_id, "test", "pending", None)
    assert '"tasks"' not in goal_data  # the list rides in the note, not in the rebuilt row
