"""goals-list's default view is the live work, and an abandoned goal is not live.

`goals-list` filtered the default view on `is_completed = 0` alone. Abandoned keeps `is_completed = 0` on purpose (abandoned is not done, see
test_goal_abandoned_state.py), so every abandoned goal came back as active: 87 listed against 68 live rows on this practice, the gap being exactly the
19 abandoned. The injection paths already excluded the status; the list verb was the path that did not.
"""

from __future__ import annotations

import sqlite3
import time
import types

import pytest

import empirica.cli.command_handlers.goal_commands as gc

_DB_TARGET = "empirica.data.session_database.SessionDatabase"


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn

    def close(self):
        pass


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE goals (id TEXT PRIMARY KEY, objective TEXT, description TEXT, status TEXT, "
        "is_completed INT, created_timestamp REAL, session_id TEXT, project_id TEXT, "
        "transaction_id TEXT, archived INT DEFAULT 0)"
    )
    c.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_id TEXT, ai_id TEXT)")
    c.execute("CREATE TABLE subtasks (goal_id TEXT, status TEXT)")
    c.execute("INSERT INTO sessions VALUES ('s1','projA','ai')")
    now = time.time()
    rows = [
        ("g-prog", "in_progress", 0),
        ("g-plan", "planned", 0),
        ("g-block", "blocked", 0),
        ("g-done", "completed", 1),
        ("g-dead", "abandoned", 0),
    ]
    for gid, status, done in rows:
        c.execute(
            "INSERT INTO goals VALUES (?,?,?,?,?,?,'s1','projA',NULL,0)",
            (gid, f"goal {gid}", "body", status, done, now),
        )
    c.commit()
    return c


def _list(monkeypatch, conn, **kw):
    monkeypatch.setattr(_DB_TARGET, lambda: _FakeDB(conn))
    args = {"output": "json", "project_id": "projA", "session_id": None, "limit": 20}
    args.update(kw)
    res = gc.handle_goals_list_command(types.SimpleNamespace(**args))
    assert isinstance(res, dict), res
    goals = res["goals"]
    assert isinstance(goals, list)
    return {g["goal_id"] if "goal_id" in g else g["id"] for g in goals}


def test_the_default_view_leaves_out_abandoned_goals(monkeypatch, conn):
    assert _list(monkeypatch, conn) == {"g-prog", "g-plan", "g-block"}


def test_abandoned_goals_are_listed_when_asked_for_by_name(monkeypatch, conn):
    """Positive control for the refusal above: the rows exist and the filter can reach them."""
    assert _list(monkeypatch, conn, status="abandoned") == {"g-dead"}


def test_all_still_shows_every_status(monkeypatch, conn):
    assert _list(monkeypatch, conn, status="all") == {"g-prog", "g-plan", "g-block", "g-done", "g-dead"}


def test_the_status_flag_offers_abandoned():
    import argparse

    from empirica.cli.parsers.checkpoint_parsers import add_checkpoint_parsers

    parser = argparse.ArgumentParser()
    add_checkpoint_parsers(parser.add_subparsers(dest="command"))
    assert parser.parse_args(["goals-list", "--status", "abandoned"]).status == "abandoned"
