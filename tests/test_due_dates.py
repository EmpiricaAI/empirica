"""Due dates on goals, unknowns and assumptions (prop_bnviyv2epvg27kgmqycwlflshm, goal e7156005).

Planned work had no way to resurface: a date written into a description fires nothing. ``due_at`` is an ISO date; an
item whose date has come is listed under Due at bootstrap and session start until it is done, snoozed (a new date) or
dropped (resolved). Only open items are due.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

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


# ── snooze: update-artifacts may move or clear the date ─────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["goal", "unknown", "assumption"])
def test_a_snooze_is_a_due_at_correction_normalised_to_a_date(kind):
    from empirica.data.artifact_fields import filter_updates

    updates, rejected = filter_updates(kind, {"due_at": "2026-12-24"})
    assert updates == {"due_at": "2026-12-24"} and rejected == []
    relative, _ = filter_updates(kind, {"due_at": "+1w"})
    assert len(relative["due_at"]) == 10 and relative["due_at"][4] == "-"
    cleared, _ = filter_updates(kind, {"due_at": None})
    assert cleared == {"due_at": None}


def test_an_unreadable_snooze_is_rejected_and_other_types_cannot_carry_a_date():
    from empirica.data.artifact_fields import filter_updates, value_problems

    assert filter_updates("goal", {"due_at": "later"}) == ({}, ["due_at"])
    assert "due_at" in value_problems("goal", {"due_at": "later"})
    assert filter_updates("finding", {"due_at": "2026-12-24"}) == ({}, ["due_at"])


# ── the Due section: bootstrap JSON, the human bootstrap, and the SessionStart context ──────────────────────

_HOOKS = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks"


@pytest.fixture(scope="module")
def session_init():
    import importlib.util
    import sys

    sys.path.insert(0, str(_HOOKS.parent / "lib"))
    sys.path.insert(0, str(_HOOKS))
    spec = importlib.util.spec_from_file_location("session_init_due", _HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


DUE = [
    {"type": "goal", "id": "g1234567890", "text": "ship the thing", "due_at": "2026-11-01"},
    {"type": "unknown", "id": "u1234567890", "text": "does X hold", "due_at": "2026-11-09", "more": 3},
]


def test_session_start_lists_due_items_first_with_the_three_answers(session_init):
    text = session_init.format_context({"due": DUE, "goals": [{"objective": "other goal"}]})
    assert text.index("**Due") < text.index("**Active Goals")
    assert "goal g1234567 (2026-11-01): ship the thing" in text and "3 more" in text
    assert "snooze" in text and "update-artifacts" in text and "drop" in text


def test_session_start_says_nothing_about_due_when_nothing_is(session_init):
    assert "Due" not in session_init.format_context({"due": [], "goals": [{"objective": "g"}]})


def test_session_start_reads_due_from_the_bootstrap_breadcrumbs(session_init, monkeypatch):
    import json as _json
    import subprocess

    payload = {"breadcrumbs": {"goals": [], "findings": [], "unknowns": [], "due": DUE}}
    done = subprocess.CompletedProcess([], 0, stdout=_json.dumps(payload), stderr="")
    monkeypatch.setattr(session_init.subprocess, "run", lambda *a, **k: done)
    _, ctx = session_init._run_bootstrap("sid", {})
    assert ctx["due"] == DUE


def test_bootstrap_human_output_has_a_due_section(capsys):
    from empirica.cli.command_handlers.project_bootstrap_formatter import _format_due_section

    _format_due_section({"due": DUE})
    out = capsys.readouterr().out
    assert "Due" in out and "ship the thing" in out and "3 more" in out
    _format_due_section({"due": []})
    assert capsys.readouterr().out == ""


def test_goals_list_shows_the_due_date(tmp_path, monkeypatch):
    """Bootstrap listed due items but goals-list did not carry the date at all."""
    from tests.test_goals_accept_what_they_store import _run_listing

    db_path = build_db(tmp_path / "list.db", "current")
    c = sqlite3.connect(db_path)
    c.execute(
        "INSERT INTO goals (id, session_id, objective, scope, status, created_timestamp, is_completed, project_id, "
        "goal_data, due_at) VALUES ('g-due', 's', 'dated goal', '{}', 'planned', 0, 0, 'p', '{}', '2026-12-01')"
    )
    c.commit()
    c.close()
    result = _run_listing(db_path, monkeypatch, status="planned", all_projects=True)
    assert isinstance(result, dict)
    goals = result["goals"]
    assert isinstance(goals, list)
    assert [(g["goal_id"], g["due_at"]) for g in goals] == [("g-due", "2026-12-01")]


def test_the_daemon_patch_stores_the_date_an_offset_names(tmp_path, monkeypatch):
    """PATCH /api/v1/artifacts/<id> took due_at through the field map but skipped the normalising step, so '+7d' was
    stored as typed and, sorting before any digit, made the item due forever (1.15.0 sweep, D1)."""
    import sqlite3 as _sqlite3
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    import empirica.api.daemon_project as dp
    from empirica.api.serve_app import create_serve_app
    from tests.test_data_layer_sweep_fixes import _api_project

    dp._cached, dp._cached_project = False, None
    pid = "33333333-3333-3333-3333-333333333333"
    proj, db_path = _api_project(tmp_path, pid)
    c = _sqlite3.connect(db_path)
    c.execute(
        "CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, unknown TEXT, "
        "is_resolved INTEGER DEFAULT 0, created_timestamp REAL, unknown_data TEXT, subject TEXT, impact REAL, "
        "epistemic_source TEXT, visibility TEXT, due_at TEXT)"
    )
    c.execute("INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp) VALUES "
              "('44444444-4444-4444-4444-444444444444', ?, 's', 'u', 1.0)", (pid,))  # fmt: skip
    c.commit()
    c.close()
    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app(), raise_server_exceptions=False)
        resp = client.patch("/api/v1/artifacts/44444444-4444-4444-4444-444444444444", json={"due_at": "+7d"})
    dp._cached, dp._cached_project = False, None
    assert resp.status_code == 200, resp.text
    stored = _sqlite3.connect(db_path).execute("SELECT due_at FROM project_unknowns").fetchone()[0]
    assert stored == parse_due("+7d")


def test_a_date_is_not_stamped_on_a_closed_row_and_an_existing_date_is_reported(conn):
    from empirica.data.due import due_fields, stamp_due

    _unknown(conn, "u-closed", resolved=1)
    assert "closed unknown" in stamp_due(conn, "unknown", "u-closed", "2026-12-01")["due_note"]
    assert conn.execute("SELECT due_at FROM project_unknowns WHERE id='u-closed'").fetchone()[0] is None
    _unknown(conn, "u-dated", due="2026-12-05")
    stamped = stamp_due(conn, "unknown", "u-dated", None)  # logged again without --due
    assert stamped == {"due_at": "2026-12-05"} and "due_hint" not in due_fields("unknown", stamped, hint=True)
