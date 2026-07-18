"""Prevention detection — the positive mirror of the blindspot regret loop.

exposed → prevented (acknowledged + window elapsed + no same-subject failure)
        → failed    (a same-subject mistake / dead-end logged AFTER the exposure)
        → stays exposed (window still open). All fail-open.

See docs/architecture/PREVENTION_MEASUREMENT_SPEC.md.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.core.prevention import (
    aggregate_prevention_events,
    apply_prevention_detection,
    emit_prevention_exposure,
    read_prevention_events,
)
from empirica.data.migrations.migrations import migration_058_prevention_events


def _db():
    conn = sqlite3.connect(":memory:")
    migration_058_prevention_events(conn.cursor())
    # minimal failure tables the detection pass joins against (causal order)
    conn.execute(
        "CREATE TABLE mistakes_made (id INTEGER PRIMARY KEY, session_id TEXT, goal_id TEXT, created_timestamp REAL)"
    )
    conn.execute(
        "CREATE TABLE session_dead_ends (id INTEGER PRIMARY KEY, session_id TEXT, "
        "goal_id TEXT, subtask_id TEXT, created_timestamp REAL)"
    )
    conn.commit()
    return types.SimpleNamespace(conn=conn)


def _emit(db, **kw):
    kw.setdefault("pattern_key", "P")
    kw.setdefault("subject_key", "subj")
    kw.setdefault("goal_id", "g")
    kw.setdefault("subtask_id", "s1")
    return emit_prevention_exposure(db, "sess", "tx", **kw)


def _outcome(db):
    return db.conn.execute("SELECT outcome FROM prevention_events LIMIT 1").fetchone()[0]


def test_emit_writes_exposed_row():
    db = _db()
    assert _emit(db) == 1
    rows = read_prevention_events(db, "sess")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "exposed"
    assert rows[0]["pattern_key"] == "P"


def test_acknowledged_window_elapsed_no_failure_becomes_prevented():
    db = _db()
    _emit(db, acknowledged=True, window_s=100, exposed_at=1000.0)
    assert apply_prevention_detection(db, "sess", now=2000.0) == 1
    assert _outcome(db) == "prevented"


def test_same_subject_mistake_after_exposure_becomes_failed():
    db = _db()
    _emit(db, acknowledged=True, window_s=100, exposed_at=1000.0)
    db.conn.execute("INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('sess','g',1500.0)")
    db.conn.commit()
    assert apply_prevention_detection(db, "sess", now=2000.0) == 1
    assert _outcome(db) == "failed"


def test_dead_end_after_exposure_becomes_failed():
    db = _db()
    _emit(db, acknowledged=True, window_s=100, exposed_at=1000.0)
    db.conn.execute(
        "INSERT INTO session_dead_ends (session_id, goal_id, subtask_id, created_timestamp) VALUES ('sess','g','s1',1500.0)"
    )
    db.conn.commit()
    assert apply_prevention_detection(db, "sess", now=2000.0) == 1
    assert _outcome(db) == "failed"


def test_failure_before_exposure_does_not_count():
    """Causal order: a failure logged BEFORE the exposure is not a miss."""
    db = _db()
    _emit(db, acknowledged=True, window_s=100, exposed_at=1000.0)
    db.conn.execute("INSERT INTO mistakes_made (session_id, goal_id, created_timestamp) VALUES ('sess','g',500.0)")
    db.conn.commit()
    assert apply_prevention_detection(db, "sess", now=2000.0) == 1
    assert _outcome(db) == "prevented"


def test_window_still_open_stays_exposed():
    """Absence of a failure inside too-short a window is not evidence (§5)."""
    db = _db()
    _emit(db, acknowledged=True, window_s=100, exposed_at=1000.0)
    assert apply_prevention_detection(db, "sess", now=1050.0) == 0
    assert _outcome(db) == "exposed"


def test_unacknowledged_not_prevented_even_without_failure():
    db = _db()
    _emit(db, acknowledged=False, window_s=100, exposed_at=1000.0)
    assert apply_prevention_detection(db, "sess", now=2000.0) == 0
    assert _outcome(db) == "exposed"


def test_emit_and_detect_fail_open_on_missing_table():
    conn = sqlite3.connect(":memory:")  # no prevention_events table
    db = types.SimpleNamespace(conn=conn)
    assert emit_prevention_exposure(db, "s", "t", pattern_key="P", subject_key="x") == 0
    assert read_prevention_events(db) == []
    assert apply_prevention_detection(db, "s") == 0


def test_aggregate_prevention_rate_and_beneficiary_independence():
    rows = [
        {"outcome": "prevented", "author_practice": "A", "beneficiary_practice": "B"},  # cross-practice
        {"outcome": "prevented", "author_practice": "A", "beneficiary_practice": "A"},  # endogenous
        {"outcome": "failed", "author_practice": "A", "beneficiary_practice": "B"},
        {"outcome": "exposed", "author_practice": "A", "beneficiary_practice": "B"},
    ]
    agg = aggregate_prevention_events(rows)
    assert agg["by_outcome"]["prevented"] == 2
    assert agg["prevention_rate"] == round(2 / 3, 3)  # 2 prevented of 3 resolved
    assert agg["beneficiary_independent"] == 1  # only the A→B prevention counts
    assert agg["beneficiary_independent_rate"] == 0.5  # 1 of 2 preventions cross-practice
