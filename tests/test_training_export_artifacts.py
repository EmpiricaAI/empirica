"""The training export reads the real decisions table and attributes by transaction (goal 26aff05d, with the d687086e criteria).

Two defects, both silent:

* ``_collect_transaction_artifacts`` selected from ``decisions_made``. The table is ``decisions``. The ``OperationalError`` was
  swallowed along with the legitimate "this older database has no such table", so decisions were never exported and nothing said so.
* artifacts, CHECK decisions and grounded calibration were attributed to a transaction by the TIME WINDOW around it (grounded
  calibration by "the five minutes after POSTFLIGHT"). Two transactions of one session that overlap, or follow each other within
  five minutes, exchanged each other's artifacts.

The tests run on the real schema through ``SessionDatabase``; a hand-built table named like the code expects would have been green.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

import pytest

from empirica.cli.command_handlers import training_commands as tc
from empirica.data.session_database import SessionDatabase

PID = str(uuid.uuid4())
TX_A, TX_B = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    d.conn.execute("PRAGMA foreign_keys=OFF")
    d.conn.row_factory = sqlite3.Row
    yield d
    d.close()


@pytest.fixture
def sid(db):
    return db.create_session(ai_id="training-test")


def _collect(db, sid, tx=None):
    return tc._collect_transaction_artifacts(db.conn, sid, 0.0, 4e9, tx)


# --- decisions ---------------------------------------------------------------------------------------------------------------


def test_decisions_are_exported(db, sid):
    db.log_decision(PID, sid, "use sqlite", "it is already there", reversibility="committal", transaction_id=TX_A)
    out = _collect(db, sid, TX_A)
    assert out["decisions"] == [
        {"choice": "use sqlite", "rationale": "it is already there", "reversibility": "committal"}
    ]


def test_a_missing_artifact_table_is_still_skipped_quietly(db, sid, caplog):
    """The legitimate case the broad except was written for: an older database without the table."""
    db.log_finding(PID, sid, "kept", transaction_id=TX_A)
    db.conn.execute("DROP TABLE decisions")
    with caplog.at_level(logging.WARNING):
        out = _collect(db, sid, TX_A)
    assert [f["finding"] for f in out["findings"]] == ["kept"] and "decisions" not in out
    assert caplog.text == ""


def test_any_other_query_failure_is_reported_not_swallowed(db, sid, caplog):
    """A column the code expects and the table lacks is a bug in the exporter; it must not read as 'nothing to export'."""
    db.log_finding(PID, sid, "kept", transaction_id=TX_A)
    db.conn.execute("ALTER TABLE decisions RENAME COLUMN rationale TO rationale_renamed")
    with caplog.at_level(logging.WARNING):
        out = _collect(db, sid, TX_A)
    assert "decisions" not in out
    assert "decisions" in caplog.text and "rationale" in caplog.text


# --- attribution -------------------------------------------------------------------------------------------------------------


def test_two_transactions_in_one_window_do_not_exchange_artifacts(db, sid):
    db.log_finding(PID, sid, "belongs to A", transaction_id=TX_A)
    db.log_finding(PID, sid, "belongs to B", transaction_id=TX_B)
    assert [f["finding"] for f in _collect(db, sid, TX_A)["findings"]] == ["belongs to A"]
    assert [f["finding"] for f in _collect(db, sid, TX_B)["findings"]] == ["belongs to B"]


def test_an_artifact_with_no_transaction_still_falls_back_to_the_window(db, sid):
    """Unattributed rows (written before transaction ids existed) belong to no other transaction, so the window decides."""
    db.log_finding(PID, sid, "legacy row")
    db.log_finding(PID, sid, "other transaction", transaction_id=TX_B)
    assert [f["finding"] for f in _collect(db, sid, TX_A)["findings"]] == ["legacy row"]


def test_without_a_transaction_id_the_window_decides_as_before(db, sid):
    db.log_finding(PID, sid, "a", transaction_id=TX_A)
    db.log_finding(PID, sid, "b", transaction_id=TX_B)
    assert sorted(f["finding"] for f in _collect(db, sid)["findings"]) == ["a", "b"]


def _grounded(db, sid, tx, score, created_at):
    db.conn.execute(
        "INSERT INTO grounded_verifications (verification_id, session_id, ai_id, self_assessed_vectors, grounded_vectors, "
        "calibration_gaps, grounded_coverage, overall_calibration_score, evidence_count, sources_available, created_at, "
        "transaction_id) VALUES (?, ?, 'a', '{}', '{}', '{}', 0.5, ?, 1, '[]', ?, ?)",
        (str(uuid.uuid4()), sid, score, created_at, tx),
    )


def test_grounded_calibration_is_the_transactions_own_not_the_neighbours(db, sid):
    _grounded(db, sid, TX_A, 0.9, 1000.0)
    _grounded(db, sid, TX_B, 0.1, 1100.0)  # B's postflight lands inside A's +300s window
    got = tc._collect_grounded_calibration(db.conn, sid, 1000.0, TX_A)
    assert got is not None and got["calibration_score"] == pytest.approx(0.9)


def test_grounded_calibration_without_a_transaction_id_keeps_the_window(db, sid):
    _grounded(db, sid, TX_A, 0.9, 1000.0)
    got = tc._collect_grounded_calibration(db.conn, sid, 1000.0)
    assert got is not None and got["calibration_score"] == pytest.approx(0.9)


def _check(db, sid, tx, ts, know):
    db.conn.execute(
        "INSERT INTO reflexes (session_id, phase, timestamp, know, uncertainty, completion, clarity, transaction_id, "
        "reflex_data) VALUES (?, 'CHECK', ?, ?, 0.1, 0.5, 0.5, ?, '{\"decision\": \"proceed\"}')",
        (sid, ts, know, tx),
    )


def test_check_decisions_are_the_transactions_own(db, sid):
    _check(db, sid, TX_A, 100.0, 0.81)
    _check(db, sid, TX_B, 101.0, 0.22)  # same window, the other transaction
    got = tc._collect_check_decisions(db.conn, sid, 50.0, 200.0, TX_A)
    assert got is not None and [c["vectors"]["know"] for c in got] == [pytest.approx(0.81)]
    assert len(tc._collect_check_decisions(db.conn, sid, 50.0, 200.0)) == 2, "no id: the window decides, as before"
