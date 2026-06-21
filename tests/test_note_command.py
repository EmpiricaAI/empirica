"""Tests for the `empirica note` scratchpad.

Covers the storage/query logic and the POSTFLIGHT retrospective surfacing
directly (no full session needed): notes are transaction-scoped, triage-aware,
and degrade safely when the table is absent on older DBs.
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


def _add(conn, text, session_id="s1", transaction_id="t1", tag=None, triaged=0):
    conn.execute(
        "INSERT INTO notes (note_id, session_id, transaction_id, project_id, "
        "ai_id, text, tag, created_at, triaged) VALUES (?,?,?,?,?,?,?,?,?)",
        (text, session_id, transaction_id, "p", "a", text, tag, time.time(), triaged),
    )
    conn.commit()


def _ctx(session_id="s1", transaction_id="t1"):
    return {"session_id": session_id, "transaction_id": transaction_id}


# --- schema ---------------------------------------------------------------- #
def test_notes_table_in_schema():
    assert any("CREATE TABLE IF NOT EXISTS notes" in s for s in SCHEMAS)


# --- query logic ----------------------------------------------------------- #
def test_query_untriaged_scoped_to_transaction():
    conn = _conn()
    _add(conn, "a", transaction_id="t1")
    _add(conn, "b", transaction_id="t1")
    _add(conn, "c", transaction_id="t2")           # other transaction
    _add(conn, "d", transaction_id="t1", triaged=1)  # already triaged
    rows = nc._query_untriaged(conn, _ctx(transaction_id="t1"))
    texts = sorted(r[1] for r in rows)
    assert texts == ["a", "b"]


def test_query_untriaged_session_scope_when_no_transaction():
    conn = _conn()
    _add(conn, "a", transaction_id="t1")
    _add(conn, "b", transaction_id=None)
    rows = nc._query_untriaged(conn, _ctx(transaction_id=None))
    assert {r[1] for r in rows} == {"a", "b"}  # all untriaged for the session


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
