"""Test migration 051 — nullable engagement_id column on goals.

Scopes a goal to an engagement (the artifact → goal → engagement linkage of the
engagement substrate). Additive, nullable, idempotent.
"""

from __future__ import annotations

import sqlite3

from empirica.data.migrations.migrations import migration_051_goals_engagement_id


def _goals_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, objective TEXT NOT NULL)")
    return conn


def _cols(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(goals)").fetchall()}


def test_adds_engagement_id_column():
    conn = _goals_db()
    migration_051_goals_engagement_id(conn.cursor())
    conn.commit()
    assert "engagement_id" in _cols(conn)


def test_engagement_id_is_nullable():
    conn = _goals_db()
    migration_051_goals_engagement_id(conn.cursor())
    conn.commit()
    # insert without engagement_id succeeds → column is nullable
    conn.execute("INSERT INTO goals (id, objective) VALUES ('g1', 'x')")
    assert conn.execute("SELECT engagement_id FROM goals WHERE id='g1'").fetchone()[0] is None


def test_creates_index():
    conn = _goals_db()
    migration_051_goals_engagement_id(conn.cursor())
    conn.commit()
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(goals)").fetchall()}
    assert "idx_goals_engagement_id" in indexes


def test_idempotent():
    conn = _goals_db()
    cur = conn.cursor()
    migration_051_goals_engagement_id(cur)
    migration_051_goals_engagement_id(cur)  # second run must not raise
    conn.commit()
    # re-run did not raise and the column is still present + queryable
    assert "engagement_id" in _cols(conn)
    conn.execute("INSERT INTO goals (id, objective, engagement_id) VALUES ('g2', 'y', 'e1')")
    assert conn.execute("SELECT engagement_id FROM goals WHERE id='g2'").fetchone()[0] == "e1"
