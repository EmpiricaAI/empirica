"""Migration 073 — a verification row names the transaction it graded.

`grounded_verifications` had one transaction column, `parent_transaction_id`,
which is the parent of a compliance-loop retry and NULL on ordinary rows. A
reader joining through it got nothing; a reader matching rows to POSTFLIGHTs by
session and time window got three wrong answers in a day.

The store is the real one, built under tmp_path, so the column the insert names
is the column the migrations actually produce.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from empirica.core.post_test.grounded_calibration import GroundedCalibrationManager
from empirica.data.migrations.migrations import migration_073_verification_transaction_id


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "sessions.db"))
    from empirica.data.session_database import SessionDatabase

    database = SessionDatabase()
    session_id = database.create_session(ai_id="empirica")
    yield SimpleNamespace(conn=database.conn, session_id=session_id)
    database.close()


def _assessment():
    return SimpleNamespace(
        grounded={},
        self_assessed={"know": 0.7},
        calibration_gaps={"know": 0.1},
        grounded_coverage=0.5,
        overall_calibration_score=0.2,
    )


def _bundle():
    return SimpleNamespace(items=[], sources_available=["git"], sources_failed=[])


def test_a_stored_verification_joins_to_its_transaction(db):
    manager = GroundedCalibrationManager(db)
    vid = manager.store_verification(db.session_id, _assessment(), _bundle(), phase="combined", transaction_id="tx-1")
    row = db.conn.execute(
        "SELECT transaction_id, parent_transaction_id FROM grounded_verifications WHERE verification_id = ?", (vid,)
    ).fetchone()
    assert tuple(row) == ("tx-1", None)


def test_positive_control_no_transaction_id_stays_null_not_empty(db):
    manager = GroundedCalibrationManager(db)
    vid = manager.store_verification(db.session_id, _assessment(), _bundle(), transaction_id="")
    row = db.conn.execute(
        "SELECT transaction_id FROM grounded_verifications WHERE verification_id = ?", (vid,)
    ).fetchone()
    assert row[0] is None


def test_the_phase_runner_passes_the_transaction_id_through():
    import inspect

    from empirica.core.post_test import grounded_calibration as gc

    source = inspect.getsource(gc._run_single_phase_verification)
    assert "transaction_id=transaction_id" in source.split("manager.store_verification(")[1].split(")")[0]


def test_the_migration_is_idempotent_and_does_not_backfill():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE grounded_verifications (verification_id TEXT PRIMARY KEY, session_id TEXT)")
    conn.execute("INSERT INTO grounded_verifications VALUES ('v-old', 's1')")
    migration_073_verification_transaction_id(conn.cursor())
    migration_073_verification_transaction_id(conn.cursor())
    assert conn.execute("SELECT transaction_id FROM grounded_verifications").fetchone()[0] is None
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(grounded_verifications)").fetchall()}
    assert "idx_grounded_verifications_transaction" in indexes
