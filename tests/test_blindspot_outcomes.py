"""Blindspot outcome resolution (T4) — the POSTFLIGHT learning half.

surfaced → acknowledged (task engaged) / dismissed (goal closed bare) / stays
surfaced (still in-flight). mark_blindspot_regretted flips dismissed → regretted.
All fail-open.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.core.blindspots import (
    mark_blindspot_regretted,
    persist_blindspot_candidates,
    resolve_blindspot_outcomes,
)
from empirica.data.migrations.migrations import migration_053_blindspot_events


def _db(goal_tree):
    conn = sqlite3.connect(":memory:")
    migration_053_blindspot_events(conn.cursor())
    conn.commit()
    return types.SimpleNamespace(conn=conn, goals=types.SimpleNamespace(get_goal_tree=lambda sid: goal_tree))


def _cand(sid="s1"):
    return {"kind": "intent_gap", "goal_id": "g", "subtask_id": sid, "intent": "assess"}


def _tree(goal_status="in_progress", status="pending", findings=None, unknowns=None, dead_ends=None):
    st = {
        "subtask_id": "s1",
        "description": "d",
        "status": status,
        "findings": findings or [],
        "unknowns": unknowns or [],
        "dead_ends": dead_ends or [],
    }
    return [{"goal_id": "g", "objective": "o", "status": goal_status, "subtasks": [st]}]


def _outcome(db, sid="s1"):
    return db.conn.execute("SELECT outcome FROM blindspot_events WHERE subtask_id = ?", (sid,)).fetchone()[0]


def test_engaged_subtask_becomes_acknowledged():
    for engage in ({"unknowns": ["u"]}, {"findings": ["f"]}, {"dead_ends": ["d"]}, {"status": "completed"}):
        db = _db(_tree(**engage))
        persist_blindspot_candidates(db, "sess", "tx", [_cand()], "check")
        assert resolve_blindspot_outcomes(db, "sess") == 1, engage
        assert _outcome(db) == "acknowledged", engage


def test_bare_subtask_under_terminal_goal_dismissed():
    db = _db(_tree(goal_status="completed"))
    persist_blindspot_candidates(db, "sess", "tx", [_cand()], "check")
    assert resolve_blindspot_outcomes(db, "sess") == 1
    assert _outcome(db) == "dismissed"


def test_bare_subtask_under_active_goal_stays_surfaced():
    db = _db(_tree(goal_status="in_progress"))
    persist_blindspot_candidates(db, "sess", "tx", [_cand()], "check")
    assert resolve_blindspot_outcomes(db, "sess") == 0  # still in-flight — don't dismiss early
    assert _outcome(db) == "surfaced"


def test_no_surfaced_events_is_noop():
    assert resolve_blindspot_outcomes(_db(_tree()), "sess") == 0


def test_mark_regretted_flips_dismissed():
    db = _db(_tree(goal_status="completed"))
    persist_blindspot_candidates(db, "sess", "tx", [_cand()], "check")
    resolve_blindspot_outcomes(db, "sess")  # → dismissed
    assert _outcome(db) == "dismissed"
    assert mark_blindspot_regretted(db, "sess", "s1") == 1
    assert _outcome(db) == "regretted"


def test_mark_regretted_only_targets_dismissed():
    db = _db(_tree())
    persist_blindspot_candidates(db, "sess", "tx", [_cand()], "check")  # still surfaced
    assert mark_blindspot_regretted(db, "sess", "s1") == 0  # not dismissed → no-op
    assert _outcome(db) == "surfaced"


def test_fail_open():
    class Boom:
        @property
        def conn(self):
            raise RuntimeError("db")

    assert resolve_blindspot_outcomes(Boom(), "sess") == 0
    assert mark_blindspot_regretted(Boom(), "sess", "s1") == 0
