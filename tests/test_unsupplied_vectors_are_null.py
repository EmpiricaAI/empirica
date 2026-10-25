"""A vector the caller did not supply is NULL in the reflexes table, never an invented 0.5 (sweep item A7, goal 1cd7caa3).

`store_vectors` filled every missing or non-numeric vector with 0.5. Measured on this project's last 60 days: PREFLIGHT and POSTFLIGHT
rows always supply all 13, but CHECK rows supply a mean of 7.0 (min 2), so 53 of 60 CHECK rows carried up to eleven invented 0.5s that
every reader then took for a measurement. The columns are nullable and `reflex_data` already holds only what was supplied.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.session_database import SessionDatabase

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


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


def _row(db, row_id):
    conn = db.conn
    conn.row_factory = sqlite3.Row
    return dict(conn.execute("SELECT * FROM reflexes WHERE id = ?", (row_id,)).fetchone())


def test_a_check_that_supplies_four_vectors_stores_nine_nulls_not_nine_half_values(db):
    sid = db.create_session(ai_id="a7")
    rid = db.vectors.store_vectors(sid, "CHECK", {"know": 0.8, "uncertainty": 0.2, "context": 0.7, "clarity": 0.9})
    row = _row(db, rid)
    assert [row[k] for k in ("know", "uncertainty", "context", "clarity")] == [0.8, 0.2, 0.7, 0.9]
    unsupplied = [n for n in NAMES if n not in ("know", "uncertainty", "context", "clarity")]
    assert all(row[n] is None for n in unsupplied), {n: row[n] for n in unsupplied}


def test_a_full_set_is_stored_exactly_as_before(db):
    """Control: the change must not touch the rows that were already right."""
    sid = db.create_session(ai_id="a7")
    full = {n: round(0.05 * (i + 1), 2) for i, n in enumerate(NAMES)}
    row = _row(db, db.vectors.store_vectors(sid, "PREFLIGHT", full))
    assert {n: row[n] for n in NAMES} == full


def test_a_real_zero_is_stored_as_zero_not_as_missing(db):
    """completion 0.0 at PREFLIGHT is the normal value; a falsy-zero shortcut would read it as absent."""
    sid = db.create_session(ai_id="a7")
    row = _row(db, db.vectors.store_vectors(sid, "PREFLIGHT", {"completion": 0.0, "change": 0.0, "know": 0.6}))
    assert row["completion"] == 0.0 and row["change"] == 0.0 and row["know"] == 0.6


@pytest.mark.parametrize("bad", ["high", None, [0.5], {"v": 1}, True])
def test_a_non_numeric_value_is_null_not_half(db, bad):
    sid = db.create_session(ai_id="a7")
    row = _row(db, db.vectors.store_vectors(sid, "CHECK", {"know": 0.7, "do": bad}))
    assert row["know"] == 0.7 and row["do"] is None


def test_the_blob_keeps_only_what_was_supplied(db):
    import json

    sid = db.create_session(ai_id="a7")
    row = _row(db, db.vectors.store_vectors(sid, "CHECK", {"know": 0.8, "uncertainty": 0.2}))
    assert json.loads(row["reflex_data"])["vectors"] == {"know": 0.8, "uncertainty": 0.2}


def test_the_reader_returns_only_the_vectors_that_exist(db):
    """Control for the existing None-dropping reader the callers depend on."""
    sid = db.create_session(ai_id="a7")
    db.vectors.store_vectors(sid, "CHECK", {"know": 0.8, "uncertainty": 0.2})
    latest = db.vectors.get_latest_vectors(sid, "CHECK")
    assert latest is not None and latest["vectors"] == {"know": 0.8, "uncertainty": 0.2}
