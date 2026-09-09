#!/usr/bin/env python3
"""
End-to-End Tests for Goal Architecture

Tests the complete workflow: create goal → add subtasks → mark complete → track progress
Designed for Minimax to follow and validate implementation.
"""

import uuid

import pytest

from empirica.core.completion.tracker import CompletionTracker
from empirica.core.goals.repository import GoalRepository
from empirica.core.goals.types import Goal, ScopeVector, SuccessCriterion
from empirica.core.goals.validation import ValidationError
from empirica.core.tasks.repository import TaskRepository
from empirica.core.tasks.types import EpistemicImportance, SubTask


class TestGoalArchitectureE2E:
    """End-to-end tests for complete goal workflow"""

    @pytest.fixture
    def temp_db(self, tmp_path):
        """Create temporary database for testing"""
        db_path = tmp_path / "test_goals.db"
        return str(db_path)

    @pytest.fixture
    def session_id(self):
        """Generate test session ID"""
        return str(uuid.uuid4())

    def test_complete_goal_workflow(self, temp_db, session_id):
        """
        Test complete workflow from goal creation to completion tracking

        Workflow:
        1. Create a goal with success criteria
        2. Add multiple subtasks with different importance levels
        3. Mark subtasks as complete
        4. Track progress
        5. Verify goal completion when all subtasks done
        """
        # STEP 1: Create a goal
        goal_repo = GoalRepository(db_path=temp_db)

        success_criteria = [
            SuccessCriterion(
                id=str(uuid.uuid4()),
                description="All input validation tests pass",
                validation_method="completion",
                is_required=True,
            ),
            SuccessCriterion(
                id=str(uuid.uuid4()),
                description="Code coverage >= 80%",
                validation_method="metric_threshold",
                threshold=0.8,
                is_required=True,
            ),
        ]

        goal = Goal.create(
            objective="Implement input validation for Goal Architecture",
            success_criteria=success_criteria,
            scope=ScopeVector(breadth=0.3, duration=0.2, coordination=0.1),
            estimated_complexity=0.6,
        )

        # Save goal
        assert goal_repo.save_goal(goal, session_id), "Goal should save successfully"

        # Verify goal saved
        retrieved_goal = goal_repo.get_goal(goal.id)
        assert retrieved_goal is not None, "Goal should be retrievable"
        assert retrieved_goal.objective == goal.objective
        assert len(retrieved_goal.success_criteria) == 2

        # STEP 2: Add subtasks
        task_repo = TaskRepository(db_path=temp_db)

        subtasks_data = [
            ("Add validation for empty objective", EpistemicImportance.CRITICAL, 500),
            ("Add validation for invalid scope enum", EpistemicImportance.CRITICAL, 300),
            ("Add validation for success_criteria format", EpistemicImportance.HIGH, 600),
            ("Add validation for complexity range (0-1)", EpistemicImportance.MEDIUM, 200),
            ("Write comprehensive test cases", EpistemicImportance.HIGH, 1000),
        ]

        subtasks = []
        for desc, importance, tokens in subtasks_data:
            subtask = SubTask.create(
                goal_id=goal.id, description=desc, epistemic_importance=importance, estimated_tokens=tokens
            )
            assert task_repo.save_subtask(subtask), f"Subtask '{desc}' should save"
            subtasks.append(subtask)

        # Verify subtasks saved
        retrieved_subtasks = task_repo.get_goal_subtasks(goal.id)
        assert len(retrieved_subtasks) == 5, "All 5 subtasks should be saved"

        # STEP 3: Track initial progress (should be 0%)
        tracker = CompletionTracker(db_path=temp_db)
        progress = tracker.track_progress(goal.id)

        assert progress.completion_percentage == 0.0, "Initial progress should be 0%"
        assert len(progress.remaining_subtasks) == 5, "All subtasks should be pending"
        assert progress.estimated_remaining_tokens == 2600, "Token estimate should match"

        # STEP 4: Complete subtasks incrementally
        # Complete first subtask (CRITICAL)
        assert tracker.record_subtask_completion(subtasks[0].id, evidence="commit:abc123"), (
            "First subtask completion should succeed"
        )

        progress = tracker.track_progress(goal.id)
        assert progress.completion_percentage == 0.2, "Progress should be 20% (1/5)"
        assert len(progress.completed_subtasks) == 1
        assert len(progress.remaining_subtasks) == 4
        assert subtasks[0].id in progress.completion_evidence

        # Complete second and third subtasks
        tracker.record_subtask_completion(subtasks[1].id, evidence="commit:def456")
        tracker.record_subtask_completion(subtasks[2].id, evidence="commit:ghi789")

        progress = tracker.track_progress(goal.id)
        assert progress.completion_percentage == 0.6, "Progress should be 60% (3/5)"

        # STEP 5: Complete remaining subtasks
        tracker.record_subtask_completion(subtasks[3].id)
        tracker.record_subtask_completion(subtasks[4].id, evidence="commit:jkl012")

        progress = tracker.track_progress(goal.id)
        assert progress.completion_percentage == 1.0, "Progress should be 100% (5/5)"
        assert len(progress.completed_subtasks) == 5
        assert len(progress.remaining_subtasks) == 0

        # STEP 6: Verify goal marked as complete
        final_goal = goal_repo.get_goal(goal.id)
        assert final_goal.is_completed, "Goal should be marked complete"
        assert final_goal.completed_timestamp is not None

        # Cleanup
        goal_repo.close()
        task_repo.close()
        tracker.close()

    def test_input_validation_objective(self, temp_db, session_id):
        """Test validation for empty/invalid objective"""
        goal_repo = GoalRepository(db_path=temp_db)

        # Test empty objective
        with pytest.raises(ValidationError):
            goal = Goal.create(
                objective="",  # Invalid: empty
                success_criteria=[],
                scope=ScopeVector(breadth=0.3, duration=0.2, coordination=0.1),
            )
            goal_repo.save_goal(goal, session_id)

        goal_repo.close()

    def test_input_validation_scope(self, temp_db, session_id):
        """Test validation for invalid scope enum"""
        goal_repo = GoalRepository(db_path=temp_db)

        # Valid scope should work
        goal = Goal.create(
            objective="Valid goal",
            success_criteria=[
                SuccessCriterion(id=str(uuid.uuid4()), description="Test criterion", validation_method="completion")
            ],
            scope=ScopeVector(breadth=0.3, duration=0.2, coordination=0.1),
        )
        assert goal_repo.save_goal(goal, session_id)

        # Invalid scope values should fail
        with pytest.raises((ValueError, TypeError)):
            ScopeVector(breadth=2.0, duration=0.2, coordination=0.1)  # breadth > 1.0

        goal_repo.close()

    def test_input_validation_success_criteria(self, temp_db, session_id):
        """Test validation for success criteria format"""
        goal_repo = GoalRepository(db_path=temp_db)

        # Valid success criteria
        valid_sc = SuccessCriterion(id=str(uuid.uuid4()), description="Valid criterion", validation_method="completion")

        goal = Goal.create(
            objective="Test goal",
            success_criteria=[valid_sc],
            scope=ScopeVector(breadth=0.3, duration=0.2, coordination=0.1),
        )
        assert goal_repo.save_goal(goal, session_id)

        # Invalid: missing required fields
        with pytest.raises(TypeError):
            SuccessCriterion(description="Missing id and validation_method")

        goal_repo.close()

    def test_input_validation_epistemic_importance(self, temp_db):
        """Test validation for epistemic importance enum"""
        task_repo = TaskRepository(db_path=temp_db)

        # Valid importance levels
        for importance in [
            EpistemicImportance.CRITICAL,
            EpistemicImportance.HIGH,
            EpistemicImportance.MEDIUM,
            EpistemicImportance.LOW,
        ]:
            subtask = SubTask.create(
                goal_id=str(uuid.uuid4()), description="Test task", epistemic_importance=importance
            )
            assert task_repo.save_subtask(subtask)

        # Invalid importance should fail
        with pytest.raises(ValueError):
            EpistemicImportance("invalid_importance")

        task_repo.close()

    def test_query_filtering(self, temp_db, session_id):
        """Test goal querying with filters"""
        goal_repo = GoalRepository(db_path=temp_db)

        # Create multiple goals with different scopes
        goals_data = [
            ("Task 1", ScopeVector(breadth=0.2, duration=0.1, coordination=0.05), False),
            ("Session 1", ScopeVector(breadth=0.5, duration=0.6, coordination=0.3), False),
            ("Project 1", ScopeVector(breadth=0.9, duration=0.9, coordination=0.8), True),
            ("Task 2", ScopeVector(breadth=0.2, duration=0.1, coordination=0.05), True),
        ]

        for obj, scope, completed in goals_data:
            goal = Goal.create(
                objective=obj,
                success_criteria=[
                    SuccessCriterion(id=str(uuid.uuid4()), description="Test", validation_method="completion")
                ],
                scope=scope,
            )
            goal.is_completed = completed
            if completed:
                goal.completed_timestamp = 1234567890.0
            goal_repo.save_goal(goal, session_id)

        # Query by completion status
        completed_goals = goal_repo.query_goals(session_id=session_id, is_completed=True)
        assert len(completed_goals) == 2, "Should find 2 completed goals"

        incomplete_goals = goal_repo.query_goals(session_id=session_id, is_completed=False)
        assert len(incomplete_goals) == 2, "Should find 2 incomplete goals"

        # Query by scope
        task_goals = goal_repo.query_goals(
            session_id=session_id, scope=ScopeVector(breadth=0.2, duration=0.1, coordination=0.05)
        )
        assert len(task_goals) == 2, "Should find 2 task-specific goals"

        # Query completed + specific scope
        completed_tasks = goal_repo.query_goals(
            session_id=session_id, is_completed=True, scope=ScopeVector(breadth=0.2, duration=0.1, coordination=0.05)
        )
        assert len(completed_tasks) == 1, "Should find 1 completed task-specific goal"

        goal_repo.close()

    def test_serialization_roundtrip(self, temp_db, session_id):
        """Test that goals serialize/deserialize correctly"""
        goal_repo = GoalRepository(db_path=temp_db)

        # Create complex goal
        goal = Goal.create(
            objective="Test serialization",
            success_criteria=[
                SuccessCriterion(
                    id=str(uuid.uuid4()), description="Criterion 1", validation_method="completion", is_required=True
                ),
                SuccessCriterion(
                    id=str(uuid.uuid4()),
                    description="Criterion 2",
                    validation_method="metric_threshold",
                    threshold=0.9,
                    is_required=False,
                ),
            ],
            scope=ScopeVector(breadth=0.6, duration=0.7, coordination=0.4),
            estimated_complexity=0.7,
            constraints={"max_iterations": 50, "token_budget": 10000},
            metadata={"tags": ["testing", "serialization"], "priority": "high"},
        )

        # Save and retrieve
        assert goal_repo.save_goal(goal, session_id)
        retrieved = goal_repo.get_goal(goal.id)

        # Verify all fields
        assert retrieved.objective == goal.objective
        assert retrieved.scope == goal.scope
        assert retrieved.estimated_complexity == goal.estimated_complexity
        assert retrieved.constraints == goal.constraints
        assert retrieved.metadata == goal.metadata
        assert len(retrieved.success_criteria) == 2

        # Verify success criteria details
        sc1 = retrieved.success_criteria[0]
        assert sc1.validation_method == "completion"
        assert sc1.is_required

        sc2 = retrieved.success_criteria[1]
        assert sc2.validation_method == "metric_threshold"
        assert sc2.threshold == 0.9
        assert not sc2.is_required

        goal_repo.close()


class TestMCPIntegration:
    """Test MCP tool integration (requires running MCP server)"""

    def test_mcp_tool_schemas(self):
        """Superseded placeholder, converted from a silent lie to a loud skip.

        The body was `pass` under a docstring claiming "Verify MCP tool schemas
        are valid" — reported PASSED forever while verifying nothing. Real MCP
        schema coverage now lives in empirica-mcp/tests/test_tool_schema.py, so
        this skips by name rather than passing by silence. Kept (not deleted)
        so the skip count records that this suite defers MCP schema coverage
        elsewhere, instead of the gap being invisible.
        """
        pytest.skip("MCP tool schema coverage lives in empirica-mcp/tests/test_tool_schema.py")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
