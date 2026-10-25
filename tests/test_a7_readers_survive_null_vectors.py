"""Readers of the reflexes vector columns must cope with NULL = "not assessed" (sweep item A7, goal 1cd7caa3).

Written with rows inserted by direct SQL, so none of these tests depends on `store_vectors` having been changed: NULL rows already
exist in the table today (reflex_import writes them for vectors missing from a git note) and the writer change is what makes them
routine. Every reader below was found by the two read-only audits of the reader surface: each either raised TypeError on None or
re-invented a value (0.5, 0.0, 1.0) that the NULL exists to avoid.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

from empirica.data.session_database import SessionDatabase

HOOKS = Path(__file__).resolve().parents[1] / "empirica" / "plugins" / "claude-code-integration" / "hooks"
NAMES = [
    "engagement",
    "know",
    "do",
    "context",
    "clarity",
    "coherence",
    "signal",
    "density",
    "state",
    "change",
    "completion",
    "impact",
    "uncertainty",
]
FULL = {n: round(0.3 + 0.04 * i, 2) for i, n in enumerate(NAMES)}


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


def put(db, session_id, phase, vectors, *, ts=None, transaction_id=None):
    """Insert a reflexes row exactly as given: a vector that is absent from `vectors` is NULL."""
    cols = ", ".join(NAMES)
    marks = ", ".join("?" for _ in NAMES)
    db.conn.execute(
        f"INSERT INTO reflexes (session_id, phase, round, timestamp, {cols}, reflex_data, transaction_id) "
        f"VALUES (?, ?, 1, ?, {marks}, '{{}}', ?)",
        (session_id, phase, ts or time.time(), *[vectors.get(n) for n in NAMES], transaction_id),
    )
    db.conn.commit()


def hook(filename, name):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── U1: the session-end auto-POSTFLIGHT must not become sparse ──


def test_the_session_end_snapshot_takes_each_vector_from_its_last_recorded_value(db, monkeypatch):
    """The auto-POSTFLIGHT is built from the latest row, which is usually a partial CHECK. With 0.5s gone from that row it would post
    4 vectors and store a POSTFLIGHT full of NULLs, which breaks the 'POSTFLIGHT is complete' assumption flow_state and the health
    score make. The honest completion is the last value each vector was actually recorded with, which is what its own reasoning text
    already says ('auto-captured from the last recorded vectors')."""
    sid = db.create_session(ai_id="a7-end")
    put(db, sid, "PREFLIGHT", FULL, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.91, "uncertainty": 0.11}, ts=2000.0)
    mod = hook("session-end-postflight.py", "session_end_a7")
    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase", lambda *a, **k: SessionDatabase(db_path=db.db_path)
    )
    state = mod.get_session_state(sid)
    last = state["last_vectors"]
    assert set(last) == set(NAMES), f"sparse auto-POSTFLIGHT: {sorted(set(NAMES) - set(last))} missing"
    assert last["know"] == 0.91 and last["uncertainty"] == 0.11, "the newest recorded value wins"
    assert last["clarity"] == FULL["clarity"], "an unsupplied vector keeps its last recorded value, not 0.5"
    assert state["needs_postflight"] is True


# ── U5: session-create's auto-close must not re-invent what the NULL exists to avoid ──


def _auto_closed_row(db, sid):
    db.conn.row_factory = __import__("sqlite3").Row
    return dict(
        db.conn.execute("SELECT * FROM reflexes WHERE session_id = ? AND phase = 'POSTFLIGHT'", (sid,)).fetchone()
    )


def _close(db, sid):
    from empirica.cli.command_handlers.session_create import auto_close_previous_sessions

    db.conn.execute("UPDATE sessions SET ai_id = 'a7-close', project_id = 'p1' WHERE session_id = ?", (sid,))
    db.conn.commit()
    return auto_close_previous_sessions(db, "a7-close", "p1")


def test_the_auto_close_keeps_a_real_zero_instead_of_turning_it_into_a_default(db):
    """`vector or 0.5` read a genuine 0.0 (change at the start of a transaction is 0.0; uncertainty can be 0.0) as missing."""
    sid = db.create_session(ai_id="a7-close")
    put(db, sid, "PREFLIGHT", {**FULL, "change": 0.0, "uncertainty": 0.0}, ts=1000.0)
    _close(db, sid)
    row = _auto_closed_row(db, sid)
    assert row["change"] == 0.0 and row["uncertainty"] == 0.0, "a real zero was replaced by a default"


def test_the_auto_close_takes_each_vector_from_its_last_recorded_value_and_leaves_unrecorded_ones_null(db):
    sid = db.create_session(ai_id="a7-close")
    partial_pre = {k: v for k, v in FULL.items() if k not in ("impact", "engagement")}
    put(db, sid, "PREFLIGHT", partial_pre, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.91}, ts=2000.0)
    _close(db, sid)
    row = _auto_closed_row(db, sid)
    assert row["know"] == 0.91, "the newest recorded value wins"
    assert row["clarity"] == FULL["clarity"], "an unsupplied vector keeps its last recorded value"
    assert row["impact"] is None and row["engagement"] is None, "never recorded means NULL, not an invented 0.5"
    assert row["completion"] == 1.0, "the existing 'session ended = complete' stays"


# ── U2: goals-ready compared None with a threshold ──


def _ready(db, sid, **kw):
    from empirica.cli.command_handlers.goal_commands import _handle_goals_ready_command_helper

    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, beads_issue_id) "
        "VALUES ('g-ready', ?, 'an objective', '{}', 0.0, '{}', 'bd-1')",
        (sid,),
    )
    db.conn.commit()
    ready: list = []
    _handle_goals_ready_command_helper(
        [{"id": "bd-1", "priority": 2}],
        None,
        db,
        kw.get("max_uncertainty", 0.35),
        kw.get("min_confidence", 0.7),
        ready,
        sid,
    )
    return ready


def test_goals_ready_does_not_raise_when_the_latest_row_is_a_sparse_check(db):
    """The latest row is usually a CHECK that omits know or uncertainty: `None < 0.7` raised TypeError for every candidate item."""
    sid = db.create_session(ai_id="a7-ready")
    put(db, sid, "PREFLIGHT", FULL | {"know": 0.9, "uncertainty": 0.1}, ts=1000.0)
    put(db, sid, "CHECK", {"clarity": 0.9}, ts=2000.0)
    ready = _ready(db, sid)
    assert len(ready) == 1
    assert ready[0]["last_check_confidence"] == 0.9 and ready[0]["preflight_uncertainty"] == 0.1, (
        "last RECORDED know/uncertainty"
    )
    assert ready[0]["epistemic_ready"] is True


def test_goals_ready_says_not_ready_when_know_was_never_recorded(db):
    sid = db.create_session(ai_id="a7-ready")
    put(db, sid, "CHECK", {"clarity": 0.9}, ts=2000.0)
    ready = _ready(db, sid)
    assert ready[0]["epistemic_ready"] is False and "recorded" in ready[0].get("why_not_ready", "").lower()
