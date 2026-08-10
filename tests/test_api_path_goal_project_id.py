"""API-path goals must inherit the session's project_id at birth.

Two writers insert into `goals`. The CLI path
(`core.goals.repository.GoalRepository.save_goal`) resolves project_id from the
session row and stamps it. The data-layer path
(`data.repositories.goals.GoalDataRepository.create_goal`, reached via
`SessionDatabase.create_goal` — the documented API/MCP surface) inserted with NO
project_id column at all, so every goal born there had project_id NULL and never
surfaced in any project-scoped view again. Cortex found five of its own this
way, one in_progress since March (prop_msp5i3tl3vhstpylsn6xom6fne).

The fix mirrors the CLI path's resolution. NULL stays honest: a session that is
itself unbound produces a NULL-project goal, because inventing a binding would
be the opposite defect.

The root sat one level deeper than the goal recorded: `create_session` accepted
project_id and silently dropped it for the LOCAL row (only the global registry
got it) — the CLI's separate link_session_to_project step was the only writer
of sessions.project_id. So the resolver alone would have been inert on the API
path: the session row it reads was itself NULL. Both halves are fixed and both
are pinned here.

These tests build their own database under tmp_path and assert on the row the
system wrote — not on a fixture shaped like the belief being tested.
"""

from __future__ import annotations

import pytest

from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path):
    database = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    yield database
    database.close()


def _stored_project_id(db: SessionDatabase, goal_id: str):
    row = db.conn.execute("SELECT project_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    assert row is not None, "goal row must exist"
    return row[0]


def test_create_session_binds_the_local_row(db):
    """The dropped-parameter half: project_id passed to create_session must
    land on the local sessions row, not only the global registry."""
    session_id = db.create_session(ai_id="test-ai", project_id="proj-1234")

    row = db.conn.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    assert row[0] == "proj-1234"


def test_goal_created_under_bound_session_inherits_project_id(db):
    session_id = db.create_session(ai_id="test-ai", project_id="proj-1234")
    goal_id = db.create_goal(session_id, "API-path goal under a bound session")

    assert _stored_project_id(db, goal_id) == "proj-1234"


def test_goal_inherits_project_id_bound_via_link_step(db):
    """The CLI arrangement: session created unbound, then linked. The resolver
    must read the row as it is NOW, not as it was at session creation."""
    session_id = db.create_session(ai_id="test-ai")
    db.conn.execute("UPDATE sessions SET project_id = ? WHERE session_id = ?", ("proj-linked", session_id))
    goal_id = db.create_goal(session_id, "goal after link_session_to_project")

    assert _stored_project_id(db, goal_id) == "proj-linked"


def test_goal_created_under_unbound_session_stays_null(db):
    """A session with no project binding must NOT get a project_id invented."""
    session_id = db.create_session(ai_id="test-ai")
    goal_id = db.create_goal(session_id, "API-path goal under an unbound session")

    assert _stored_project_id(db, goal_id) is None


def test_goal_created_under_unknown_session_stays_null(db):
    """No session row at all → NULL, not an error and not a fabricated id."""
    goal_id = db.create_goal("00000000-0000-0000-0000-000000000000", "orphan-session goal")

    assert _stored_project_id(db, goal_id) is None


def test_bound_goal_surfaces_in_project_scoped_query(db):
    """The defect's symptom, asserted directly: the goal must be visible when
    filtering by project_id — invisibility in scoped views is what made five
    NULL goals silent for months."""
    session_id = db.create_session(ai_id="test-ai", project_id="proj-5678")
    goal_id = db.create_goal(session_id, "must surface in project-scoped views")

    rows = db.conn.execute("SELECT id FROM goals WHERE project_id = ?", ("proj-5678",)).fetchall()
    assert goal_id in {r[0] for r in rows}
