"""Finishing the work is not telling the peer.

``_maybe_add_deferred_proposals_note`` existed to stop the half-handshake: work
a peer asked for, done, never acked, so the source AI's outbox shows it stalled
forever. It queried ``WHERE is_completed = 0``.

That is inverted. The ack becomes due *when the work finishes*, so a reminder
scoped to open goals goes silent at exactly the moment it is needed — closing
the goal is what removes it from the query.

Measured 2026-08-07: autonomy's Claude-5 trim directive shipped across eight
commits and two merged PRs, its goals were closed, and the proposal sat at
``completed=null`` for four days. David asked why it was not resolved. It was
resolved; it was never said, and the mechanism built to catch that could not
have caught it.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from empirica.cli.command_handlers._workflow_shared import _maybe_add_deferred_proposals_note

SESSION = "sess-1"
PROJECT = "proj-1"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_id TEXT);
        CREATE TABLE goals (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            objective TEXT,
            is_completed BOOLEAN DEFAULT 0,
            completed_timestamp REAL,
            created_timestamp REAL
        );
        """
    )
    conn.execute("INSERT INTO sessions VALUES (?, ?)", (SESSION, PROJECT))
    return conn


def _goal(conn, gid, objective, *, completed=False, completed_ago_s=0):
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, is_completed, completed_timestamp, created_timestamp)"
        " VALUES (?,?,?,?,?,?)",
        (
            gid,
            SESSION,
            objective,
            1 if completed else 0,
            (time.time() - completed_ago_s) if completed else None,
            time.time() - 86400,
        ),
    )


def _run(conn) -> dict:
    retro: dict = {}
    _maybe_add_deferred_proposals_note(conn.cursor(), SESSION, retro)
    return retro


# ─── The inversion ─────────────────────────────────────────────────────


def test_completed_proposal_goal_still_surfaces_as_ack_owed(db):
    """THE REGRESSION. Closing the goal must not silence the reminder."""
    _goal(db, "g1", "Process proposal prop_abc123: run the Claude-5 trim", completed=True, completed_ago_s=3600)
    retro = _run(db)
    assert retro.get("proposal_acks_owed_count") == 1, "a completed proposal-goal vanished from the retrospective"
    assert "prop_abc123" in retro["deferred_proposals_note"]
    assert "mailbox reply" in retro["deferred_proposals_note"], "the note must name the verb that closes the loop"


def test_ack_note_states_that_it_cannot_verify_the_ack(db):
    """It reads local goal state and cannot see cortex's proposal table, so an
    already-acked goal appears here too. Verified against real data on
    2026-08-07: both flagged goals were in fact already acked.

    The note must therefore prompt rather than accuse. An accusatory note that
    is wrong two times in two is exactly how a reminder gets trained away — and
    the open-goal half, which IS precise, would be skipped with it.
    """
    _goal(db, "g1", "Process proposal prop_abc123: ship it", completed=True, completed_ago_s=3600)
    note = _run(db)["deferred_proposals_note"]
    assert "CONFIRM" in note, "must ask for confirmation"
    assert "cannot see" in note, "must disclose that it has no view of the ack state"


def test_open_and_completed_are_reported_separately(db):
    """They are different obligations: one is do the work, one is say so."""
    _goal(db, "g1", "Process proposal prop_open1: investigate X")
    _goal(db, "g2", "Process proposal prop_done1: ship Y", completed=True, completed_ago_s=60)
    retro = _run(db)
    assert retro["deferred_proposals_count"] == 1
    assert retro["proposal_acks_owed_count"] == 1
    note = retro["deferred_proposals_note"]
    assert "prop_open1" in note and "prop_done1" in note


def test_open_goals_still_surface_as_before(db):
    """The original behaviour is preserved, not replaced."""
    _goal(db, "g1", "Process proposal prop_abc123: do the thing")
    retro = _run(db)
    assert retro["deferred_proposals_count"] == 1
    assert "half-handshake" in retro["deferred_proposals_note"]


# ─── Bounded, so the note stays worth reading ──────────────────────────


def test_long_completed_goals_stop_nagging(db):
    """An ack for something closed weeks ago is sent or moot. Permanent
    nagging trains the reader to skip the note, which costs the open ones too."""
    _goal(db, "g1", "Process proposal prop_old: ancient", completed=True, completed_ago_s=30 * 86400)
    assert _run(db) == {}


def test_ack_window_boundary_is_seven_days(db):
    _goal(db, "g1", "Process proposal prop_in: just inside", completed=True, completed_ago_s=6 * 86400)
    _goal(db, "g2", "Process proposal prop_out: just outside", completed=True, completed_ago_s=8 * 86400)
    retro = _run(db)
    assert retro["proposal_acks_owed_count"] == 1
    assert "prop_in" in retro["deferred_proposals_note"]
    assert "prop_out" not in retro["deferred_proposals_note"]


# ─── Precision preserved ───────────────────────────────────────────────


def test_non_convention_goals_are_not_matched(db):
    """The prefix match was tightened in 2026-05 after 16 false positives from
    a loose '%prop_%'. Widening the completion window must not re-loosen it."""
    _goal(db, "g1", "Refactor the PROPOSAL_INTAKE.md doc", completed=True, completed_ago_s=60)
    _goal(db, "g2", "Investigate prop_xyz mentioned in passing", completed=True, completed_ago_s=60)
    assert _run(db) == {}


def test_other_projects_are_not_surfaced(db):
    _goal(db, "g1", "Process proposal prop_mine: ours", completed=True, completed_ago_s=60)
    db.execute("INSERT INTO sessions VALUES (?, ?)", ("sess-2", "proj-2"))
    db.execute(
        "INSERT INTO goals (id, session_id, objective, is_completed, completed_timestamp, created_timestamp)"
        " VALUES ('g2','sess-2','Process proposal prop_theirs: not ours',1,?,?)",
        (time.time() - 60, time.time()),
    )
    retro = _run(db)
    assert retro["proposal_acks_owed_count"] == 1
    assert "prop_theirs" not in retro["deferred_proposals_note"]


def test_nothing_to_report_leaves_retro_untouched(db):
    assert _run(db) == {}
