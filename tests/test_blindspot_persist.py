"""blindspot_events persistence + aggregation (migration 053).

Instrument-before-surface substrate: persist candidates fail-open, aggregate into
the blindspot-report telemetry (acknowledge / dismiss / regret rates).
"""

from __future__ import annotations

import sqlite3
import types

from empirica.core.blindspots import (
    aggregate_blindspot_events,
    persist_blindspot_candidates,
    read_blindspot_events,
)
from empirica.data.migrations.migrations import migration_053_blindspot_events


def _db():
    conn = sqlite3.connect(":memory:")
    migration_053_blindspot_events(conn.cursor())
    conn.commit()
    return types.SimpleNamespace(conn=conn)


def _cand(subtask="s1", intent="assess Y"):
    return {"kind": "intent_gap", "goal_id": "g1", "subtask_id": subtask, "intent": intent}


def test_migration_creates_table_with_columns():
    db = _db()
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(blindspot_events)")}
    assert {"session_id", "transaction_id", "kind", "goal_id", "subtask_id", "intent", "surfaced_at", "outcome"} <= cols


def test_migration_idempotent():
    db = _db()
    migration_053_blindspot_events(db.conn.cursor())  # second run must not raise


def test_persist_writes_rows_as_surfaced():
    db = _db()
    n = persist_blindspot_candidates(db, "sess", "tx1", [_cand("s1"), _cand("s2")], "check")
    assert n == 2
    rows = read_blindspot_events(db)
    assert len(rows) == 2
    assert all(r["outcome"] == "surfaced" and r["surfaced_at"] == "check" for r in rows)
    assert {r["subtask_id"] for r in rows} == {"s1", "s2"}


def test_persist_empty_is_noop():
    db = _db()
    assert persist_blindspot_candidates(db, "sess", "tx1", [], "check") == 0
    assert read_blindspot_events(db) == []


def test_persist_fail_open_missing_table():
    conn = sqlite3.connect(":memory:")  # un-migrated
    db = types.SimpleNamespace(conn=conn)
    assert persist_blindspot_candidates(db, "sess", "tx1", [_cand()], "check") == 0  # no raise


def test_persist_fail_open_db_error():
    class Boom:
        @property
        def conn(self):
            raise RuntimeError("locked")

    assert persist_blindspot_candidates(Boom(), "s", "t", [_cand()], "check") == 0  # no raise


def test_read_session_scoped():
    db = _db()
    persist_blindspot_candidates(db, "sA", "t", [_cand("s1")], "check")
    persist_blindspot_candidates(db, "sB", "t", [_cand("s2")], "check")
    assert len(read_blindspot_events(db, "sA")) == 1
    assert len(read_blindspot_events(db)) == 2


def test_aggregate_rates():
    rows = [
        {"outcome": "surfaced", "kind": "intent_gap"},
        {"outcome": "acknowledged", "kind": "intent_gap"},
        {"outcome": "dismissed", "kind": "intent_gap"},
        {"outcome": "regretted", "kind": "intent_gap"},
    ]
    s = aggregate_blindspot_events(rows)
    assert s["total"] == 4
    assert s["acknowledge_rate"] == 0.25
    assert s["dismiss_rate"] == 0.25
    assert s["regret_rate"] == 0.25
    assert s["by_kind"] == {"intent_gap": 4}


def test_aggregate_empty():
    s = aggregate_blindspot_events([])
    assert s["total"] == 0
    assert s["acknowledge_rate"] is None
    assert s["regret_rate"] is None
