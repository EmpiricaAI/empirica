"""The statusline's G counts the goals that are still open, not the abandoned ones.

`get_open_counts` counted every goal with is_completed = 0. An abandoned goal is dead, not open, so after a triage that abandoned
14 goals, G read 98 and "would not fall further" (mesh-support, David-approved 2026-10-14): the number he watches could not show the
cleanup. G is in_progress + planned.
"""

from __future__ import annotations

import importlib.util
import sys
import time
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/scripts/statusline_empirica.py"
PROJECT = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
SESSION = str(uuid.uuid4())


@pytest.fixture(scope="module")
def sl():
    spec = importlib.util.spec_from_file_location("statusline_empirica_goals", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["statusline_empirica_goals"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db(tmp_path):
    from empirica.data.session_database import SessionDatabase

    d = SessionDatabase(db_path=str(tmp_path / "goals.db"))
    yield d
    d.close()


def _goal(db, status: str, project: str = PROJECT):
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, status, is_completed, project_id, created_timestamp, goal_data) "
        "VALUES (?, ?, ?, '{}', ?, ?, ?, ?, '{}')",
        (str(uuid.uuid4()), SESSION, f"goal {status}", status, 1 if status == "completed" else 0, project, time.time()),
    )
    db.conn.commit()


def test_abandoned_goals_are_not_open(sl, db):
    for status in ("in_progress", "in_progress", "planned", "abandoned", "abandoned", "abandoned"):
        _goal(db, status)
    assert sl.get_open_counts(db, SESSION, PROJECT)["open_goals"] == 3


def test_completed_goals_are_still_not_open(sl, db):
    for status in ("in_progress", "completed", "completed"):
        _goal(db, status)
    assert sl.get_open_counts(db, SESSION, PROJECT)["open_goals"] == 1


def test_the_case_from_the_report_15_in_progress_33_planned_50_abandoned_reads_48(sl, db):
    for status, n in (("in_progress", 15), ("planned", 33), ("abandoned", 50)):
        for _ in range(n):
            _goal(db, status)
    assert sl.get_open_counts(db, SESSION, PROJECT)["open_goals"] == 48


def test_another_projects_goals_are_not_counted(sl, db):
    _goal(db, "in_progress")
    _goal(db, "planned", project=OTHER)
    assert sl.get_open_counts(db, SESSION, PROJECT)["open_goals"] == 1


def test_without_a_project_id_abandoned_goals_are_still_excluded(sl, db):
    _goal(db, "planned")
    _goal(db, "abandoned")
    assert sl.get_open_counts(db, SESSION, None)["open_goals"] == 1


def test_a_null_status_goal_that_is_not_completed_still_counts_as_open(sl, db):
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, status, is_completed, project_id, created_timestamp, goal_data) "
        "VALUES (?, ?, 'legacy', '{}', NULL, 0, ?, ?, '{}')",
        (str(uuid.uuid4()), SESSION, PROJECT, time.time()),
    )
    db.conn.commit()
    assert sl.get_open_counts(db, SESSION, PROJECT)["open_goals"] == 1
