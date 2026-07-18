"""Recurrence oracle — ground-truth on whether a warned-about failure recurred.

Signals (strongest first): a new mistake/dead-end after since_ts (0.9), a
prevention_event resolved to failed/recurred (0.8), a regret flip (0.6). Read-only,
fail-open. See docs/architecture/PREVENTION_MEASUREMENT_SPEC.md §5.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.core.prevention import recurrence_verdict
from empirica.data.migrations.migrations import (
    migration_053_blindspot_events,
    migration_058_prevention_events,
    migration_059_prevention_outcome_family,
)


def _db():
    conn = sqlite3.connect(":memory:")
    migration_058_prevention_events(conn.cursor())
    migration_059_prevention_outcome_family(conn.cursor())
    migration_053_blindspot_events(conn.cursor())
    conn.execute(
        "CREATE TABLE mistakes_made (id INTEGER PRIMARY KEY, session_id TEXT, goal_id TEXT, created_timestamp REAL)"
    )
    conn.execute(
        "CREATE TABLE session_dead_ends (id INTEGER PRIMARY KEY, session_id TEXT, "
        "goal_id TEXT, subtask_id TEXT, created_timestamp REAL)"
    )
    conn.commit()
    return types.SimpleNamespace(conn=conn)


def _pevent(db, *, outcome="prevented", exposed_at=1000.0, outcome_at=1100.0):
    db.conn.execute(
        "INSERT INTO prevention_events "
        "(session_id, transaction_id, created_timestamp, pattern_key, subject_key, "
        "goal_id, subtask_id, exposed_at, outcome, outcome_at) "
        "VALUES ('sess', 'tx', ?, 'P', 'subj', 'g', 's1', ?, ?, ?)",
        (exposed_at, exposed_at, outcome, outcome_at),
    )
    db.conn.commit()


def test_no_events_returns_default():
    v = recurrence_verdict(_db(), "P", "subj")
    assert v["recurred"] is False
    assert v["confidence"] == 0.0
    assert v["exposures"] == 0


def test_hard_recurrence_via_new_mistake():
    db = _db()
    _pevent(db, outcome="prevented", exposed_at=1000.0, outcome_at=1100.0)
    db.conn.execute("INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('sess','g',2000.0)")
    db.conn.commit()
    v = recurrence_verdict(db, "P", "subj", since_ts=1500.0)
    assert v["recurred"] is True
    assert v["confidence"] == 0.9
    assert any(r["kind"] == "mistake" for r in v["recurrences"])
    assert v["latency_s"] == 500.0  # 2000 - 1500
    assert v["preventions"] == 1
    assert v["first_occurrence_at"] == 1000.0


def test_recurrence_via_failed_prevention_event():
    db = _db()
    _pevent(db, outcome="failed", outcome_at=2000.0)
    v = recurrence_verdict(db, "P", "subj", since_ts=1500.0)
    assert v["recurred"] is True
    assert v["confidence"] == 0.8


def test_recurrence_via_regret_flip():
    db = _db()
    _pevent(db, outcome="prevented")
    db.conn.execute(
        "INSERT INTO blindspot_events (created_timestamp, subtask_id, outcome, resolved_timestamp) "
        "VALUES (1000.0, 's1', 'regretted', 2000.0)"
    )
    db.conn.commit()
    v = recurrence_verdict(db, "P", "subj", since_ts=1500.0)
    assert v["recurred"] is True
    assert v["confidence"] == 0.6


def test_no_recurrence_when_only_prevented():
    db = _db()
    _pevent(db, outcome="prevented", outcome_at=1100.0)
    v = recurrence_verdict(db, "P", "subj", since_ts=1500.0)
    assert v["recurred"] is False
    assert v["confidence"] == 0.0
    assert v["preventions"] == 1


def test_failure_before_since_does_not_count():
    db = _db()
    _pevent(db)
    db.conn.execute("INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('sess','g',1200.0)")
    db.conn.commit()
    v = recurrence_verdict(db, "P", "subj", since_ts=1500.0)  # mistake at 1200 < 1500
    assert v["recurred"] is False


def test_strongest_signal_wins_confidence():
    db = _db()
    _pevent(db, outcome="failed", outcome_at=2000.0)  # 0.8 signal
    db.conn.execute("INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('sess','g',2100.0)")
    db.conn.commit()
    v = recurrence_verdict(db, "P", "subj", since_ts=1500.0)
    assert v["confidence"] == 0.9  # mistake (0.9) beats failed-event (0.8)


def test_fail_open_on_missing_tables():
    conn = sqlite3.connect(":memory:")  # no tables at all
    db = types.SimpleNamespace(conn=conn)
    v = recurrence_verdict(db, "P", "subj")
    assert v["recurred"] is False
    assert v["confidence"] == 0.0
