"""A CHECK belongs to its transaction, not to the session it happened in.

detect_phase_boundary selected CHECK rows by session_id alone. After the first
CHECK in a session every later POSTFLIGHT was phase-aware, and its noetic row
graded that transaction's evidence against an earlier, unrelated CHECK. Two
peers measured noetic and praxic pairs on days with no CHECK at all.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from empirica.core.post_test.phase_boundary import detect_phase_boundary


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE reflexes (session_id TEXT, transaction_id TEXT, phase TEXT, timestamp REAL,"
        " reflex_data TEXT, know REAL, uncertainty REAL, completion REAL, context REAL, do REAL,"
        " signal REAL, coherence REAL, engagement REAL)"
    )
    return SimpleNamespace(conn=conn)


def _row(db, tx, phase, ts, know, decision=None):
    data = json.dumps({"decision": decision}) if decision else None
    db.conn.execute(
        "INSERT INTO reflexes VALUES ('s', ?, ?, ?, ?, ?, 0.3, 0, 0.5, 0.5, 0.5, 0.5, 0.5)",
        (tx, phase, ts, data, know),
    )


def _two_transactions():
    db = _db()
    _row(db, "tx-old", "PREFLIGHT", 1.0, 0.4)
    _row(db, "tx-old", "CHECK", 2.0, 0.9, "proceed")
    _row(db, "tx-new", "PREFLIGHT", 10.0, 0.6)
    return db


def test_an_earlier_transactions_check_does_not_make_this_one_phase_aware():
    boundary = detect_phase_boundary("s", _two_transactions(), transaction_id="tx-new")
    assert boundary["has_check"] is False
    assert boundary["proceed_check_vectors"] is None
    assert boundary["preflight_vectors"]["know"] == 0.6


def test_positive_control_the_transactions_own_check_is_found():
    boundary = detect_phase_boundary("s", _two_transactions(), transaction_id="tx-old")
    assert boundary["has_check"] is True and boundary["check_count"] == 1
    assert boundary["proceed_check_vectors"]["know"] == 0.9
    assert boundary["preflight_vectors"]["know"] == 0.4


def test_without_a_transaction_id_the_session_scope_still_answers():
    boundary = detect_phase_boundary("s", _two_transactions())
    assert boundary["has_check"] is True
    assert boundary["preflight_vectors"]["know"] == 0.6


def test_postflight_passes_its_transaction_id():
    import inspect

    from empirica.cli.command_handlers import _workflow_postflight as wp

    assert "detect_phase_boundary(session_id, db, transaction_id=transaction_id)" in inspect.getsource(
        wp._run_grounded_verification
    )
