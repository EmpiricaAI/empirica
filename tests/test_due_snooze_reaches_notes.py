"""A snooze or a cleared due date reaches the note (1.15.0 sweep, D3c; goal 8a26a2c3).

update-artifacts and PATCH /artifacts/{id} changed `due_at` in SQLite only, so a rebuild brought back the date the item was
created with. Each now rewrites the note from the row: a goal through the lifecycle restamp, an unknown or assumption by
re-storing its note with every other key kept. Built in a throwaway git repo holding the practice's database.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import graph_commands as gc
from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore
from empirica.core.canonical.empirica_git.due_note import restamp_due
from empirica.core.canonical.empirica_git.goal_store import GitGoalStore
from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def practice(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / ".empirica" / "sessions" / "sessions.db"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _db(practice):
    return SessionDatabase(db_path=str(practice / ".empirica" / "sessions" / "sessions.db"))


def _logged(practice):
    out = gc.log_artifacts_graph(
        {"nodes": [
            {"ref": "u", "type": "unknown", "data": {"unknown": "when?", "due": "2026-12-01"}},
            {"ref": "a", "type": "assumption", "data": {"assumption": "y", "confidence": 0.6, "due": "2026-12-02"}},
        ], "edges": []},
        session_id="sess-1", project_id="proj-1", project_path=str(practice),
    )  # fmt: skip
    assert out["ok"] and not out["errors"], out
    return out["created"]


def _update(payload) -> dict:
    args = SimpleNamespace(config="-", schema=False, output="json", verbose=False, project_id=None)
    stdout, stdin = sys.stdout, sys.stdin
    sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
    try:
        gc.handle_update_artifacts_command(args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stdin = stdout, stdin


def test_a_snooze_through_update_artifacts_rewrites_unknown_and_assumption_notes(practice):
    ids = _logged(practice)
    out = _update({"updates": [
        {"type": "unknown", "id": ids["u"], "due_at": "2027-01-15"},
        {"type": "assumption", "id": ids["a"], "due_at": "2027-01-16"},
    ]})  # fmt: skip
    assert out["updated"] == 2 and not out.get("errors"), out
    u, a = GitUnknownStore().load_unknown(ids["u"]), GitAssumptionStore().load_assumption(ids["a"])
    assert u["due_at"] == "2027-01-15" and a["due_at"] == "2027-01-16"
    assert u["unknown"] == "when?" and a["confidence"] == 0.6 and a["status"] == "unverified", "other keys kept"


def test_clearing_a_date_removes_it_from_the_note(practice):
    ids = _logged(practice)
    db = _db(practice)
    try:
        db.conn.execute("UPDATE project_unknowns SET due_at = NULL WHERE id = ?", (ids["u"],))
        db.conn.commit()
        assert restamp_due(db.conn, "unknown", ids["u"]) is True
    finally:
        db.close()
    assert "due_at" not in GitUnknownStore().load_unknown(ids["u"])


def test_a_goals_moved_date_is_restamped_into_its_note(practice):
    gid = "dddddddd-4444-4000-8000-000000000004"
    db = _db(practice)
    try:
        db.conn.execute(
            "INSERT INTO goals (id, session_id, objective, scope, status, created_timestamp, goal_data, due_at) "
            "VALUES (?, 's', 'ship x', '{}', 'planned', 0, '{}', '2027-02-01')",
            (gid,),
        )
        db.conn.commit()
        assert restamp_due(db.conn, "goal", gid) is True
    finally:
        db.close()
    assert GitGoalStore().load_goal(gid)["goal_data"]["due_at"] == "2027-02-01"


def test_a_note_that_cannot_be_rewritten_is_reported_not_silent(practice, monkeypatch):
    ids = _logged(practice)
    monkeypatch.setattr("empirica.core.canonical.empirica_git.due_note.restamp_due", lambda *a: False)
    out = _update({"updates": [{"type": "unknown", "id": ids["u"], "due_at": "2027-01-15"}]})
    assert out["updated"] == 1 and "git note was not rewritten" in " ".join(out["errors"])
