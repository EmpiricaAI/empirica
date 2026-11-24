#!/usr/bin/env python3
"""
Task Type Definitions

Core dataclasses for task decomposition and tracking.
MVP design: AI creates subtasks explicitly, no automatic decomposition yet.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EpistemicImportance(Enum):
    """Task importance from epistemic perspective"""

    CRITICAL = "critical"  # Required for goal success
    HIGH = "high"  # Important but not blocking
    MEDIUM = "medium"  # Nice to have
    LOW = "low"  # Optional enhancement


class TaskStatus(Enum):
    """Task completion status"""

    PENDING = "pending"  # Not started
    IN_PROGRESS = "in_progress"  # Currently working
    COMPLETED = "completed"  # Done
    BLOCKED = "blocked"  # Blocked by dependency
    SKIPPED = "skipped"  # Decided not to do


@dataclass
class SubTask:
    """
    Individual subtask within a goal

    MVP Design: AI creates these explicitly via the CLI (goals-add-task).
    """

    id: str
    goal_id: str  # Parent goal
    description: str  # What to do
    status: TaskStatus
    epistemic_importance: EpistemicImportance

    # Optional fields
    dependencies: list[str] = field(default_factory=list)  # Other subtask IDs
    estimated_tokens: int | None = None
    actual_tokens: int | None = None
    completion_evidence: str | None = None  # Git commit hash, file path, etc.
    notes: str = ""
    created_timestamp: float = field(default_factory=time.time)
    completed_timestamp: float | None = None

    # Epistemic investigation tracking (v4.0)
    findings: list[str] = field(default_factory=list)  # Validated discoveries
    unknowns: list[str] = field(default_factory=list)  # Remaining questions
    dead_ends: list[str] = field(default_factory=list)  # Failed approaches

    kind: str | None = None  # core/work_kinds.py: fix, refine, simplify, expand, investigate, sustain (goal 1dfb9911)

    @staticmethod
    def create(
        goal_id: str, description: str, epistemic_importance: EpistemicImportance = EpistemicImportance.MEDIUM, **kwargs
    ) -> "SubTask":
        """Convenience factory method"""
        return SubTask(
            id=str(uuid.uuid4()),
            goal_id=goal_id,
            description=description,
            status=TaskStatus.PENDING,
            epistemic_importance=epistemic_importance,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "description": self.description,
            "status": self.status.value,
            "epistemic_importance": self.epistemic_importance.value,
            "dependencies": self.dependencies,
            "estimated_tokens": self.estimated_tokens,
            "actual_tokens": self.actual_tokens,
            "completion_evidence": self.completion_evidence,
            "notes": self.notes,
            "created_timestamp": self.created_timestamp,
            "completed_timestamp": self.completed_timestamp,
            "findings": self.findings,
            "unknowns": self.unknowns,
            "dead_ends": self.dead_ends,
            "kind": self.kind,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SubTask":
        """Deserialize from dictionary"""
        return SubTask(
            id=data["id"],
            goal_id=data["goal_id"],
            description=data["description"],
            status=TaskStatus(data["status"]),
            epistemic_importance=EpistemicImportance(data["epistemic_importance"]),
            dependencies=data.get("dependencies", []),
            estimated_tokens=data.get("estimated_tokens"),
            actual_tokens=data.get("actual_tokens"),
            completion_evidence=data.get("completion_evidence"),
            notes=data.get("notes", ""),
            created_timestamp=data.get("created_timestamp", time.time()),
            completed_timestamp=data.get("completed_timestamp"),
            findings=data.get("findings", []),
            unknowns=data.get("unknowns", []),
            dead_ends=data.get("dead_ends", []),
            kind=data.get("kind"),
        )


@dataclass
class TaskDecomposition:
    """
    Complete task breakdown for a goal

    MVP: Simple container for manually created subtasks.
    Future: Add automatic decomposition, critical path analysis, etc.
    """

    goal_id: str
    subtasks: list[SubTask]
    critical_path: list[str] = field(default_factory=list)  # Subtask IDs in order
    total_estimated_tokens: int = 0
    complexity_factors: dict[str, Any] = field(default_factory=dict)
    created_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "goal_id": self.goal_id,
            "subtasks": [st.to_dict() for st in self.subtasks],
            "critical_path": self.critical_path,
            "total_estimated_tokens": self.total_estimated_tokens,
            "complexity_factors": self.complexity_factors,
            "created_timestamp": self.created_timestamp,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TaskDecomposition":
        """Deserialize from dictionary"""
        return TaskDecomposition(
            goal_id=data["goal_id"],
            subtasks=[SubTask.from_dict(st) for st in data["subtasks"]],
            critical_path=data.get("critical_path", []),
            total_estimated_tokens=data.get("total_estimated_tokens", 0),
            complexity_factors=data.get("complexity_factors", {}),
            created_timestamp=data.get("created_timestamp", time.time()),
        )
