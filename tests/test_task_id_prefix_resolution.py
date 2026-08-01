"""A task id too short to identify anything is a typo, not a query.

Found by using the tool on itself: `goals-complete-task --task-id 1` printed
"✅ Task marked as complete" and completed a task in a DIFFERENT goal. The
resolver built `LIKE '1%'`, matched hundreds of rows, logged "Multiple subtasks
match - using most recent", and returned that one. The CLI has a correct
task_not_found branch — it never fired, because the repository reported success.

Two separate defects, one symptom:
  1. The documented "8+ chars" minimum was never enforced.
  2. Ambiguity resolved by recency instead of being refused.

Same family as #390 (unknown-resolve) and the blank-id guard in
`empirica/data/id_guard.py`: an operation that acted on the wrong row and
reported success. The blank guard already covered `""`; a one-character prefix
walked straight past it.
"""

from __future__ import annotations

import uuid

import pytest

from empirica.core.tasks.repository import MIN_SUBTASK_ID_PREFIX, TaskRepository
from empirica.core.tasks.types import EpistemicImportance, SubTask, TaskStatus

GOAL = str(uuid.uuid4())


@pytest.fixture
def repo(tmp_path):
    r = TaskRepository(db_path=str(tmp_path / "tasks.db"))
    yield r
    r.db.conn.close()


def _add(repo, task_id: str, description: str = "work") -> str:
    repo.save_subtask(
        SubTask(
            id=task_id,
            goal_id=GOAL,
            description=description,
            status=TaskStatus.PENDING,
            epistemic_importance=EpistemicImportance.MEDIUM,
        )
    )
    return task_id


def test_a_one_character_id_is_refused(repo):
    """POSITIVE CONTROL — the exact reproduction.

    Two tasks both start with '1'. The old resolver returned the newer one.
    """
    _add(repo, "1aaaaaaa-0000-4000-8000-000000000001", "mine")
    _add(repo, "1bbbbbbb-0000-4000-8000-000000000002", "someone else's")

    assert repo._resolve_subtask_id("1") is None


def test_a_short_id_does_not_mutate_anything(repo):
    """The failure that mattered: it did not just misreport, it wrote. Confirm
    no row moved, so a typo cannot complete an unrelated task."""
    tid = _add(repo, "1aaaaaaa-0000-4000-8000-000000000001")
    _add(repo, "1bbbbbbb-0000-4000-8000-000000000002")

    assert repo.update_subtask_status("1", TaskStatus.COMPLETED, "evidence for a different task") is False

    row = repo.db.conn.execute("SELECT status, completion_evidence FROM subtasks WHERE id = ?", (tid,)).fetchone()
    assert row[0] == "pending"
    assert row[1] is None


def test_an_ambiguous_prefix_is_refused_even_when_long_enough(repo):
    """Length alone is not identity. Two tasks sharing a 9-char prefix are
    genuinely ambiguous, and picking the newest is a coin flip reported as a
    result."""
    _add(repo, "abcdef12-0000-4000-8000-000000000001")
    _add(repo, "abcdef12-0000-4000-8000-000000000002")

    assert repo._resolve_subtask_id("abcdef12") is None


def test_a_unique_prefix_at_the_minimum_still_resolves(repo):
    """NEGATIVE CONTROL: refusing everything would pass every test above while
    breaking the prefix ergonomics the CLI depends on."""
    tid = _add(repo, "abcdef12-0000-4000-8000-000000000001")
    _add(repo, "99999999-0000-4000-8000-000000000002")

    assert repo._resolve_subtask_id(tid[:MIN_SUBTASK_ID_PREFIX]) == tid


def test_a_full_uuid_still_resolves(repo):
    """NEGATIVE CONTROL: the common path must be untouched."""
    tid = _add(repo, "abcdef12-0000-4000-8000-000000000001")

    assert repo._resolve_subtask_id(tid) == tid


def test_completing_by_full_uuid_still_works(repo):
    """NEGATIVE CONTROL at the level the CLI actually calls."""
    tid = _add(repo, "abcdef12-0000-4000-8000-000000000001")

    assert repo.update_subtask_status(tid, TaskStatus.COMPLETED, "commit abc1234") is True

    row = repo.db.conn.execute("SELECT status, completion_evidence FROM subtasks WHERE id = ?", (tid,)).fetchone()
    assert row[0] == "completed"
    assert row[1] == "commit abc1234"


def test_a_blank_id_is_still_refused(repo):
    """Regression guard on the existing blank-id fix — the short-prefix check
    must not shadow it, and neither must be removed in favour of the other."""
    _add(repo, "abcdef12-0000-4000-8000-000000000001")

    assert repo._resolve_subtask_id("") is None
    assert repo._resolve_subtask_id("   ") is None


def test_a_prefix_matching_nothing_is_refused(repo):
    """An id that matches no row must fail, not fall through to any row."""
    _add(repo, "abcdef12-0000-4000-8000-000000000001")

    assert repo._resolve_subtask_id("deadbeef") is None
