"""`--all-projects` (gardening) on goals-list + unknown-list.

The list verbs normally scope to the active project, which hides goals/unknowns
stranded under other or divergent project_ids. `--all-projects` bypasses that
scope so a gardening pass can see (and clean) the whole graph. These tests
monkeypatch SessionDatabase with an in-memory 2-project DB and assert the flag
crosses the project boundary while the default stays scoped.
"""

from __future__ import annotations

import sqlite3
import time
import types

import empirica.cli.command_handlers.artifact_log_commands as alc
import empirica.cli.command_handlers.goal_commands as gc


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn

    def close(self):
        pass


def _goals_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE goals (id TEXT PRIMARY KEY, objective TEXT, status TEXT, is_completed INT, "
        "created_timestamp REAL, session_id TEXT, project_id TEXT, transaction_id TEXT, archived INT DEFAULT 0)"
    )
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_id TEXT, ai_id TEXT)")
    conn.execute("CREATE TABLE subtasks (goal_id TEXT, status TEXT)")
    now = time.time()
    conn.execute("INSERT INTO sessions VALUES ('s1','projA','ai')")
    conn.execute("INSERT INTO goals VALUES ('g-a','goal A','in_progress',0,?,'s1','projA',NULL,0)", (now,))
    conn.execute("INSERT INTO goals VALUES ('g-b','goal B','in_progress',0,?,'s1','projB',NULL,0)", (now,))
    conn.execute("INSERT INTO goals VALUES ('g-c','goal C','in_progress',0,?,'s1',NULL,NULL,0)", (now,))
    conn.commit()
    return conn


def _unknowns_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, unknown TEXT, is_resolved INT, resolved_by TEXT, "
        "impact REAL, subject TEXT, created_timestamp REAL, resolved_timestamp REAL, goal_id TEXT, project_id TEXT)"
    )
    now = time.time()
    conn.execute("INSERT INTO project_unknowns VALUES ('u-a','q A',0,NULL,0.5,NULL,?,NULL,NULL,'projA')", (now,))
    conn.execute("INSERT INTO project_unknowns VALUES ('u-b','q B',0,NULL,0.5,NULL,?,NULL,NULL,'projB')", (now,))
    conn.commit()
    return conn


def _args(**kw):
    base = {"output": "json", "project_id": None, "session_id": None, "limit": 20}
    base.update(kw)
    return types.SimpleNamespace(**base)


# The handlers import SessionDatabase locally, so patch it at the source module.
_DB_TARGET = "empirica.data.session_database.SessionDatabase"


# ── goals-list ───────────────────────────────────────────────────────────────
def test_goals_all_projects_crosses_boundary(monkeypatch):
    conn = _goals_db()
    monkeypatch.setattr(_DB_TARGET, lambda: _FakeDB(conn))
    # default: scoped to the derived/active project → NOT all three
    scoped = gc.handle_goals_list_command(_args(all_projects=False, project_id="projA"))
    assert isinstance(scoped, dict) and scoped["goals_count"] == 1
    # --all-projects: every project_id, incl. the null-project orphan
    allp = gc.handle_goals_list_command(_args(all_projects=True))
    assert isinstance(allp, dict)
    assert allp["goals_count"] == 3
    goals = allp["goals"]
    assert isinstance(goals, list)
    assert {g["project_id"] for g in goals} == {"projA", "projB", None}


def test_goals_all_projects_bumps_default_limit(monkeypatch):
    conn = _goals_db()
    monkeypatch.setattr(_DB_TARGET, lambda: _FakeDB(conn))
    # limit stays the default 20 in args, but --all-projects raises it so a sweep isn't capped
    res = gc.handle_goals_list_command(_args(all_projects=True, limit=20))
    assert isinstance(res, dict) and res["limit"] == 2000


# ── unknown-list ─────────────────────────────────────────────────────────────
def test_unknowns_all_projects_crosses_boundary(monkeypatch):
    conn = _unknowns_db()
    monkeypatch.setattr(_DB_TARGET, lambda: _FakeDB(conn))
    scoped = alc.handle_unknown_list_command(_args(all_projects=False, project_id="projA", limit=30))
    assert isinstance(scoped, dict) and scoped["unknowns_count"] == 1
    allp = alc.handle_unknown_list_command(_args(all_projects=True, limit=30))
    assert isinstance(allp, dict)
    assert allp["unknowns_count"] == 2
    unk = allp["unknowns"]
    assert isinstance(unk, list)
    assert {u["project_id"] for u in unk} == {"projA", "projB"}
