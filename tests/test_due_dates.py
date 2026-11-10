"""Due dates on goals, unknowns and assumptions (prop_bnviyv2epvg27kgmqycwlflshm, goal e7156005).

Planned work had no way to resurface: a date written into a description fires nothing. ``due_at`` is an ISO date; an
item whose date has come is listed under Due at bootstrap and session start until it is done, snoozed (a new date) or
dropped (resolved). Only open items are due.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from empirica.data.due import DueDateError, due_items, parse_due, set_due
from tests.schema_shapes import build_db

TODAY = date(2026, 11, 10)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(build_db(tmp_path / "sessions.db", "current"))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.mark.parametrize("table", ["goals", "project_unknowns", "assumptions"])
def test_the_three_tables_have_somewhere_to_put_a_date(conn, table):
    assert "due_at" in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-12-01", "2026-12-01"),
        ("+7d", "2026-11-17"),
        ("+2w", "2026-11-24"),
        ("+0d", "2026-11-10"),
        (" 2027-01-05 ", "2027-01-05"),
    ],
)
def test_parse_due_takes_a_date_or_an_offset(text, expected):
    assert parse_due(text, today=TODAY) == expected


@pytest.mark.parametrize("text", ["tomorrow", "2026-13-01", "+3m", "+-1d", "", "7d"])
def test_parse_due_refuses_anything_else(text):
    with pytest.raises(DueDateError):
        parse_due(text, today=TODAY)


def _goal(conn, gid, status="planned", due=None, archived=0):
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, status, created_timestamp, is_completed, project_id, archived, due_at, goal_data) "
        "VALUES (?, 's', ?, '{}', ?, 0, ?, 'p', ?, ?, '{}')",
        (gid, f"objective {gid}", status, int(status == "completed"), archived, due),
    )


def _unknown(conn, uid, resolved=0, due=None):
    conn.execute(
        "INSERT INTO project_unknowns (id, project_id, session_id, unknown, is_resolved, created_timestamp, due_at, unknown_data) "
        "VALUES (?, 'p', 's', ?, ?, 0, ?, '{}')",
        (uid, f"unknown {uid}", resolved, due),
    )


def _assumption(conn, aid, status="unverified", due=None):
    conn.execute(
        "INSERT INTO assumptions (id, assumption, confidence, status, project_id, session_id, created_timestamp, due_at) "
        "VALUES (?, ?, 0.5, ?, 'p', 's', 0, ?)",
        (aid, f"assumption {aid}", status, due),
    )


def test_only_open_items_whose_date_has_come_are_due_oldest_first(conn):
    _goal(conn, "g-due", due="2026-11-09")
    _goal(conn, "g-today", status="in_progress", due="2026-11-10")
    _goal(conn, "g-later", due="2026-11-11")
    _goal(conn, "g-done", status="completed", due="2026-11-01")
    _goal(conn, "g-dropped", status="abandoned", due="2026-11-01")
    _goal(conn, "g-archived", archived=1, due="2026-11-01")
    _goal(conn, "g-nodate")
    _unknown(conn, "u-due", due="2026-11-02")
    _unknown(conn, "u-resolved", resolved=1, due="2026-11-02")
    _assumption(conn, "a-due", due="2026-11-05")
    _assumption(conn, "a-verified", status="verified", due="2026-11-05")
    items = due_items(conn, "p", today=TODAY)
    assert [(i["type"], i["id"]) for i in items] == [
        ("unknown", "u-due"),
        ("assumption", "a-due"),
        ("goal", "g-due"),
        ("goal", "g-today"),
    ]
    assert items[0]["text"] == "unknown u-due" and items[0]["due_at"] == "2026-11-02"


def test_the_list_is_capped_and_says_how_many_it_left_out(conn):
    for i in range(7):
        _goal(conn, f"g{i}", due=f"2026-11-0{i + 1}")
    items = due_items(conn, "p", today=TODAY, limit=5)
    assert len(items) == 5 and items[-1]["more"] == 2


def test_another_projects_items_are_not_due_here(conn):
    _goal(conn, "g-mine", due="2026-11-01")
    conn.execute("UPDATE goals SET project_id = 'other' WHERE id = 'g-mine'")
    assert due_items(conn, "p", today=TODAY) == []


def test_set_due_writes_the_column_and_refuses_an_unknown_type(conn):
    _goal(conn, "g1")
    assert set_due(conn, "goal", "g1", "2026-12-24") is True
    assert conn.execute("SELECT due_at FROM goals WHERE id = 'g1'").fetchone()[0] == "2026-12-24"
    assert set_due(conn, "goal", "missing", "2026-12-24") is False
    with pytest.raises(DueDateError):
        set_due(conn, "finding", "x", "2026-12-24")


def test_due_option_reads_the_json_config_first_then_the_flag():
    from types import SimpleNamespace

    from empirica.data.due import due_option

    assert due_option({"due": "2026-12-01"}, SimpleNamespace(due="2027-01-01")) == "2026-12-01"
    assert due_option(None, SimpleNamespace(due="2027-01-01")) == "2027-01-01"
    assert due_option([1, 2], SimpleNamespace()) is None
    with pytest.raises(DueDateError):
        due_option({"due": "soon"}, SimpleNamespace())


def test_a_response_names_the_date_or_says_how_to_set_one():
    from empirica.data.due import due_fields

    assert due_fields("goal", "2026-12-01", hint=True) == {"due_at": "2026-12-01"}
    assert "no due date" in due_fields("goal", None, hint=True)["due_hint"]
    assert due_fields("assumption", None, hint=False) == {}
