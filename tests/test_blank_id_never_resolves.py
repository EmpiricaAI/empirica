"""A blank id must never resolve to a row.

Every id-resolving path in the goals stack does prefix matching by interpolating
the caller's id into a LIKE pattern: ``f"{goal_id}%"``. When the id is the empty
string that pattern becomes ``LIKE '%'``, which matches **every** row. The
resolvers then take the first or most-recent match and write to it.

Cortex reported this as a harmless no-op dressed as success (prop_fm4ultb5): they
piped an empty lookup into ``goals-reopen``, saw ``ok:true``, checked their
intended goal, found it still completed, and concluded nothing had happened. That
conclusion is wrong, and so was my own earlier note saying ``goals-complete-task``
with an empty id "completed nothing". Neither of us checked whether a *different*
row had moved. It had — the write landed on an arbitrary row and reported success.

So the hypothetical in cortex's report ("the identical blind call against a
destructive verb would apply it to the wrong scope and still report success") is
not hypothetical. It is what both verbs already did.

Each test below is a positive control for the wrong-target write, paired with a
negative control proving a genuine prefix still resolves — otherwise the guard
could pass by refusing everything.
"""

from __future__ import annotations

import sqlite3
import time
import uuid

from empirica.data.repositories.goals import GoalDataRepository

SESSION = str(uuid.uuid4())


def _goals_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE goals (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            objective TEXT NOT NULL,
            description TEXT,
            scope TEXT NOT NULL DEFAULT '{}',
            estimated_complexity REAL,
            created_timestamp REAL NOT NULL,
            completed_timestamp REAL,
            is_completed BOOLEAN DEFAULT 0,
            goal_data TEXT NOT NULL DEFAULT '{}',
            status TEXT DEFAULT 'in_progress',
            beads_issue_id TEXT,
            project_id TEXT,
            transaction_id TEXT,
            archived BOOLEAN DEFAULT 0,
            archived_at REAL
        )
        """
    )
    conn.commit()
    return conn


def _insert_goal(conn, *, status: str, is_completed: int) -> str:
    gid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, "
        "completed_timestamp, is_completed, goal_data, status) "
        "VALUES (?, ?, ?, '{}', ?, ?, ?, '{}', ?)",
        (gid, SESSION, "bystander goal", time.time(), time.time(), is_completed, status),
    )
    conn.commit()
    return gid


def _statuses(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT status FROM goals ORDER BY id")]


# ── reopen_goal ───────────────────────────────────────────────────────


def test_blank_goal_id_does_not_reopen_a_bystander():
    """POSITIVE CONTROL — the wrong-target write cortex mistook for a no-op."""
    conn = _goals_conn()
    for _ in range(3):
        _insert_goal(conn, status="completed", is_completed=1)
    repo = GoalDataRepository(conn)

    assert repo.reopen_goal("") is False, "a blank id resolves to nothing and must be refused"
    assert _statuses(conn) == ["completed"] * 3, "no goal may change status on a blank id"


def test_whitespace_goal_id_is_treated_as_blank():
    """A lookup that returns a stray newline is the same failure with a disguise."""
    conn = _goals_conn()
    _insert_goal(conn, status="completed", is_completed=1)
    repo = GoalDataRepository(conn)

    assert repo.reopen_goal("  \n ") is False
    assert _statuses(conn) == ["completed"]


def test_negative_control_a_real_prefix_still_reopens():
    """The guard must refuse blanks, not refuse everything."""
    conn = _goals_conn()
    gid = _insert_goal(conn, status="completed", is_completed=1)
    repo = GoalDataRepository(conn)

    assert repo.reopen_goal(gid[:8]) is True
    assert _statuses(conn) == ["in_progress"]


# ── activate_goal (same LIKE shape, planned goals) ────────────────────


def test_blank_goal_id_does_not_activate_a_bystander():
    conn = _goals_conn()
    for _ in range(3):
        _insert_goal(conn, status="planned", is_completed=0)
    repo = GoalDataRepository(conn)

    assert repo.activate_goal("") is False
    assert _statuses(conn) == ["planned"] * 3


def test_negative_control_a_real_prefix_still_activates():
    conn = _goals_conn()
    gid = _insert_goal(conn, status="planned", is_completed=0)
    repo = GoalDataRepository(conn)

    assert repo.activate_goal(gid[:8]) is True
    assert _statuses(conn) == ["in_progress"]


# ── archive_stale_completed ───────────────────────────────────────────


def test_blank_goal_id_is_not_read_as_archive_everything():
    """``None`` legitimately means "no filter"; ``""`` means a lookup came back
    empty. Collapsing the two turns a failed lookup into a fleet-wide archive."""
    conn = _goals_conn()
    old = time.time() - 400 * 86400
    for _ in range(3):
        gid = _insert_goal(conn, status="completed", is_completed=1)
        conn.execute("UPDATE goals SET completed_timestamp = ? WHERE id = ?", (old, gid))
    conn.commit()
    repo = GoalDataRepository(conn)

    result = repo.archive_stale_completed(older_than_days=30, apply=True, goal_id="")

    archived = conn.execute("SELECT COUNT(*) FROM goals WHERE archived = 1").fetchone()[0]
    assert archived == 0, f"blank id archived {archived} goals: {result}"


# ── subtask resolution ────────────────────────────────────────────────


def _subtasks_conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subtasks (
            id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            epistemic_importance TEXT NOT NULL DEFAULT 'medium',
            estimated_tokens INTEGER,
            actual_tokens INTEGER,
            completion_evidence TEXT,
            notes TEXT,
            created_timestamp REAL NOT NULL,
            completed_timestamp REAL,
            subtask_data TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.commit()
    return conn


def _task_repo(tmp_path):
    """A repository over a throwaway file DB — TaskRepository owns its own
    SessionDatabase, so hand it a path rather than a connection."""
    from empirica.core.tasks.repository import TaskRepository

    return TaskRepository(db_path=str(tmp_path / "s.db"))


def _insert_subtask(conn, *, created: float) -> str:
    sid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO subtasks (id, goal_id, description, status, created_timestamp, subtask_data) "
        "VALUES (?, 'g1', 'task', 'pending', ?, '{}')",
        (sid, created),
    )
    conn.commit()
    return sid


def test_blank_task_id_does_not_complete_the_most_recent_task(tmp_path):
    """POSITIVE CONTROL — ``_resolve_subtask_id`` logged "using most recent" and
    returned a real id for a blank input, so the caller's evidence landed on
    whichever task happened to be newest."""
    conn = _subtasks_conn(tmp_path)
    _insert_subtask(conn, created=1.0)
    _insert_subtask(conn, created=2.0)
    repo = _task_repo(tmp_path)

    assert repo._resolve_subtask_id("") is None

    from empirica.core.tasks.types import TaskStatus

    assert repo.update_subtask_status("", TaskStatus.COMPLETED, "evidence for nothing") is False
    completed = conn.execute("SELECT COUNT(*) FROM subtasks WHERE status = 'completed'").fetchone()[0]
    assert completed == 0


def test_negative_control_a_real_task_prefix_still_resolves(tmp_path):
    conn = _subtasks_conn(tmp_path)
    sid = _insert_subtask(conn, created=1.0)
    repo = _task_repo(tmp_path)

    assert repo._resolve_subtask_id(sid[:8]) == sid
