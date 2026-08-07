"""Goals stranded with an empty project_id are invisible to the default listing.

A goal with no `project_id` is unaddressable rather than lost: every
project-scoped listing skips it, and the project-scoped one is the default.
Measured 2026-08-07 on the empirica practice — 305 of 1520 — and the pattern is
fleet-wide (cortex 72, one archive 216, autopilot 9).

Migration 065 writes **only where the goal's own session row names a project**.
That restraint is the load-bearing part: a practice db can hold more than one
registered project (this one holds `Empirica` and `empirica-platform`), so
assigning the remainder to the main project is an inference — and an inference
shipped as a migration hardens into a fact on every machine at once.
"""

from __future__ import annotations

import json
import sqlite3

from empirica.data.migrations.migrations import migration_065_backfill_goal_project_id_from_session as migrate

PROJ = "748a81a2-ac14-45b8-a185-994997b76828"
OTHER = "47e34466-47ba-4a30-80be-987ec17416d8"


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, session_id TEXT, project_id TEXT, goal_data TEXT)")
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_id TEXT)")
    return conn


def test_a_session_evidenced_goal_is_scoped(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (PROJ,))
    conn.execute("INSERT INTO goals VALUES ('g1', 's1', '', NULL)")
    migrate(conn.cursor())
    conn.commit()
    assert conn.execute("SELECT project_id FROM goals WHERE id='g1'").fetchone()[0] == PROJ


def test_a_goal_with_no_evidence_is_left_alone(tmp_path):
    """The restraint that keeps an inference out of 39 databases."""
    conn = _db(tmp_path)
    conn.execute("INSERT INTO goals VALUES ('g2', 's-missing', '', NULL)")
    conn.execute("INSERT INTO sessions VALUES ('s2', '')")
    conn.execute("INSERT INTO goals VALUES ('g3', 's2', '', NULL)")
    migrate(conn.cursor())
    conn.commit()
    rows = dict(conn.execute("SELECT id, project_id FROM goals").fetchall())
    assert rows["g2"] == "", "no session row — nothing evidences a project"
    assert rows["g3"] == "", "session exists but names no project — still no evidence"


def test_an_existing_project_id_is_never_overwritten(tmp_path):
    """Including one pointing at the practice's OTHER registered project."""
    conn = _db(tmp_path)
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (PROJ,))
    conn.execute("INSERT INTO goals VALUES ('g4', 's1', ?, NULL)", (OTHER,))
    migrate(conn.cursor())
    conn.commit()
    assert conn.execute("SELECT project_id FROM goals WHERE id='g4'").fetchone()[0] == OTHER


def test_provenance_is_recorded_so_the_write_is_reversible(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (PROJ,))
    conn.execute("INSERT INTO goals VALUES ('g5', 's1', '', ?)", (json.dumps({"objective": "x"}),))
    migrate(conn.cursor())
    conn.commit()
    data = json.loads(conn.execute("SELECT goal_data FROM goals WHERE id='g5'").fetchone()[0])
    assert data["objective"] == "x", "existing goal_data must survive"
    bf = data["project_id_backfill"]
    assert bf["assigned"] == PROJ and bf["prior"] == "" and bf["tier"] == "session_evidence"


def test_malformed_goal_data_does_not_break_the_row(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (PROJ,))
    conn.execute("INSERT INTO goals VALUES ('g6', 's1', '', 'not json')")
    migrate(conn.cursor())
    conn.commit()
    assert conn.execute("SELECT project_id FROM goals WHERE id='g6'").fetchone()[0] == PROJ


def test_missing_tables_are_a_noop(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    migrate(conn.cursor())  # must not raise
