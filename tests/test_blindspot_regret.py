"""Blindspot regret auto-trigger — the training label.

A dismissed blindspot flips to `regretted` when a mistake / dead-end lands on the
same goal *after* the dismissal (we warned, it was ignored, the gap bit). The
`created_timestamp > resolved_timestamp` guard enforces the causal order.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.core.blindspots import apply_blindspot_regret
from empirica.data.migrations.migrations import migration_053_blindspot_events


def _db():
    conn = sqlite3.connect(":memory:")
    migration_053_blindspot_events(conn.cursor())
    conn.execute("CREATE TABLE mistakes_made (id TEXT, session_id TEXT, goal_id TEXT, created_timestamp REAL)")
    conn.execute(
        "CREATE TABLE session_dead_ends (id TEXT, session_id TEXT, goal_id TEXT, subtask_id TEXT, created_timestamp REAL)"
    )
    conn.commit()
    return types.SimpleNamespace(conn=conn)


def _dismissed(db, goal="g", subtask="s1", resolved_ts=100.0):
    db.conn.execute(
        "INSERT INTO blindspot_events (session_id, transaction_id, created_timestamp, kind, goal_id, "
        "subtask_id, intent, surfaced_at, outcome, resolved_timestamp) "
        "VALUES ('sess', 'tx', 50.0, 'intent_gap', ?, ?, 'i', 'check', 'dismissed', ?)",
        (goal, subtask, resolved_ts),
    )
    db.conn.commit()


def _outcome(db, sid="s1"):
    return db.conn.execute("SELECT outcome FROM blindspot_events WHERE subtask_id = ?", (sid,)).fetchone()[0]


def test_mistake_after_dismiss_regrets():
    db = _db()
    _dismissed(db, resolved_ts=100.0)
    db.conn.execute("INSERT INTO mistakes_made VALUES ('m', 'sess', 'g', 200.0)")  # after dismiss
    db.conn.commit()
    assert apply_blindspot_regret(db, "sess") == 1
    assert _outcome(db) == "regretted"


def test_dead_end_after_dismiss_regrets():
    db = _db()
    _dismissed(db, resolved_ts=100.0)
    db.conn.execute("INSERT INTO session_dead_ends VALUES ('d', 'sess', 'g', 's1', 200.0)")
    db.conn.commit()
    assert apply_blindspot_regret(db, "sess") == 1
    assert _outcome(db) == "regretted"


def test_mistake_before_dismiss_no_regret():
    db = _db()
    _dismissed(db, resolved_ts=100.0)
    db.conn.execute("INSERT INTO mistakes_made VALUES ('m', 'sess', 'g', 50.0)")  # before dismiss — not causal
    db.conn.commit()
    assert apply_blindspot_regret(db, "sess") == 0
    assert _outcome(db) == "dismissed"


def test_mistake_different_goal_no_regret():
    db = _db()
    _dismissed(db, goal="g", resolved_ts=100.0)
    db.conn.execute("INSERT INTO mistakes_made VALUES ('m', 'sess', 'OTHER', 200.0)")
    db.conn.commit()
    assert apply_blindspot_regret(db, "sess") == 0


def test_no_dismissed_is_noop():
    assert apply_blindspot_regret(_db(), "sess") == 0


def test_fail_open_on_missing_correlation_tables():
    conn = sqlite3.connect(":memory:")
    migration_053_blindspot_events(conn.cursor())  # no mistakes_made / session_dead_ends
    conn.execute(
        "INSERT INTO blindspot_events (session_id, transaction_id, created_timestamp, kind, goal_id, "
        "subtask_id, intent, surfaced_at, outcome, resolved_timestamp) "
        "VALUES ('sess', 'tx', 50.0, 'intent_gap', 'g', 's1', 'i', 'check', 'dismissed', 100.0)"
    )
    conn.commit()
    assert apply_blindspot_regret(types.SimpleNamespace(conn=conn), "sess") == 0  # fail-open, no raise
