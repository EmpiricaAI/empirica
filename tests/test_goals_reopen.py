"""Tests for GoalDataRepository.reopen_goal — reversible goal completion.

`goals-complete` used to be irreversible via the CLI (finding: no reopen path).
`reopen_goal` flips a completed goal back to in_progress and clears the completed
flags, so an accidental or premature completion can be undone (David directive
2026-07-08).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

from empirica.data.repositories.goals import GoalDataRepository

SESSION = str(uuid.uuid4())


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE goals (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            objective TEXT NOT NULL,
            description TEXT,
            scope TEXT NOT NULL DEFAULT '{}',
            estimated_complexity REAL,
            created_timestamp REAL NOT NULL,
            completed_timestamp REAL,
            is_completed BOOLEAN DEFAULT 0,
            goal_data TEXT NOT NULL DEFAULT '{}',
            status TEXT DEFAULT 'in_progress',
            beads_issue_id TEXT,
            project_id TEXT,
            transaction_id TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert(conn, *, status="completed", is_completed=1, completed=True) -> str:
    gid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, "
        "completed_timestamp, is_completed, goal_data, status) "
        "VALUES (?, ?, ?, '{}', ?, ?, ?, '{}', ?)",
        (
            gid,
            SESSION,
            "test goal",
            time.time(),
            time.time() if completed else None,
            is_completed,
            status,
        ),
    )
    conn.commit()
    return gid


def test_reopen_completed_goal_resets_to_in_progress():
    conn = _conn()
    gid = _insert(conn, status="completed", is_completed=1, completed=True)
    repo = GoalDataRepository(conn)

    assert repo.reopen_goal(gid, reason="premature close", transaction_id="tx-1") is True

    row = conn.execute(
        "SELECT status, is_completed, completed_timestamp, goal_data, transaction_id FROM goals WHERE id = ?",
        (gid,),
    ).fetchone()
    assert row[0] == "in_progress"
    assert row[1] == 0  # is_completed cleared
    assert row[2] is None  # completed_timestamp cleared
    assert row[4] == "tx-1"  # re-linked to the current transaction
    data = json.loads(row[3])
    assert data["reopen_history"][0]["reason"] == "premature close"


def test_reopen_accepts_id_prefix():
    conn = _conn()
    gid = _insert(conn)
    repo = GoalDataRepository(conn)
    assert repo.reopen_goal(gid[:8]) is True
    assert conn.execute("SELECT status FROM goals WHERE id = ?", (gid,)).fetchone()[0] == "in_progress"


def test_reopen_non_completed_goal_returns_false():
    conn = _conn()
    gid = _insert(conn, status="in_progress", is_completed=0, completed=False)
    repo = GoalDataRepository(conn)
    assert repo.reopen_goal(gid) is False


def test_reopen_missing_goal_returns_false():
    conn = _conn()
    repo = GoalDataRepository(conn)
    assert repo.reopen_goal("nonexistent-id") is False


def test_reopen_matches_is_completed_even_if_status_stale():
    """is_completed=1 with a stale status still reopens (is_completed is the
    source of truth per statusline_empirica)."""
    conn = _conn()
    gid = _insert(conn, status="something_odd", is_completed=1, completed=True)
    repo = GoalDataRepository(conn)
    assert repo.reopen_goal(gid) is True
    assert conn.execute("SELECT status, is_completed FROM goals WHERE id = ?", (gid,)).fetchone()[0] == "in_progress"
