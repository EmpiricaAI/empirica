"""Tests for GoalDataRepository.archive_stale_completed + reopen un-archive.

goal-lifecycle archive-after-X hygiene (mirrors source-archive): a completed
goal older than N days can be archived (hidden from the completed list unless
`goals-list --include-archived`); `goals-reopen` un-archives (David directive
2026-07-08, Part 2).
"""

from __future__ import annotations

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
            transaction_id TEXT,
            archived BOOLEAN DEFAULT 0,
            archived_at REAL
        )
        """
    )
    conn.commit()
    return conn


def _insert(conn, *, status="completed", is_completed=1, completed_ago_days=None, archived=0) -> str:
    gid = str(uuid.uuid4())
    completed_ts = (time.time() - completed_ago_days * 86400) if completed_ago_days is not None else None
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, "
        "completed_timestamp, is_completed, goal_data, status, archived) "
        "VALUES (?, ?, ?, '{}', ?, ?, ?, '{}', ?, ?)",
        (gid, SESSION, "g", time.time(), completed_ts, is_completed, status, archived),
    )
    conn.commit()
    return gid


def test_archive_dry_run_does_not_mutate():
    conn = _conn()
    gid = _insert(conn, completed_ago_days=40)
    repo = GoalDataRepository(conn)
    affected = repo.archive_stale_completed(older_than_days=30, apply=False)
    assert len(affected) == 1 and affected[0]["id"] == gid
    assert conn.execute("SELECT archived FROM goals WHERE id = ?", (gid,)).fetchone()[0] == 0


def test_archive_apply_archives_only_old_completed():
    conn = _conn()
    old = _insert(conn, completed_ago_days=40)
    recent = _insert(conn, completed_ago_days=5)
    active = _insert(conn, status="in_progress", is_completed=0, completed_ago_days=None)
    repo = GoalDataRepository(conn)

    affected = repo.archive_stale_completed(older_than_days=30, apply=True)

    assert {a["id"] for a in affected} == {old}
    old_row = conn.execute("SELECT archived, archived_at FROM goals WHERE id = ?", (old,)).fetchone()
    assert old_row[0] == 1 and old_row[1] is not None
    # recent completed + active goal untouched
    assert conn.execute("SELECT archived FROM goals WHERE id = ?", (recent,)).fetchone()[0] == 0
    assert conn.execute("SELECT archived FROM goals WHERE id = ?", (active,)).fetchone()[0] == 0


def test_archive_by_goal_id_ignores_age():
    conn = _conn()
    recent = _insert(conn, completed_ago_days=1)
    repo = GoalDataRepository(conn)
    affected = repo.archive_stale_completed(goal_id=recent[:8], apply=True)
    assert len(affected) == 1
    assert conn.execute("SELECT archived FROM goals WHERE id = ?", (recent,)).fetchone()[0] == 1


def test_archive_skips_already_archived():
    conn = _conn()
    _insert(conn, completed_ago_days=40, archived=1)
    repo = GoalDataRepository(conn)
    assert repo.archive_stale_completed(older_than_days=30, apply=True) == []


def test_reopen_unarchives():
    conn = _conn()
    gid = _insert(conn, completed_ago_days=40, archived=1)
    repo = GoalDataRepository(conn)
    assert repo.reopen_goal(gid) is True
    row = conn.execute("SELECT status, archived, archived_at FROM goals WHERE id = ?", (gid,)).fetchone()
    assert row[0] == "in_progress"
    assert row[1] == 0  # un-archived
    assert row[2] is None
