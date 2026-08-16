"""The PREFLIGHT open-unknowns suggestion must be scoped to ACTIVE goals and name
ids — the earlier form was a session-wide bare count that repeated verbatim every
PREFLIGHT of a long-lived session until it read as furniture."""

from __future__ import annotations

import sqlite3
import time

from empirica.cli.command_handlers._workflow_preflight import _feedback_collect_suggestions

_RETRO_META = {"retrospective": {"artifact_counts": {"findings": 1}}}


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_id TEXT)")
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, session_id TEXT, status TEXT)")
    conn.execute(
        "CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, goal_id TEXT, "
        "session_id TEXT, is_resolved INTEGER, created_timestamp REAL, unknown TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('s1', 'p')")
    conn.commit()
    return conn


def _unknown(conn, uid, goal_id, resolved=0):
    conn.execute(
        "INSERT INTO project_unknowns VALUES (?,?,?,?,?,?)",
        (uid, goal_id, "s1", resolved, time.time(), f"unknown {uid}"),
    )
    conn.commit()


def test_suggestion_scoped_to_active_goals_with_ids():
    conn = _conn()
    conn.execute("INSERT INTO goals VALUES ('G', 's1', 'in_progress')")
    for i in range(3):
        _unknown(conn, f"u{i}", "G")
    out = _feedback_collect_suggestions(conn.cursor(), "s1", "p", _RETRO_META)
    hit = [s for s in out if "open unknowns under active goals" in s]
    assert len(hit) == 1
    assert "u0" in hit[0]  # ids named, actionable


def test_no_suggestion_when_unknowns_only_under_completed_goals():
    """The self-clearing property: goal closes → its unknowns stop nagging here
    (the goal-close moment is the stale-artifacts nudge's job instead)."""
    conn = _conn()
    conn.execute("INSERT INTO goals VALUES ('G', 's1', 'completed')")
    for i in range(5):
        _unknown(conn, f"u{i}", "G")
    out = _feedback_collect_suggestions(conn.cursor(), "s1", "p", _RETRO_META)
    assert not any("open unknowns" in s for s in out)


def test_no_suggestion_below_threshold():
    conn = _conn()
    conn.execute("INSERT INTO goals VALUES ('G', 's1', 'in_progress')")
    _unknown(conn, "u1", "G")
    out = _feedback_collect_suggestions(conn.cursor(), "s1", "p", _RETRO_META)
    assert not any("open unknowns" in s for s in out)
