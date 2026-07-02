"""Tests for the `empirica note` scratchpad.

Covers the storage/query logic and the POSTFLIGHT retrospective surfacing
directly (no full session needed): notes resurface PROJECT-wide until triaged
(not transaction-scoped — that stranded cross-transaction follow-ups), are
triage-aware, and degrade safely when the table is absent on older DBs.
"""

from __future__ import annotations

import sqlite3
import time

from empirica.cli.command_handlers import note_commands as nc
from empirica.cli.command_handlers._workflow_shared import _maybe_add_untriaged_notes
from empirica.data.schema.epistemic_schema import SCHEMAS


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(nc._NOTES_DDL)
    return conn


def _add(conn, text, session_id="s1", transaction_id="t1", project_id="p", tag=None, triaged=0):
    conn.execute(
        "INSERT INTO notes (note_id, session_id, transaction_id, project_id, "
        "ai_id, text, tag, created_at, triaged) VALUES (?,?,?,?,?,?,?,?,?)",
        (text, session_id, transaction_id, project_id, "a", text, tag, time.time(), triaged),
    )
    conn.commit()


def _ctx(session_id="s1", transaction_id="t1", project_id="p"):
    return {"session_id": session_id, "transaction_id": transaction_id, "project_id": project_id}


# --- schema ---------------------------------------------------------------- #
def test_notes_table_in_schema():
    assert any("CREATE TABLE IF NOT EXISTS notes" in s for s in SCHEMAS)


# --- query logic ----------------------------------------------------------- #
def test_query_untriaged_resurfaces_across_transactions():
    """The fix: notes captured in ANY transaction of the project resurface —
    a note left in t1 must be visible while working in t2 (and vice versa)."""
    conn = _conn()
    _add(conn, "a", transaction_id="t1")
    _add(conn, "b", transaction_id="t1")
    _add(conn, "c", transaction_id="t2")  # different transaction — must STILL surface
    _add(conn, "d", transaction_id="t1", triaged=1)  # already triaged — excluded
    # Querying from a THIRD transaction still sees the whole project backlog.
    rows = nc._query_untriaged(conn, _ctx(transaction_id="t3"))
    assert sorted(r[1] for r in rows) == ["a", "b", "c"]


def test_query_untriaged_project_scoped_excludes_other_projects():
    conn = _conn()
    _add(conn, "mine", project_id="p")
    _add(conn, "theirs", project_id="other")
    rows = nc._query_untriaged(conn, _ctx(project_id="p"))
    assert {r[1] for r in rows} == {"mine"}


def test_query_untriaged_session_fallback_when_no_project():
    conn = _conn()
    _add(conn, "a", project_id=None, transaction_id="t1")
    _add(conn, "b", project_id=None, transaction_id="t2")
    # No project_id in ctx → fall back to session scope (both surface).
    rows = nc._query_untriaged(conn, _ctx(project_id=None, transaction_id=None))
    assert {r[1] for r in rows} == {"a", "b"}


def test_clear_marks_triaged():
    conn = _conn()
    _add(conn, "a")
    _add(conn, "b")
    nc._clear_notes(conn, _ctx(), "json")
    assert nc._query_untriaged(conn, _ctx()) == []


# --- retrospective surfacing ----------------------------------------------- #
def test_retrospective_surfaces_untriaged_notes():
    conn = _conn()
    _add(conn, "promote me", tag="followup")
    retro: dict = {}
    _maybe_add_untriaged_notes(conn.cursor(), "s1", "t1", retro)
    assert retro["untriaged_notes"] == [{"text": "promote me", "tag": "followup"}]
    assert "1 untriaged note" in retro["untriaged_notes_hint"]


def test_retrospective_silent_when_none():
    conn = _conn()
    retro: dict = {}
    _maybe_add_untriaged_notes(conn.cursor(), "s1", "t1", retro)
    assert "untriaged_notes" not in retro


def test_retrospective_tolerates_missing_table():
    conn = sqlite3.connect(":memory:")  # no notes table
    retro: dict = {}
    _maybe_add_untriaged_notes(conn.cursor(), "s1", "t1", retro)  # must not raise
    assert retro == {}


def test_retrospective_resurfaces_project_backlog_across_transactions():
    """POSTFLIGHT surfaces the whole PROJECT backlog, not just this transaction's
    — so notes left in earlier transactions reliably reappear for triage."""
    conn = _conn()
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s1', 'p')")
    conn.commit()
    _add(conn, "left in t1", transaction_id="t1")
    _add(conn, "left in t2", transaction_id="t2")
    _add(conn, "done", transaction_id="t1", triaged=1)
    retro: dict = {}
    # POSTFLIGHTing t3 still surfaces t1 + t2's untriaged notes (project-scoped).
    _maybe_add_untriaged_notes(conn.cursor(), "s1", "t3", retro)
    texts = sorted(n["text"] for n in retro["untriaged_notes"])
    assert texts == ["left in t1", "left in t2"]
    assert "for this project" in retro["untriaged_notes_hint"]
