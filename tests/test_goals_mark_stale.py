"""`goals-mark-stale` must actually mark, and must fail loudly when it cannot.

David hit the argparse error (`--session-id` was `required=True`) and asked whether
session-scoped goals could resolve it at all. They can — every other verb falls back
to `R.session_id()`; this one never did, because its documented caller is the
pre-compact hook, which always knows its own session id.

Fixing that exposed the real defect underneath. Goals created through the normal path
serialise `"metadata": null`, so the key EXISTS and is None. The guard tested
membership (`"metadata" not in goal_data`), skipped initialisation, and the next line
raised on None — for 1277 of this practice's 1277-odd goals. The exception was caught
and turned into `return 0`, so the CLI printed `ok: true, goals_marked_stale: 0`
while a traceback scrolled past on stderr. The verb had never worked outside a
fixture, and said so to nobody.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

import pytest

from empirica.core.goals.repository import GoalRepository

SESSION = str(uuid.uuid4())


@pytest.fixture
def repo(tmp_path):
    r = GoalRepository(db_path=str(tmp_path / "s.db"))
    yield r
    r.close()


def _insert(repo, goal_data: dict) -> str:
    gid = str(uuid.uuid4())
    repo.db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, is_completed, goal_data, status) "
        "VALUES (?, ?, 'obj', '{}', ?, 0, ?, 'in_progress')",
        (gid, SESSION, time.time(), json.dumps(goal_data)),
    )
    repo.db.conn.commit()
    return gid


def _metadata(repo, gid: str) -> dict:
    row = repo.db.conn.execute("SELECT goal_data FROM goals WHERE id = ?", (gid,)).fetchone()
    return json.loads(row[0]).get("metadata") or {}


def test_a_goal_with_null_metadata_is_marked(repo):
    """POSITIVE CONTROL — the shape every real goal has."""
    gid = _insert(repo, {"objective": "obj", "metadata": None})

    count = repo.mark_goals_stale(SESSION, stale_reason="memory_compact")

    assert count == 1, "the null-metadata shape raised and was swallowed as a zero count"
    assert _metadata(repo, gid)["stale_reason"] == "memory_compact"
    assert "stale_since" in _metadata(repo, gid)


def test_a_goal_with_absent_metadata_is_marked(repo):
    """NEGATIVE CONTROL — the shape the old guard DID handle must keep working."""
    gid = _insert(repo, {"objective": "obj"})

    assert repo.mark_goals_stale(SESSION) == 1
    assert "stale_since" in _metadata(repo, gid)


def test_existing_metadata_is_preserved_not_replaced(repo):
    """Marking stale must not clobber whatever else lives under metadata."""
    gid = _insert(repo, {"objective": "obj", "metadata": {"owner": "david"}})

    repo.mark_goals_stale(SESSION)

    md = _metadata(repo, gid)
    assert md["owner"] == "david"
    assert "stale_since" in md


def test_a_real_failure_raises_rather_than_reporting_zero(repo):
    """The swallow is what made the bug invisible for so long: a crash and 'nothing
    to do' produced identical output. Dropping the table is a stand-in for any
    genuine failure."""
    _insert(repo, {"objective": "obj", "metadata": None})
    repo.db.conn.execute("DROP TABLE goals")
    repo.db.conn.commit()

    with pytest.raises(sqlite3.Error):
        repo.mark_goals_stale(SESSION)


def test_no_goals_still_returns_zero_quietly(repo):
    """A genuine no-op must stay a quiet zero — otherwise the loud-failure change
    would turn every empty session into an error."""
    assert repo.mark_goals_stale(str(uuid.uuid4())) == 0
