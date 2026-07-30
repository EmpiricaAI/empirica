#!/usr/bin/env python3
"""
Goal Type Definitions

Core dataclasses for structured goal representation.
Designed for explicit AI-driven goal creation (MVP - no automatic parsing).
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass
class ScopeVector:
    """
    Goal scope as epistemic vectors (AI self-assesses, Sentinel validates coherence)

    Replaces categorical enum with numeric dimensions for genuine AI assessment.
    """

    breadth: float  # 0.0-1.0: How wide the goal spans (0=single function, 1=entire codebase)
    duration: float  # 0.0-1.0: Expected lifetime (0=minutes/hours, 1=weeks/months)
    coordination: float  # 0.0-1.0: Multi-agent/session coordination needed

    def __post_init__(self):
        """Validate ranges"""
        for field_name in ["breadth", "duration", "coordination"]:
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be numeric, got {type(value)}")
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{field_name} must be 0.0-1.0, got {value}")

    def to_dict(self) -> dict[str, float]:
        """Serialize to dictionary"""
        return {"breadth": self.breadth, "duration": self.duration, "coordination": self.coordination}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ScopeVector":
        """Deserialize from dictionary"""
        return ScopeVector(
            breadth=float(data["breadth"]), duration=float(data["duration"]), coordination=float(data["coordination"])
        )


class DependencyType(Enum):
    """Dependency relationship types"""

    PREREQUISITE = "prerequisite"  # Must complete before starting
    CONCURRENT = "concurrent"  # Can work on simultaneously
    INFORMATIONAL = "informational"  # Nice to have context


@dataclass
class SuccessCriterion:
    """Measurable success criterion for goal completion"""

    id: str
    description: str
    validation_method: str  # "completion", "quality_gate", "metric_threshold"
    threshold: float | None = None  # For metric-based criteria
    is_required: bool = True  # vs. optional/nice-to-have
    is_met: bool = False  # Completion status


@dataclass
class Dependency:
    """Goal dependency specification"""

    id: str
    goal_id: str  # Which goal this depends on
    dependency_type: DependencyType
    description: str


@dataclass
class Goal:
    """
    Structured goal representation

    MVP Design: AI creates goals explicitly via MCP tools.
    No automatic parsing - keeps it simple and heuristic-free.
    """

    id: str
    objective: str  # Clear, actionable goal statement (~256 char title)
    success_criteria: list[SuccessCriterion]
    scope: ScopeVector
    description: str | None = None  # Optional rich body (up to 8000 chars)
    dependencies: list[Dependency] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    estimated_complexity: float | None = None
    created_timestamp: float = field(default_factory=time.time)
    completed_timestamp: float | None = None
    is_completed: bool = False
    engagement_id: str | None = None  # Optional engagement scope (goals.engagement_id, migration 051)

    @staticmethod
    def create(
        objective: str,
        success_criteria: list[SuccessCriterion],
        scope: ScopeVector = None,
        description: str | None = None,
        **kwargs,
    ) -> "Goal":
        """Convenience factory method with validation"""
        from .validation import (
            validate_complexity,
            validate_description,
            validate_objective,
            validate_scope_vector,
            validate_success_criteria,
        )

        # Validate inputs before creating
        validate_objective(objective)
        validate_description(description)
        validate_success_criteria(success_criteria)

        if scope is None:
            scope = ScopeVector(breadth=0.3, duration=0.2, coordination=0.1)  # Default: narrow, short, solo
        validate_scope_vector(scope)

        complexity = kwargs.get("estimated_complexity")
        if complexity is not None:
            validate_complexity(complexity)

        return Goal(
            id=str(uuid.uuid4()),
            objective=objective,
            success_criteria=success_criteria,
            scope=scope,
            description=description,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "id": self.id,
            "objective": self.objective,
            "description": self.description,
            "success_criteria": [
                {
                    "id": sc.id,
                    "description": sc.description,
                    "validation_method": sc.validation_method,
                    "threshold": sc.threshold,
                    "is_required": sc.is_required,
                    "is_met": sc.is_met,
                }
                for sc in self.success_criteria
            ],
            "scope": self.scope.to_dict(),
            "dependencies": [
                {
                    "id": dep.id,
                    "goal_id": dep.goal_id,
                    "dependency_type": dep.dependency_type.value,
                    "description": dep.description,
                }
                for dep in self.dependencies
            ],
            "constraints": self.constraints,
            "metadata": self.metadata,
            "estimated_complexity": self.estimated_complexity,
            "created_timestamp": self.created_timestamp,
            "completed_timestamp": self.completed_timestamp,
            "is_completed": self.is_completed,
            "engagement_id": self.engagement_id,
        }

    @staticmethod
    def _scope_from(scope: Any) -> "ScopeVector":
        """Build a ScopeVector, tolerating legacy scope encodings.

        `scope` has been serialized three ways across the project's history: the
        current dict, a bare label like ``"project_wide"``, and a single float.
        `ScopeVector.from_dict` does ``float(data["breadth"])``, so the latter two
        raise ("string indices must be integers", "'float' object is not iterable"),
        which `get_goal` swallowed into "Goal not found".

        A legacy value carries no breadth/duration/coordination to recover, so this
        returns a NEUTRAL 0.5 vector rather than inventing precision. The goal
        becomes addressable — which is the point — and its scope reads as unknown
        rather than as a confident number nobody measured.
        """
        if isinstance(scope, dict) and {"breadth", "duration", "coordination"} <= set(scope):
            return ScopeVector.from_dict(scope)
        return ScopeVector(breadth=0.5, duration=0.5, coordination=0.5)

    @staticmethod
    def _criterion_from(sc: Any, index: int) -> "SuccessCriterion":
        """Build a SuccessCriterion from either the current dict form or the legacy
        plain-string form.

        Older records serialized success criteria as bare strings
        (``["Goal completion achieved"]``) rather than objects. `sc["id"]` on a
        string raises "string indices must be integers", which `get_goal` swallowed
        into a log line and reported as "Goal not found" — measured 2026-07-30, 75 of
        1431 goals on this practice were unreachable for exactly this reason, their
        rows sitting intact the whole time.

        Tolerating an older serialization of its own type is a deserializer's job, so
        it lives here rather than at one call site — every read path benefits.
        """
        if isinstance(sc, str):
            return SuccessCriterion(
                id=f"legacy-{index}",
                description=sc,
                validation_method="completion",
            )
        return SuccessCriterion(
            id=sc.get("id") or f"legacy-{index}",
            description=sc.get("description", ""),
            validation_method=sc.get("validation_method", "completion"),
            threshold=sc.get("threshold"),
            is_required=sc.get("is_required", True),
            is_met=sc.get("is_met", False),
        )

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Goal":
        """Deserialize from dictionary"""
        return Goal(
            id=data["id"],
            objective=data["objective"],
            success_criteria=[Goal._criterion_from(sc, i) for i, sc in enumerate(data.get("success_criteria") or [])],
            scope=Goal._scope_from(data.get("scope")),
            dependencies=[
                Dependency(
                    id=dep["id"],
                    goal_id=dep["goal_id"],
                    dependency_type=DependencyType(dep["dependency_type"]),
                    description=dep["description"],
                )
                for dep in data.get("dependencies", [])
            ],
            constraints=data.get("constraints", {}),
            metadata=data.get("metadata", {}),
            estimated_complexity=data.get("estimated_complexity"),
            created_timestamp=data.get("created_timestamp", time.time()),
            completed_timestamp=data.get("completed_timestamp"),
            is_completed=data.get("is_completed", False),
            engagement_id=data.get("engagement_id"),
        )

    def get_subtasks(self):
        """
        Get all subtasks for this goal

        Returns list of SubTask objects from database

        Note: Creates its own repository connection for simplicity.
        For bulk operations, use TaskRepository directly.
        """
        from empirica.core.tasks.repository import TaskRepository

        repo = TaskRepository()
        try:
            subtasks = repo.get_goal_subtasks(self.id)
            return subtasks
        finally:
            repo.close()

    def calculate_progress(self) -> dict[str, Any]:
        """
        Calculate goal progress based on subtasks

        Returns:
            {
                'total_subtasks': int,
                'completed': int,
                'in_progress': int,
                'pending': int,
                'blocked': int,
                'skipped': int,
                'completion_percentage': float
            }
        """
        from empirica.core.tasks.types import TaskStatus

        subtasks = self.get_subtasks()

        if not subtasks:
            # No subtasks - use success criteria or is_completed flag
            if self.is_completed:
                return {
                    "total_subtasks": 0,
                    "completed": 0,
                    "in_progress": 0,
                    "pending": 0,
                    "blocked": 0,
                    "skipped": 0,
                    "completion_percentage": 100.0,
                    "note": "No subtasks, marked as complete",
                }
            else:
                return {
                    "total_subtasks": 0,
                    "completed": 0,
                    "in_progress": 0,
                    "pending": 0,
                    "blocked": 0,
                    "skipped": 0,
                    "completion_percentage": 0.0,
                    "note": "No subtasks created yet",
                }

        # Count by status
        status_counts = {
            TaskStatus.COMPLETED: 0,
            TaskStatus.IN_PROGRESS: 0,
            TaskStatus.PENDING: 0,
            TaskStatus.BLOCKED: 0,
            TaskStatus.SKIPPED: 0,
        }

        for subtask in subtasks:
            status_counts[subtask.status] = status_counts.get(subtask.status, 0) + 1

        total = len(subtasks)
        completed = status_counts[TaskStatus.COMPLETED] + status_counts[TaskStatus.SKIPPED]

        return {
            "total_subtasks": total,
            "completed": status_counts[TaskStatus.COMPLETED],
            "in_progress": status_counts[TaskStatus.IN_PROGRESS],
            "pending": status_counts[TaskStatus.PENDING],
            "blocked": status_counts[TaskStatus.BLOCKED],
            "skipped": status_counts[TaskStatus.SKIPPED],
            "completion_percentage": (completed / total * 100.0) if total > 0 else 0.0,
        }

    def is_ready_for_completion(self) -> bool:
        """
        Check if goal is ready to be marked complete

        Checks:
        1. All subtasks completed or skipped (if subtasks exist)
        2. Falls back to is_completed flag if no subtasks

        Returns:
            True if ready for completion, False otherwise
        """
        progress = self.calculate_progress()

        # If no subtasks, rely on is_completed flag
        if progress["total_subtasks"] == 0:
            return self.is_completed

        # If subtasks exist, check if all are done
        all_done = progress["completed"] + progress["skipped"] == progress["total_subtasks"]

        return all_done
