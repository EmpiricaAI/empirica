"""goals-archive reaches abandoned goals, so a gardening pass can clear them (David 2026-11-04).

The archive verb selected completed goals only, so an abandoned goal had no way out of `goals-list --status abandoned` short of staying there for good.
Abandoned goals record `abandoned_at` in goal_data when they are abandoned through the verb; rows abandoned another way carry no such stamp, so the age
falls back to the goal's creation time rather than leaving them unreachable.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

from empirica.data.repositories.goals import GoalDataRepository

SESSION = str(uuid.uuid4())
DAY = 86400


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, objective TEXT NOT NULL, description TEXT,
            scope TEXT NOT NULL DEFAULT '{}', estimated_complexity REAL, created_timestamp REAL NOT NULL,
            completed_timestamp REAL, is_completed BOOLEAN DEFAULT 0, goal_data TEXT NOT NULL DEFAULT '{}',
            status TEXT DEFAULT 'in_progress', beads_issue_id TEXT, project_id TEXT, transaction_id TEXT,
            archived BOOLEAN DEFAULT 0, archived_at REAL
        )
        """
    )
    conn.commit()
    return conn


def _insert(conn, *, status, abandoned_ago_days=None, created_ago_days=1, completed_ago_days=None) -> str:
    gid = str(uuid.uuid4())
    data = {} if abandoned_ago_days is None else {"abandoned_at": time.time() - abandoned_ago_days * DAY}
    completed = None if completed_ago_days is None else time.time() - completed_ago_days * DAY
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, created_timestamp, completed_timestamp, is_completed, goal_data, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            gid,
            SESSION,
            "g",
            time.time() - created_ago_days * DAY,
            completed,
            1 if status == "completed" else 0,
            json.dumps(data),
            status,
        ),
    )
    conn.commit()
    return gid


def _archived(conn, gid) -> int:
    return conn.execute("SELECT archived FROM goals WHERE id = ?", (gid,)).fetchone()[0]


def test_the_sweep_archives_an_old_abandoned_goal_and_keeps_a_recent_one():
    conn = _conn()
    old = _insert(conn, status="abandoned", abandoned_ago_days=40)
    recent = _insert(conn, status="abandoned", abandoned_ago_days=3)
    affected = GoalDataRepository(conn).archive_stale_completed(older_than_days=30, apply=True)
    assert {a["id"] for a in affected} == {old}
    assert _archived(conn, old) == 1 and _archived(conn, recent) == 0


def test_an_abandoned_goal_with_no_abandoned_at_ages_from_its_creation():
    conn = _conn()
    old = _insert(conn, status="abandoned", created_ago_days=90)
    new = _insert(conn, status="abandoned", created_ago_days=2)
    affected = GoalDataRepository(conn).archive_stale_completed(older_than_days=30, apply=True)
    assert {a["id"] for a in affected} == {old}
    assert _archived(conn, new) == 0


def test_archiving_one_abandoned_goal_by_id_ignores_age():
    conn = _conn()
    gid = _insert(conn, status="abandoned", abandoned_ago_days=1)
    affected = GoalDataRepository(conn).archive_stale_completed(goal_id=gid[:8], apply=True)
    assert [a["id"] for a in affected] == [gid]
    assert _archived(conn, gid) == 1


def test_live_goals_are_never_archived():
    """Positive control for the widening: only terminal statuses qualify, however old the goal is."""
    conn = _conn()
    ids = [_insert(conn, status=s, created_ago_days=400) for s in ("planned", "in_progress", "blocked")]
    assert GoalDataRepository(conn).archive_stale_completed(older_than_days=30, apply=True) == []
    assert [_archived(conn, i) for i in ids] == [0, 0, 0]


def test_completed_goals_still_age_from_their_completion_time():
    conn = _conn()
    old = _insert(conn, status="completed", completed_ago_days=40)
    recent = _insert(conn, status="completed", completed_ago_days=5, created_ago_days=400)
    affected = GoalDataRepository(conn).archive_stale_completed(older_than_days=30, apply=True)
    assert {a["id"] for a in affected} == {old}
    assert _archived(conn, recent) == 0
