"""
Regression test for goals-complete-subtask silent-success bug.

Bug (fixed 2026-05-26): `update_subtask_status` returned True for non-existent
subtask IDs because `_resolve_subtask_id` short-circuited any input containing
'-' as a "full UUID" without verifying it existed in the DB. The SQL UPDATE
then silently affected 0 rows and the method returned True.

These tests pin the contract: the resolver always verifies existence, and
update_subtask_status returns False for unknown IDs (full UUID or partial).
"""

import uuid

import pytest

from empirica.core.tasks.repository import TaskRepository
from empirica.core.tasks.types import EpistemicImportance, SubTask, TaskStatus


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test_subtasks.db")


@pytest.fixture
def repo(temp_db):
    r = TaskRepository(db_path=temp_db)
    yield r
    r.close()


@pytest.fixture
def real_subtask(repo):
    """Create one real subtask in the temp DB; return its id."""
    goal_id = str(uuid.uuid4())
    subtask = SubTask.create(
        goal_id=goal_id,
        description="real subtask",
        epistemic_importance=EpistemicImportance.MEDIUM,
    )
    assert repo.save_subtask(subtask)
    return subtask.id


class TestResolveSubtaskIdValidation:
    def test_resolve_returns_none_for_nonexistent_full_uuid(self, repo):
        bad = "deadbeef-cafe-1234-5678-aaaabbbbcccc"
        assert repo._resolve_subtask_id(bad) is None

    def test_resolve_returns_none_for_nonexistent_partial(self, repo):
        assert repo._resolve_subtask_id("deadbeef") is None

    def test_resolve_returns_full_uuid_when_full_uuid_exists(self, repo, real_subtask):
        assert repo._resolve_subtask_id(real_subtask) == real_subtask

    def test_resolve_returns_full_uuid_when_partial_matches(self, repo, real_subtask):
        prefix = real_subtask[:8]
        assert repo._resolve_subtask_id(prefix) == real_subtask


class TestUpdateSubtaskStatusValidation:
    def test_update_returns_false_for_nonexistent_full_uuid(self, repo):
        """The bug: previously returned True for any '-'-containing input."""
        bad = "deadbeef-cafe-1234-5678-aaaabbbbcccc"
        assert repo.update_subtask_status(bad, TaskStatus.COMPLETED, "evidence") is False

    def test_update_returns_false_for_nonexistent_partial(self, repo):
        assert repo.update_subtask_status("deadbeef", TaskStatus.COMPLETED, "evidence") is False

    def test_update_returns_true_for_real_subtask(self, repo, real_subtask):
        assert repo.update_subtask_status(real_subtask, TaskStatus.COMPLETED, "evidence") is True

    def test_update_returns_true_for_real_partial(self, repo, real_subtask):
        assert repo.update_subtask_status(real_subtask[:8], TaskStatus.COMPLETED, "evidence") is True
