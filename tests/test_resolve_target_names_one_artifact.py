"""Resolving by prefix touches exactly one artifact, or none.

finding-resolve and unknown-resolve ran UPDATE ... WHERE id LIKE prefix% with
no check on the match count, so an ambiguous prefix resolved every artifact
sharing it. Built under tmp_path with a real SessionDatabase.
"""

from __future__ import annotations

import pytest

TWIN_A = "e90c8237-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TWIN_B = "e90c8237-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LONE = "5c5600a0-91de-4fd7-bc24-64e85aabaaac"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "sessions.db"))
    monkeypatch.chdir(tmp_path)  # the notes mirror must not touch the checkout
    from empirica.data.session_database import SessionDatabase

    database = SessionDatabase()
    sid = database.create_session(ai_id="empirica")
    for fid in (TWIN_A, TWIN_B, LONE):
        database.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data)"
            " VALUES (?, 'p', ?, 'f', 0, '{}')",
            (fid, sid),
        )
        database.conn.execute(
            "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, unknown_data)"
            " VALUES (?, 'p', ?, 'u', 0, '{}')",
            (fid, sid),
        )
    database.conn.commit()
    yield database
    database.close()


def _resolved(db, table):
    return {r[0] for r in db.conn.execute(f"SELECT id FROM {table} WHERE is_resolved = 1")}


def test_an_ambiguous_prefix_resolves_nothing(db):
    assert db.resolve_finding("e90c8237", "x", resolution_kind="stale") is False
    assert db.resolve_unknown("e90c8237", "x") is False
    assert _resolved(db, "project_findings") == set()
    assert _resolved(db, "project_unknowns") == set()


def test_positive_control_a_unique_prefix_resolves_exactly_that_row(db):
    assert db.resolve_finding("5c5600a0", "x", resolution_kind="stale") is True
    assert db.resolve_unknown("5c5600a0", "x") is True
    assert _resolved(db, "project_findings") == {LONE}
    assert _resolved(db, "project_unknowns") == {LONE}


def test_a_full_id_still_wins_over_its_twin(db):
    assert db.resolve_finding(TWIN_A, "x", resolution_kind="stale") is True
    assert _resolved(db, "project_findings") == {TWIN_A}


def test_a_short_or_missing_prefix_is_refused_not_partially_applied(db):
    assert db.resolve_finding("e90c", "x") is False
    assert db.resolve_unknown("deadbeef", "x") is False
    assert _resolved(db, "project_findings") == set()
