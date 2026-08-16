"""Tests for the gardening deterministic-nudge (_maybe_add_stale_artifacts_note).

Surfaces OPEN unknowns under the goal(s) in play, logged in an EARLIER transaction.
The three bounds that keep it high-signal (scope-to-goal, freshness, unknowns-only)
each get a test, plus the silent/tolerant cases.
"""

from __future__ import annotations

import sqlite3
import time

from empirica.cli.command_handlers._workflow_shared import _maybe_add_stale_artifacts_note

# Minimal DDL — only the columns the query touches.
_GOALS_DDL = "CREATE TABLE goals (id TEXT PRIMARY KEY, is_completed INTEGER, transaction_id TEXT)"
_UNKNOWNS_DDL = (
    "CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, goal_id TEXT, is_resolved INTEGER, "
    "transaction_id TEXT, created_timestamp REAL, unknown TEXT)"
)
_FINDINGS_DDL = "CREATE TABLE project_findings (id TEXT PRIMARY KEY, goal_id TEXT, transaction_id TEXT)"


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(_GOALS_DDL)
    conn.execute(_UNKNOWNS_DDL)
    conn.execute(_FINDINGS_DDL)
    return conn


def _goal(conn, gid, completed=0, tx=None):
    conn.execute("INSERT INTO goals VALUES (?,?,?)", (gid, completed, tx))
    conn.commit()


def _unknown(conn, uid, goal_id, resolved=0, tx="t-old", text="why does X?"):
    conn.execute(
        "INSERT INTO project_unknowns VALUES (?,?,?,?,?,?)",
        (uid, goal_id, resolved, tx, time.time(), text),
    )
    conn.commit()


def _finding(conn, fid, goal_id, tx):
    conn.execute("INSERT INTO project_findings VALUES (?,?,?)", (fid, goal_id, tx))
    conn.commit()


# --- fires ----------------------------------------------------------------- #
def test_fires_for_open_unknown_under_completed_goal():
    """Goal completed THIS tx with a still-open unknown from an earlier tx = debt."""
    conn = _conn()
    _goal(conn, "G", completed=1, tx="t-now")
    _unknown(conn, "u1", "G", resolved=0, tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert retro["stale_artifacts_in_scope"] == 1
    assert "u1" in retro["stale_artifacts_note"]
    assert "unknown-resolve" in retro["stale_artifacts_note"]


def test_fires_when_goal_in_play_via_this_tx_artifact():
    """Goal not completed, but this tx logged a finding under it → goal is in play,
    so its earlier open unknowns surface."""
    conn = _conn()
    _goal(conn, "G", completed=0, tx=None)
    _finding(conn, "f1", "G", tx="t-now")  # this tx touched goal G
    _unknown(conn, "u1", "G", resolved=0, tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert retro["stale_artifacts_in_scope"] == 1


# --- the three bounds ------------------------------------------------------ #
def test_freshness_excludes_unknown_logged_this_tx():
    """An open unknown logged in THIS transaction is current work, not stale."""
    conn = _conn()
    _goal(conn, "G", completed=1, tx="t-now")
    _unknown(conn, "u_fresh", "G", resolved=0, tx="t-now")  # same tx → excluded
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert "stale_artifacts_note" not in retro


def test_scope_excludes_unknown_under_goal_not_in_play():
    """An open unknown under a goal NOT in play (not completed, no artifact this tx)
    must not surface — no whole-graph scan."""
    conn = _conn()
    _goal(conn, "G", completed=1, tx="t-now")  # in play
    _goal(conn, "OTHER", completed=0, tx=None)  # not in play
    _unknown(conn, "u_other", "OTHER", resolved=0, tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert "stale_artifacts_note" not in retro


def test_resolved_unknown_excluded():
    conn = _conn()
    _goal(conn, "G", completed=1, tx="t-now")
    _unknown(conn, "u_done", "G", resolved=1, tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert "stale_artifacts_note" not in retro


# --- silent / tolerant ----------------------------------------------------- #
def test_silent_when_no_goal_in_play():
    conn = _conn()
    _unknown(conn, "u1", "G", resolved=0, tx="t-old")  # G never a goal-in-play
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert retro == {}


def test_no_transaction_id_is_noop():
    conn = _conn()
    _goal(conn, "G", completed=1, tx="t-now")
    _unknown(conn, "u1", "G", resolved=0, tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", None, retro)
    assert retro == {}


def test_tolerates_missing_tables():
    conn = sqlite3.connect(":memory:")  # no goals/unknowns tables
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)  # must not raise
    assert retro == {}


# --- unverified assumptions (the pre-blindspot sibling debt) ---------------- #
_ASSUMPTIONS_DDL = (
    "CREATE TABLE assumptions (id TEXT PRIMARY KEY, goal_id TEXT, status TEXT, "
    "transaction_id TEXT, created_timestamp REAL, assumption TEXT)"
)


def _conn_with_assumptions():
    conn = _conn()
    conn.execute(_ASSUMPTIONS_DDL)
    return conn


def _assumption(conn, aid, goal_id, status="unverified", tx="t-old", text="taking X for granted"):
    conn.execute(
        "INSERT INTO assumptions VALUES (?,?,?,?,?,?)",
        (aid, goal_id, status, tx, time.time(), text),
    )
    conn.commit()


def test_fires_for_unverified_assumption_under_goal_in_play():
    conn = _conn_with_assumptions()
    _goal(conn, "G", completed=1, tx="t-now")
    _assumption(conn, "a1", "G", status="unverified", tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert retro["stale_artifacts_in_scope"] == 1
    assert "a1" in retro["stale_artifacts_note"]
    assert "unverified assumption" in retro["stale_artifacts_note"]


def test_verified_assumption_excluded():
    conn = _conn_with_assumptions()
    _goal(conn, "G", completed=1, tx="t-now")
    _assumption(conn, "a_ok", "G", status="verified", tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert "stale_artifacts_note" not in retro


def test_fresh_assumption_excluded():
    conn = _conn_with_assumptions()
    _goal(conn, "G", completed=1, tx="t-now")
    _assumption(conn, "a_new", "G", status="unverified", tx="t-now")  # this tx
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert "stale_artifacts_note" not in retro


def test_unknowns_and_assumptions_both_counted():
    conn = _conn_with_assumptions()
    _goal(conn, "G", completed=1, tx="t-now")
    _unknown(conn, "u1", "G", resolved=0, tx="t-old")
    _assumption(conn, "a1", "G", status="unverified", tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert retro["stale_artifacts_in_scope"] == 2
    assert "u1" in retro["stale_artifacts_note"]
    assert "a1" in retro["stale_artifacts_note"]


def test_unknowns_still_surface_when_assumptions_table_absent():
    """Older DB without the assumptions table: the unknowns half still works."""
    conn = _conn()  # no assumptions table
    _goal(conn, "G", completed=1, tx="t-now")
    _unknown(conn, "u1", "G", resolved=0, tx="t-old")
    retro: dict = {}
    _maybe_add_stale_artifacts_note(conn.cursor(), "s1", "t-now", retro)
    assert retro["stale_artifacts_in_scope"] == 1
    assert "u1" in retro["stale_artifacts_note"]
