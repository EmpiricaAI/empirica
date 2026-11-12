"""Sessions close in the workspace registry, and the rows never closed are reconciled (goal 119e2972).

Nothing closed a global_sessions row: every path that ends a session wrote end_time to the practice's database only, and the
registry held 10,870 active rows against 1,053 closed. Readers take the most recent active row, and projects-unregister --local
counted the dead rows as references.
"""

from __future__ import annotations

import json
import sqlite3
import types

import pytest

from empirica.data import global_sessions as gs


@pytest.fixture
def registry(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".empirica" / "workspace").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    conn = sqlite3.connect(str(gs.workspace_db_path()))
    conn.executescript(
        """
        CREATE TABLE global_projects (id TEXT PRIMARY KEY, name TEXT, trajectory_path TEXT);
        CREATE TABLE global_sessions (session_id TEXT PRIMARY KEY, ai_id TEXT NOT NULL, origin_project_id TEXT,
            current_project_id TEXT, instance_id TEXT, status TEXT DEFAULT 'active', created_at REAL NOT NULL,
            last_activity REAL, parent_session_id TEXT, metadata TEXT);  -- no closed_at: the CREATE core shipped had none
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _practice(tmp_path, registry, pid, sessions):
    traj = tmp_path / pid / ".empirica"
    (traj / "sessions").mkdir(parents=True)
    local = sqlite3.connect(str(traj / "sessions" / "sessions.db"))
    local.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, end_time TEXT)")
    local.executemany("INSERT INTO sessions VALUES (?, ?)", sessions)
    local.commit()
    local.close()
    registry.execute("INSERT INTO global_projects VALUES (?, ?, ?)", (pid, pid, str(traj)))
    registry.commit()
    return traj


def _register(registry, sid, pid):
    registry.execute(
        "INSERT INTO global_sessions (session_id, ai_id, origin_project_id, current_project_id, created_at) VALUES (?, 't', ?, ?, 1)",
        (sid, pid, pid),
    )
    registry.commit()


def _status(registry):
    return dict(registry.execute("SELECT session_id, status FROM global_sessions").fetchall())


def test_closing_a_session_closes_its_registry_row(registry):
    _register(registry, "s1", "p")
    _register(registry, "s2", "p")
    assert gs.close_global_sessions(["s1"]) == 1
    assert _status(registry) == {"s1": "closed", "s2": "active"}
    assert gs.close_global_sessions(["s1"]) == 0  # already closed: nothing changes


def test_without_a_registry_closing_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "nobody"))
    assert gs.close_global_sessions(["s1"]) == 0


def test_every_path_that_ends_a_session_closes_its_row(registry, tmp_path):
    from empirica.cli.command_handlers.session_create import auto_close_previous_sessions
    from empirica.core.handoff.auto_generator import close_session
    from empirica.data.session_database import SessionDatabase

    db_path = str(tmp_path / "s.db")
    db = SessionDatabase(db_path=db_path)
    try:
        ended = db.create_session(ai_id="t1")
        handed_off = db.create_session(ai_id="t2")
        replaced = db.create_session(ai_id="t3")
        assert set(_status(registry).values()) == {"active"}
        db.end_session(ended)
        project = db.conn.execute("SELECT project_id FROM sessions WHERE session_id = ?", (replaced,)).fetchone()[0]
        auto_close_previous_sessions(db, "t3", project)
    finally:
        db.close()
    close_session(handed_off, db_path=db_path)
    status = _status(registry)
    assert {sid: status.get(sid) for sid in (ended, replaced, handed_off)} == dict.fromkeys(
        (ended, replaced, handed_off), "closed"
    )


def test_reconcile_closes_what_ended_is_absent_or_lost_its_practice_and_leaves_the_rest(registry, tmp_path):
    _practice(tmp_path, registry, "p1", [("ended", "2026-10-01T10:00:00"), ("live", None)])
    for sid in ("ended", "live", "absent"):
        _register(registry, sid, "p1")
    registry.execute(
        "INSERT INTO global_projects VALUES ('gone', 'gone', ?)", (str(tmp_path / "deleted" / ".empirica"),)
    )
    _register(registry, "orphan", "gone")
    _register(registry, "no-project", None)
    broken = _practice(tmp_path, registry, "p2", [])
    (broken / "sessions" / "sessions.db").write_bytes(b"not a database")
    _register(registry, "unreadable", "p2")

    plan = gs.plan_reconcile(registry)
    assert plan["counts"] == {"active": 6, "ended": 1, "absent": 1, "practice_gone": 2, "live": 1, "unknown": 1}
    assert _status(registry)["ended"] == "active"  # the plan writes nothing

    assert gs.apply_reconcile(registry, plan) == 4
    assert _status(registry) == {
        "ended": "closed",
        "live": "active",
        "absent": "closed",
        "orphan": "closed",
        "no-project": "closed",
        "unreadable": "active",
    }
    closed_at = registry.execute("SELECT closed_at FROM global_sessions WHERE session_id = 'ended'").fetchone()[0]
    assert closed_at == pytest.approx(gs._ended_at("2026-10-01T10:00:00"))


def test_doctor_reconcile_sessions_plans_then_applies(registry, tmp_path, capsys):
    from empirica.cli.command_handlers.doctor import handle_doctor_command

    _practice(tmp_path, registry, "p1", [("ended", "2026-10-01T10:00:00")])
    _register(registry, "ended", "p1")
    args = types.SimpleNamespace(reconcile_sessions=True, apply=False, output="json")
    assert handle_doctor_command(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["would_close"] == 1 and plan["next"].endswith("--apply")
    assert _status(registry)["ended"] == "active"

    args.apply = True
    assert handle_doctor_command(args) == 0
    assert json.loads(capsys.readouterr().out)["closed"] == 1
    assert _status(registry)["ended"] == "closed"


def test_unregister_counts_only_active_sessions_as_references(registry):
    from empirica.cli.command_handlers.projects_commands import _local_sql_references

    _register(registry, "old", "p")
    registry.execute("UPDATE global_sessions SET status = 'closed'")
    registry.commit()
    assert _local_sql_references(registry, "p")["global_sessions"] == 0
    _register(registry, "now", "p")
    assert _local_sql_references(registry, "p")["global_sessions"] == 1
