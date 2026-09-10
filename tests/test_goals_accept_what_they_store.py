"""A goals flag that is accepted must be stored, and a filter must filter.

Two defects reported by empirica-workspace within minutes of each other, both in
the same verb family and both the same shape: **the CLI accepted an input and
then behaved as though it had not been given**, with nothing in the output to
contradict the assumption that it landed.

1. `goals-complete --reason` was read into `close_reason` and passed to exactly
   one consumer — `_gc_close_beads`, guarded by `if beads_issue_id`. A goal not
   linked to BEADS dropped it. An **advertised no-op**: accepted, documented,
   discarded, no error. Confirmed against two closures written the same hour,
   both gone. What is lost is the only thing distinguishing *achieved* from
   *abandoned* from *superseded* on a closed goal.

2. `goals-list --status blocked` returned the whole open backlog. The filter
   enumerated `("in_progress", "planned")` and everything else fell through to
   the not-completed default. `abandoned` (9 real rows) was mis-filed
   identically, so fixing only the reported value would have left a live one
   broken.

Both survived because no moment exists where the wrong behaviour looks wrong.

FIXTURES: built via ``tests.schema_shapes`` from the REAL schema + the REAL
migration registry — never hand-rolled. The first version of this file
hand-rolled its schema and included ``archived``, a migration-056 column, so its
"unmigrated database" compat test passed against a world that does not exist:
the TRUE base schema broke `goals-list` on `no such column: g.archived`, found
within a minute of building fixtures from production code. A hand-written test
schema is a transcription of the author's assumptions, and it is precisely the
author's assumptions that need testing.
"""

from __future__ import annotations

import sqlite3
from argparse import Namespace

import pytest

from empirica.cli.command_handlers.goal_commands import _build_goals_status_filter
from tests.schema_shapes import build_db


def _insert_goal(conn, goal_id, objective, status="planned"):
    """One goal row satisfying the REAL schema's NOT NULL constraints."""
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status) "
        "VALUES (?, 's1', ?, '{}', 1.0, '{}', ?)",
        (goal_id, objective, status),
    )


@pytest.fixture
def db(tmp_path):
    """A CURRENT-generation database — base schema + the full migration registry."""
    return build_db(tmp_path / "sessions.db", "current")


class _FakeDBFactory:
    """SessionDatabase stand-in bound to a specific file."""

    def __init__(self, path):
        self.path = path

    def make(self):
        path = self.path

        class _FakeDB:
            def __init__(self):
                self.conn = sqlite3.connect(path)

            def close(self):
                self.conn.close()

        return _FakeDB


def _list_args(**kw):
    base = {
        "status": None,
        "output": "json",
        "project_id": None,
        "session_id": None,
        "ai_id": None,
        "transaction_id": None,
        "limit": 50,
        "all_projects": False,
        "scope": None,
        "include_archived": False,
        "show_completed": False,
        "uncapped": False,
    }
    base.update(kw)
    return Namespace(**base)


def _run_listing(dbp, monkeypatch, **kw):
    from empirica.cli.command_handlers import goal_commands

    fake = _FakeDBFactory(dbp).make()
    monkeypatch.setattr(goal_commands, "SessionDatabase", fake, raising=False)
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", fake, raising=False)
    return goal_commands.handle_goals_list_command(_list_args(**kw))


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
    _insert_goal(conn, "g1", "do a thing")
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
    """9 real rows carry this. Fixing only `blocked` would have left it broken."""
    sql, params = _build_goals_status_filter("abandoned", False)
    assert "g.status = ?" in sql
    assert params == ["abandoned"]


@pytest.mark.parametrize("status", ["blocked", "abandoned", "planned", "in_progress", "on_hold", "custom_thing"])
def test_any_literal_status_filters_literally(status):
    """Allow-any, not an enumeration — so a status added tomorrow works today."""
    _sql, params = _build_goals_status_filter(status, False)
    assert params == [status], f"{status!r} was not applied as a literal filter"


def test_aggregates_keep_their_special_meaning():
    """`all`, `completed`, `drift` are questions about completion state."""
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
    """
    conn = sqlite3.connect(db)
    _insert_goal(conn, "g1", "x", "planned")
    _insert_goal(conn, "g2", "y", "abandoned")
    conn.commit()
    conn.close()

    result = _run_listing(db, monkeypatch, status="blocked")

    assert isinstance(result, dict), "listing returned a non-dict (error path)"
    assert result["goals_count"] == 0, "blocked must not return the backlog"
    assert result["filters"]["status"] == "blocked"
    assert result.get("note"), (
        "no note on an empty status filter — if the note is computed after "
        "db.close() it raises on a dead cursor and is swallowed into silence"
    )
    assert "planned=1" in result["note"] and "abandoned=1" in result["note"]


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


# ─── 3. migration-added columns must not break databases without them ──


@pytest.mark.parametrize("generation", ["base", "current"])
def test_listing_works_on_both_schema_generations(tmp_path, monkeypatch, generation):
    """The permanent contract: readers meet BOTH shapes, indefinitely.

    `--all-projects` opens OTHER practices' sessions.db files, which this
    process cannot migrate — they migrate themselves when their own practitioner
    next runs a command. Two migration-added columns each broke this verb
    outright when named unconditionally:

      completion_reason (068) — caught by unrelated tests in the full suite
      archived (056)          — caught within a minute of this fixture being
                                built from the REAL base schema; every earlier
                                hand-rolled fixture had helpfully included the
                                column, so the break was structurally invisible

    Parametrized over generations so the next migration-added column in this
    query fails HERE, at author time, instead of on someone else's database.
    """
    dbp = build_db(tmp_path / f"{generation}.db", generation)
    conn = sqlite3.connect(dbp)
    _insert_goal(conn, "g1", "still listable")
    conn.commit()
    conn.close()

    result = _run_listing(dbp, monkeypatch)

    assert isinstance(result, dict), (
        f"listing returned None on the {generation} generation — the SELECT raised on a missing column"
    )
    assert result["goals_count"] == 1
    assert result["goals"][0]["objective"] == "still listable"
    if generation == "base":
        assert "completion_reason" not in result["goals"][0], (
            "a database without the column must not report the field at all"
        )


def test_empty_status_note_survives_the_base_generation(tmp_path, monkeypatch):
    """The annotator referenced `archived` unconditionally behind its own
    `except`, so on a pre-056 database the note silently vanished — the
    swallowed-detector shape, one layer down from where it was fixed the first
    time. The note must appear on BOTH generations or it is not a detector."""
    dbp = build_db(tmp_path / "base.db", "base")
    conn = sqlite3.connect(dbp)
    _insert_goal(conn, "g1", "x", "planned")
    conn.commit()
    conn.close()

    result = _run_listing(dbp, monkeypatch, status="blocked")
    assert isinstance(result, dict), "listing returned a non-dict (error path)"
    assert result["goals_count"] == 0
    assert result.get("note"), "the empty-status note vanished on the base generation"
    assert "planned=1" in result["note"]
