"""`update-artifacts --project-id`: snooze a due date in another practice (1.15.0 sweep, D6).

`goals-create --project-id` writes a goal, and its date, into the target practice's database, but update-artifacts only
opened the current one, so that date could be set and never moved.
"""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

from empirica.cli.command_handlers import artifact_log_commands as alc
from empirica.cli.command_handlers.graph_commands import handle_update_artifacts_command
from empirica.data.session_database import SessionDatabase

GOAL = "bbbbbbbb-2222-4000-8000-000000000002"


def _target_db(tmp_path):
    path = tmp_path / "other" / ".empirica" / "sessions" / "sessions.db"
    path.parent.mkdir(parents=True)
    db = SessionDatabase(db_path=str(path))
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, status, created_timestamp, goal_data, due_at) "
        "VALUES (?, 's', 'ship x', '{}', 'planned', 0, '{}', '2026-11-20')",
        (GOAL,),
    )
    db.conn.commit()
    db.close()
    return path


def _run(payload, **kw) -> tuple[int, dict]:
    args = SimpleNamespace(
        **{"config": "-", "schema": False, "output": "json", "verbose": False, "project_id": None, **kw}
    )
    stdout, stdin = sys.stdout, sys.stdin
    sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
    try:
        rc = handle_update_artifacts_command(args)
        return rc, json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stdin = stdout, stdin


def test_a_goal_in_another_practice_is_snoozed_there(tmp_path, monkeypatch):
    path = _target_db(tmp_path)
    monkeypatch.setattr(
        alc, "_get_db_for_project", lambda name: SessionDatabase(db_path=str(path)) if name == "other" else None
    )
    rc, out = _run({"updates": [{"type": "goal", "id": GOAL, "due_at": "2026-12-15"}]}, **{"project_id": "other"})
    assert rc == 0 and out["updated"] == 1, out
    import sqlite3

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT due_at FROM goals WHERE id = ?", (GOAL,)).fetchone()[0] == "2026-12-15"


def test_an_unknown_project_is_refused_and_nothing_is_written(monkeypatch):
    monkeypatch.setattr(alc, "_get_db_for_project", lambda name: None)
    rc, out = _run({"updates": [{"type": "goal", "id": GOAL, "due_at": "+7d"}]}, **{"project_id": "nope"})
    assert rc == 1 and "cannot resolve project 'nope'" in out["error"]


def test_a_lesson_is_refused_with_project_id(tmp_path, monkeypatch):
    path = _target_db(tmp_path)
    monkeypatch.setattr(alc, "_get_db_for_project", lambda name: SessionDatabase(db_path=str(path)))
    _rc, out = _run(
        {"updates": [{"type": "lesson", "id": "cccccccc", "visibility": "shared"}]}, **{"project_id": "other"}
    )
    assert out["updated"] == 0 and "not with --project-id" in " ".join(out["errors"])


def test_the_parser_has_the_flag():
    from empirica.cli.cli_core import create_argument_parser

    args = create_argument_parser().parse_args(["update-artifacts", "-", "--project-id", "other"])
    assert args.project_id == "other"
