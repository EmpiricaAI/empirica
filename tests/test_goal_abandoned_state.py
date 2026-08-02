"""A goal can be dead without having been delivered.

The lifecycle was planned | in_progress | completed. There was no terminal state
for work that stopped mattering, so closing an abandoned goal meant writing
`completed` — which is false, and which every `status='completed'` query counts
as delivered work, **grounded calibration included**. The measurement layer was
being fed inflated completion by a vocabulary gap.

Measured on this practice 2026-08-02: 65 open goals reachable by the bootstrap
situation path, 22 of them 30+ days untouched with no way out of the pool.

`mark_goals_stale` does not help despite its name — its own docstring says
"status unchanged" and "the stale status was removed". It annotates compaction
metadata. So the fix is a real terminal state, not a flag on that verb.

**`is_completed` stays 0 for abandoned, deliberately.** Abandoned is not done.
That choice is what makes the injection paths interesting: two of the three use
`status` allowlists and exclude it for free, while `bootstrap/topic` filtered on
`is_completed = 0` alone and therefore injected abandoned goals as current work.
"""

from __future__ import annotations

import sqlite3

import pytest

TOPIC_SQL_NEW = (
    "SELECT objective FROM goals WHERE transaction_id = ? AND is_completed = 0 "
    "AND (status IS NULL OR status != 'abandoned') ORDER BY created_timestamp DESC LIMIT 1"
)
TOPIC_SQL_OLD = (
    "SELECT objective FROM goals WHERE transaction_id = ? AND is_completed = 0 ORDER BY created_timestamp DESC LIMIT 1"
)


@pytest.fixture
def repo(tmp_path):
    from empirica.data.repositories.goals import GoalDataRepository

    conn = sqlite3.connect(tmp_path / "s.db")
    conn.execute(
        "CREATE TABLE goals (id TEXT PRIMARY KEY, session_id TEXT, project_id TEXT, transaction_id TEXT, "
        "objective TEXT, status TEXT, is_completed INTEGER DEFAULT 0, goal_data TEXT, created_timestamp REAL)"
    )
    conn.commit()
    r = GoalDataRepository.__new__(GoalDataRepository)
    r.conn = conn
    r._execute = lambda q, p=(): conn.execute(q, p)  # type: ignore[method-assign]
    r.commit = conn.commit  # type: ignore[method-assign]
    return r


def _add(repo, gid, status="in_progress", completed=0, tx="tx-1", ts=1.0, obj="o"):
    repo.conn.execute(
        "INSERT INTO goals (id, transaction_id, objective, status, is_completed, created_timestamp) "
        "VALUES (?,?,?,?,?,?)",
        (gid, tx, obj, status, completed, ts),
    )
    repo.conn.commit()
    return gid


ID_A = "aaaaaaaa-0000-4000-8000-000000000001"


def test_abandoning_sets_the_terminal_state(repo):
    """POSITIVE CONTROL."""
    _add(repo, ID_A)

    assert repo.abandon_goal(ID_A, "superseded by the 1.13 release") is True

    row = repo.conn.execute("SELECT status, is_completed FROM goals WHERE id=?", (ID_A,)).fetchone()
    assert row[0] == "abandoned"
    assert row[1] == 0, "abandoned must NOT count as completed — that is the whole point"


def test_the_reason_is_recorded(repo):
    """A terminal state with no reason is just a delete with extra steps."""
    _add(repo, ID_A)
    repo.abandon_goal(ID_A, "proposal no longer exists cortex-side")

    import json

    data = json.loads(repo.conn.execute("SELECT goal_data FROM goals WHERE id=?", (ID_A,)).fetchone()[0])
    assert data["abandoned_reason"] == "proposal no longer exists cortex-side"
    assert data["abandoned_at"] > 0


def test_an_already_completed_goal_is_not_relabelled(repo):
    """NEGATIVE CONTROL: delivered work must not be quietly reclassified as
    abandoned, and the caller has to be told it did not happen."""
    _add(repo, ID_A, status="completed", completed=1)

    assert repo.abandon_goal(ID_A, "nope") is False
    assert repo.conn.execute("SELECT status FROM goals WHERE id=?", (ID_A,)).fetchone()[0] == "completed"


def test_a_bogus_id_reports_failure(repo):
    """The silent-success class this release spent itself on."""
    _add(repo, ID_A)

    assert repo.abandon_goal("deadbeef", "x") is False
    assert repo.conn.execute("SELECT status FROM goals WHERE id=?", (ID_A,)).fetchone()[0] == "in_progress"


def test_an_ambiguous_prefix_is_refused(repo):
    """Abandoning the wrong goal would remove real work from injected context."""
    _add(repo, "abcdef12-0000-4000-8000-000000000001")
    _add(repo, "abcdef12-0000-4000-8000-000000000002")

    assert repo.abandon_goal("abcdef12", "ambiguous") is False


# ── the injection path this exists to protect ─────────────────────────


def test_the_topic_path_no_longer_injects_abandoned_goals(repo):
    """THE REGRESSION THIS PREVENTS.

    `bootstrap/topic` filtered on `is_completed = 0` alone. Because abandoned
    deliberately keeps is_completed=0, that query surfaced dead goals as the
    practice's current work. Reproduced against the real SQL text.
    """
    _add(repo, ID_A, status="in_progress", ts=1.0, obj="the live goal")
    dead = "bbbbbbbb-0000-4000-8000-000000000002"
    _add(repo, dead, status="in_progress", ts=2.0, obj="the dead goal")
    repo.abandon_goal(dead, "dead")

    old = repo.conn.execute(TOPIC_SQL_OLD, ("tx-1",)).fetchone()
    new = repo.conn.execute(TOPIC_SQL_NEW, ("tx-1",)).fetchone()

    assert old[0] == "the dead goal", "the old filter injected the abandoned goal"
    assert new[0] == "the live goal", "the fixed filter must skip it and surface real work"


def test_the_topic_path_still_injects_normal_open_goals(repo):
    """NEGATIVE CONTROL: excluding abandoned must not exclude everything."""
    _add(repo, ID_A, status="in_progress", obj="still open")

    assert repo.conn.execute(TOPIC_SQL_NEW, ("tx-1",)).fetchone()[0] == "still open"


def test_a_null_status_goal_still_injects(repo):
    """Older rows carry NULL status; `!= 'abandoned'` alone would drop them,
    since NULL comparisons are never true in SQL."""
    _add(repo, ID_A, status=None, obj="legacy row")

    assert repo.conn.execute(TOPIC_SQL_NEW, ("tx-1",)).fetchone()[0] == "legacy row"
