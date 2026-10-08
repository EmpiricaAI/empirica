"""goals-activate on a goal that is not `planned` says what to do instead of "not found".

The Sentinel counts a goal as in play for a transaction when it was created in it, or a task on it was created or
completed since PREFLIGHT. goals-activate only moves a planned goal. A practitioner continuing an in_progress goal got
"not found or not in 'planned' status", read it as a defect in the link, and kept working with the "no goal in play"
nudge firing on every call (2026-10-07): the way in is `goals-add-task` after PREFLIGHT.
"""

from __future__ import annotations

import json
import types

import pytest

import empirica.cli.command_handlers.goal_commands as gcmd


def _run(monkeypatch, capsys, status):
    class _Repo:
        def __init__(self, _conn):
            pass

        def activate_goal(self, goal_id, transaction_id=None):
            return False

        def goal_status(self, goal_id):
            return status

    monkeypatch.setattr("empirica.data.repositories.goals.GoalDataRepository", _Repo)
    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase",
        lambda *a, **k: types.SimpleNamespace(conn=object(), close=lambda: None),
    )
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.transaction_read",
        staticmethod(lambda *a, **k: {"transaction_id": "tx", "status": "open"}),
    )
    monkeypatch.setattr("empirica.utils.session_resolver.transaction_open_in_db", lambda *a, **k: True)
    with pytest.raises(SystemExit) as exit_info:
        gcmd.handle_goals_activate_command(types.SimpleNamespace(goal_id="g1", output="json"))
    assert exit_info.value.code == 1
    return json.loads(capsys.readouterr().out)


def test_an_in_progress_goal_is_told_to_add_a_task_after_preflight(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "in_progress")
    assert out["ok"] is False
    assert "already in_progress" in out["error"]
    assert "goals-add-task" in out["error"] and "PREFLIGHT" in out["error"]


def test_a_completed_goal_is_pointed_at_creating_a_new_one(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "completed")
    assert "completed" in out["error"] and "goals-create" in out["error"]


def test_an_unknown_goal_still_reads_as_not_found(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, None)
    assert "not found" in out["error"]
