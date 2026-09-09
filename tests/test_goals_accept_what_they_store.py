"""A goals flag that is accepted must be stored, and a filter must filter.

Two defects reported by empirica-workspace within minutes of each other, both in
the same verb family and both the same shape: **the CLI accepted an input and
then behaved as though it had not been given**, with nothing in the output to
contradict the assumption that it landed.

1. `goals-complete --reason` was read into `close_reason` and passed to exactly
   one consumer — `_gc_close_beads`, guarded by `if beads_issue_id`. A goal not
   linked to BEADS dropped it. An **advertised no-op**: accepted, documented,
   discarded, no error. Confirmed here against two closures written the same
   hour, both gone. What is lost is the only thing distinguishing *achieved*
   from *abandoned* from *superseded* on a closed goal.

2. `goals-list --status blocked` returned the whole open backlog. The filter
   enumerated `("in_progress", "planned")` and everything else fell through to
   the not-completed default — so a question about blocked goals was answered,
   confidently, with every goal. `abandoned` (9 real rows on this practice) was
   mis-filed identically, so fixing only the reported value would have left a
   live one broken.

Both survived for the same reason: **no moment exists where the wrong behaviour
looks wrong.** A closure reason is written and never read back in the same
session. A filter returning too much looks like a full backlog, not a broken
filter.
"""

from __future__ import annotations

import sqlite3
from argparse import Namespace

import pytest

from empirica.cli.command_handlers.goal_commands import _build_goals_status_filter
from empirica.data.migrations.migrations import migration_068_goal_completion_reason

_SCHEMA = """
CREATE TABLE goals (
    id TEXT PRIMARY KEY, session_id TEXT, objective TEXT, scope TEXT,
    estimated_complexity TEXT, created_timestamp REAL, completed_timestamp REAL,
    is_completed BOOLEAN DEFAULT 0, goal_data TEXT, status TEXT DEFAULT 'in_progress',
    beads_issue_id TEXT, project_id TEXT, transaction_id TEXT, description TEXT,
    archived INTEGER DEFAULT 0
);
"""


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    migration_068_goal_completion_reason(conn.cursor())
    conn.commit()
    conn.close()
    return path


# ─── 1. the reason must be STORED, not merely accepted ─────────────────


def test_migration_gives_goals_somewhere_to_put_a_reason(db):
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(goals)")}
    conn.close()
    assert "completion_reason" in cols


def test_reason_survives_the_write(db):
    """The assertion is on the stored value, never on the exit code.

    A verb that returns 0 while discarding its input is exactly the bug; a test
    asserting `rc == 0` would have passed throughout.
    """
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO goals (id, objective, goal_data) VALUES ('g1', 'do a thing', '{}')")
    conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=1.0, "
        "completion_reason=? WHERE id='g1'",
        ("superseded by the postgres pivot, not achieved",),
    )
    conn.commit()
    stored = conn.execute("SELECT completion_reason FROM goals WHERE id='g1'").fetchone()[0]
    conn.close()
    assert stored == "superseded by the postgres pivot, not achieved"


def test_mark_completed_reports_whether_it_stored_rather_than_asserting_it():
    """`_gc_mark_completed` returns a bool the caller echoes.

    Reporting `reason_stored` from the flag instead of from the write would
    announce success with equal confidence whether or not anything landed —
    reproducing the defect inside its own fix.
    """
    import inspect

    from empirica.cli.command_handlers import goal_commands

    src = inspect.getsource(goal_commands._gc_mark_completed)
    assert "close_reason" in src, "_gc_mark_completed never receives the reason"
    assert "completion_reason" in src, "_gc_mark_completed does not write the column"
    assert "return reason_stored" in src, "no signal back to the caller about what was written"

    handler = inspect.getsource(goal_commands.handle_goals_complete_command)
    assert "reason_stored = _gc_mark_completed(goal_id, close_reason)" in handler
    assert '"reason_stored": reason_stored' in handler, (
        "output must echo what the WRITE returned, not what the flag carried"
    )


def test_pre_migration_db_says_the_reason_was_not_saved():
    """A drop that announces itself is recoverable. This one never did."""
    import inspect

    from empirica.cli.command_handlers import goal_commands

    handler = inspect.getsource(goal_commands.handle_goals_complete_command)
    assert "reason_not_stored" in handler, (
        "on a DB without the column the reason is silently dropped again, which is the original bug"
    )


# ─── 2. the status filter must actually filter ─────────────────────────


def test_blocked_filters_instead_of_returning_the_backlog():
    sql, params = _build_goals_status_filter("blocked", False)
    assert "g.status = ?" in sql, "status fell through to the not-completed default"
    assert params == ["blocked"]


def test_abandoned_too_the_value_nobody_reported():
    """9 real rows carry this. Fixing only `blocked` would have left it broken.

    The reported symptom is one instance of the defect, not its extent — the
    enumeration mis-files EVERY value it does not list.
    """
    sql, params = _build_goals_status_filter("abandoned", False)
    assert "g.status = ?" in sql
    assert params == ["abandoned"]


@pytest.mark.parametrize("status", ["blocked", "abandoned", "planned", "in_progress", "on_hold", "custom_thing"])
def test_any_literal_status_filters_literally(status):
    """Allow-any, not an enumeration — so a status added tomorrow works today."""
    _sql, params = _build_goals_status_filter(status, False)
    assert params == [status], f"{status!r} was not applied as a literal filter"


def test_aggregates_keep_their_special_meaning():
    """`all`, `completed`, `drift` are questions about completion state.

    They must NOT become literal status matches — no row holds status='all'.
    """
    assert _build_goals_status_filter("all", False) == ("", [])
    assert _build_goals_status_filter("completed", False) == (" AND g.is_completed = 1", [])
    drift_sql, drift_params = _build_goals_status_filter("drift", False)
    assert drift_params == []
    assert "g.status = ?" not in drift_sql


def test_no_status_still_defaults_to_open_goals():
    """The unfiltered default must not change — every bare `goals-list` uses it."""
    assert _build_goals_status_filter(None, False) == (" AND g.is_completed = 0", [])
    assert _build_goals_status_filter(None, True) == (" AND g.is_completed = 1", [])


def test_empty_result_is_annotated_END_TO_END(db, monkeypatch):
    """Integration, not source order — the version that catches the real bug.

    The first implementation computed this note inside a helper that runs AFTER
    `db.close()`, so it raised on a dead cursor and its own `except` returned
    None. Every unit test still passed, because they call the helper with an
    open connection. Only running the actual command surfaced it.

    A source-order assertion was tried here first and was brittle for its own
    reasons (several `db.close()` calls in one function). Exercise the command.
    """
    import sqlite3 as _sq

    from empirica.cli.command_handlers import goal_commands

    conn = _sq.connect(db)
    conn.execute("INSERT INTO goals (id, objective, goal_data, status) VALUES ('g1','x','{}','planned')")
    conn.execute("INSERT INTO goals (id, objective, goal_data, status) VALUES ('g2','y','{}','abandoned')")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT, ai_id TEXT, project_id TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS subtasks (goal_id TEXT, status TEXT)")
    conn.commit()
    conn.close()

    class _FakeDB:
        def __init__(self):
            self.conn = _sq.connect(db)

        def close(self):
            self.conn.close()

    monkeypatch.setattr(goal_commands, "SessionDatabase", _FakeDB, raising=False)
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _FakeDB, raising=False)

    args = Namespace(
        status="blocked",
        output="json",
        project_id=None,
        session_id=None,
        ai_id=None,
        transaction_id=None,
        limit=50,
        all_projects=False,
        scope=None,
        include_archived=False,
        show_completed=False,
        uncapped=False,
    )
    result = goal_commands.handle_goals_list_command(args)

    assert result["goals_count"] == 0, "blocked must not return the backlog"
    assert result["filters"]["status"] == "blocked"
    assert result.get("note"), (
        "no note on an empty status filter — if the note is computed after "
        "db.close() it raises on a dead cursor and is swallowed into silence"
    )
    assert "planned" in result["note"] and "abandoned" in result["note"]


def test_empty_status_filter_note_names_the_real_statuses(db):
    """Behavioural, not source-shaped: run the explainer against real rows."""
    from empirica.cli.command_handlers.goal_commands import _explain_empty_status_filter

    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO goals (id, objective, goal_data, status) VALUES ('g1', 'x', '{}', 'planned')")
    conn.execute("INSERT INTO goals (id, objective, goal_data, status) VALUES ('g2', 'y', '{}', 'abandoned')")
    conn.commit()
    note = _explain_empty_status_filter(conn.cursor(), [], "blocked")
    conn.close()

    assert note is not None
    assert "blocked" in note
    assert "planned=1" in note and "abandoned=1" in note, (
        "the note must name the statuses that DO exist, or an empty result stays indistinguishable from a broken filter"
    )


def test_annotator_stays_quiet_when_there_are_results(db):
    """The note is for an EMPTY set only — otherwise it is noise on every call."""
    from empirica.cli.command_handlers.goal_commands import _explain_empty_status_filter

    conn = sqlite3.connect(db)
    assert _explain_empty_status_filter(conn.cursor(), [{"goal_id": "g1"}], "blocked") is None
    assert _explain_empty_status_filter(conn.cursor(), [], None) is None
    assert _explain_empty_status_filter(conn.cursor(), [], "all") is None
    conn.close()


def test_reported_filter_is_the_one_applied():
    """The payload announced `active` whatever `--status` carried."""
    import inspect

    from empirica.cli.command_handlers import goal_commands

    src = inspect.getsource(goal_commands.handle_goals_list_command)
    assert 'status_desc = status_filter or ("completed" if show_completed else "active")' in src, (
        "filters.status does not reflect the status that was actually requested"
    )
