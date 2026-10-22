"""A rebuilt goal keeps what its note carries and says what the note cannot (goal d897e750, medium item).

``_rebuild_ensure_goals`` stamped every goal with the rebuild instant, left ``project_id`` NULL and ``ai_id`` empty, and reported
nothing about the fact that the note records no status: a goal note is written once at creation and never updated (lineage only
ever records "resumed"), so a completed goal rebuilds as ``in_progress`` and returns to the active list as if it were current.

The note carries the creation time and the creating AI (kept in goal_data: the goals table has no creator column); the project is recoverable from the artifacts that name the goal. The
status is NOT recoverable, so the rebuild counts those goals and marks each one rather than letting the default read as a fact.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

import empirica.cli.command_handlers.sync_commands as sc
from empirica.data.session_database import SessionDatabase

WHEN = "2026-03-04T05:06:07+00:00"
WHEN_TS = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc).timestamp()
PID, SID = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    d.conn.execute("PRAGMA foreign_keys=OFF")
    yield d
    d.close()


def _store(monkeypatch, goals):
    class _Store:
        def discover_goals(self, *a, **k):
            return goals

    monkeypatch.setattr("empirica.core.canonical.empirica_git.goal_store.GitGoalStore", _Store)


def _goal(**data):
    return {
        "goal_id": str(uuid.uuid4()),
        "session_id": SID,
        "ai_id": "empirica",
        "created_at": WHEN,
        "goal_data": {"objective": "ship it", **data},
    }


def _row(db, gid):
    return db.conn.execute(
        "SELECT created_timestamp, project_id, status, goal_data FROM goals WHERE id = ?", (gid,)
    ).fetchone()


def test_the_creation_time_and_creator_come_from_the_note(db, monkeypatch):
    g = _goal()
    _store(monkeypatch, [g])
    rebuilt = {"goals": 0}
    sc._rebuild_ensure_goals(db, 1.0, rebuilt)
    created, _, _, data = _row(db, g["goal_id"])
    assert created == pytest.approx(WHEN_TS) and json.loads(data)["created_by_ai"] == "empirica"
    assert rebuilt["goals"] == 1


def test_the_project_is_recovered_from_the_artifacts_that_name_the_goal(db, monkeypatch):
    g = _goal()
    _store(monkeypatch, [g])
    finding = {"project_id": PID, "session_id": SID, "goal_id": g["goal_id"], "finding": "x"}
    sc._rebuild_ensure_goals(db, 1.0, {"goals": 0}, [[finding]])
    assert _row(db, g["goal_id"])[1] == PID


def test_a_goal_no_artifact_names_keeps_a_null_project(db, monkeypatch):
    g = _goal()
    _store(monkeypatch, [g])
    other = {"project_id": PID, "session_id": SID, "goal_id": str(uuid.uuid4()), "finding": "x"}
    sc._rebuild_ensure_goals(db, 1.0, {"goals": 0}, [[other]])
    assert _row(db, g["goal_id"])[1] is None, "control: only a goal an artifact names inherits its project"


def test_a_goal_whose_note_carries_a_status_keeps_it_and_is_not_flagged(db, monkeypatch):
    g = _goal(status="completed", project_id=PID)
    _store(monkeypatch, [g])
    rebuilt = {"goals": 0}
    sc._rebuild_ensure_goals(db, 1.0, rebuilt)
    _, project, status, data = _row(db, g["goal_id"])
    assert status == "completed" and project == PID
    assert "status_unknown" not in json.loads(data) and not rebuilt.get("goals_status_unknown")


def test_a_goal_whose_note_has_no_status_is_marked_and_counted(db, monkeypatch):
    g = _goal()
    _store(monkeypatch, [g])
    rebuilt = {"goals": 0}
    sc._rebuild_ensure_goals(db, 1.0, rebuilt)
    assert json.loads(_row(db, g["goal_id"])[3])["status_unknown"] is True
    assert rebuilt["goals_status_unknown"] == 1


def test_the_unknown_status_count_is_not_a_record_count(db, monkeypatch, capsys):
    """It rides in the rebuild result, so the total and the human listing must not treat it as rebuilt records."""
    monkeypatch.setattr(sc, "_rebuild_from_notes", lambda: {"goals": 2, "goals_status_unknown": 2})
    import types

    rc = sc.handle_rebuild_command(
        types.SimpleNamespace(output="json", from_notes=True, qdrant=False, qdrant_only=False, verbose=False)
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["total"] == 2
