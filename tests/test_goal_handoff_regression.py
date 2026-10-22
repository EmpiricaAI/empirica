"""
Regression test for goal handoff bug fix

Tests that discover_goals() and add_lineage() work correctly
after the git notes enumeration fix.

Bug: Goals stored in refs/notes/empirica/goals/<uuid> were not discoverable
Fix: Use 'git for-each-ref' instead of 'git notes list'
"""

import pytest

from empirica.core.canonical.empirica_git import GitGoalStore

# Marked as integration: requires empirica CLI on PATH, git-initialized
# CWD, and/or a populated sessions.db. Excluded from default CI run
# (pytest -m "not integration"). Run explicitly via:
#   pytest -m integration tests/...
pytestmark = pytest.mark.integration


class TestGoalHandoffRegression:
    """Regression tests for goal discovery and lineage tracking"""

    def test_discover_goals_returns_results(self):
        """
        Test that discover_goals() actually finds stored goals

        Regression: Previously returned empty list due to wrong git command
        """
        store = GitGoalStore()
        goals = store.discover_goals()

        # Should find at least some goals (4 documentation goals exist)
        assert len(goals) > 0, "discover_goals() should find stored goals"

        # Each goal should have required fields
        for goal in goals:
            assert "goal_id" in goal
            assert "ai_id" in goal
            assert "goal_data" in goal
            assert "lineage" in goal

    def test_discover_goals_filters_by_ai(self):
        """
        Test that filtering by ai_id works correctly

        Ensures the for-each-ref parsing correctly loads goal data
        """
        store = GitGoalStore()

        # Get all goals first
        all_goals = store.discover_goals()
        if len(all_goals) == 0:
            pytest.skip("No goals in repository")

        # Pick an AI that has goals
        ai_id = all_goals[0]["ai_id"]

        # Filter by that AI
        filtered_goals = store.discover_goals(from_ai_id=ai_id)

        assert len(filtered_goals) > 0, f"Should find goals from {ai_id}"

        # All returned goals should be from that AI
        for goal in filtered_goals:
            assert goal["ai_id"] == ai_id

    def test_lineage_preserved_on_resume(self, tmp_path):
        """
        Lineage is appended, never overwritten, and stays on the ONE note the reader loads.

        Regression 1: lineage used to be overwritten with a fresh array.
        Regression 2: add_lineage wrote at HEAD, so once HEAD had moved the goal ref held two notes and load_goal read the
        stale one; this test used to ASSERT that (as a "known limitation") against the real repository's goals, mutating
        its notes on every run. It now runs in a scratch repository and asserts the single-note contract.
        """
        import json
        import subprocess
        import uuid

        def git(*args):
            return subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        git("init", "-q")
        git("config", "user.name", "t")  # store_goal runs plain `git notes`, which needs a repo-local identity
        git("config", "user.email", "t@example.invalid")
        (tmp_path / "f").write_text("x")
        git("add", "f")
        git("commit", "-q", "-m", "c1")
        store = GitGoalStore(str(tmp_path))
        goal_id = str(uuid.uuid4())
        assert store.store_goal(goal_id, "sess", "creator-ai", {"objective": "o"})
        original = store.load_goal(goal_id)
        assert original is not None
        original_lineage = original["lineage"]

        (tmp_path / "g").write_text("y")  # HEAD moves between creation and resume
        git("add", "g")
        git("commit", "-q", "-m", "c2")

        test_ai_id = f"test-ai-regression-{uuid.uuid4().hex[:8]}"
        assert store.add_lineage(goal_id, test_ai_id, "resumed"), "add_lineage should succeed"

        notes = git("notes", f"--ref=empirica/goals/{goal_id}", "list").splitlines()
        assert len(notes) == 1, "one note per goal ref, however far HEAD has moved"
        loaded = store.load_goal(goal_id)
        assert loaded is not None
        lineage = loaded["lineage"]
        assert len(lineage) == len(original_lineage) + 1
        assert lineage[-1]["ai_id"] == test_ai_id and lineage[-1]["action"] == "resumed"
        assert lineage[: len(original_lineage)] == original_lineage
        assert (
            json.loads(git("notes", f"--ref=empirica/goals/{goal_id}", "show", notes[0].split()[1]))["created_at"]
            == (original["created_at"])
        ), "and the creation time survives the re-store"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
