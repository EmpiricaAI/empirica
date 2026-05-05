"""Internal type definitions for criterion evaluators.

Kept in a separate module from __init__.py to avoid circular-import
ambiguity between registry.py / builtin.py and the package init.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from empirica.core.goals.types import Goal, SuccessCriterion
from empirica.core.post_test.collector import EvidenceBundle


@dataclass
class CriterionContext:
    """Input passed to every evaluator."""

    criterion: SuccessCriterion
    goal: Goal
    evidence: EvidenceBundle
    session_id: str
    project_id: str | None = None
    transaction_id: str | None = None
    postflight_vectors: dict[str, float] | None = None


@dataclass
class CriterionResult:
    """Output from a single evaluator. Advisory — never hard-blocks."""

    criterion_id: str
    goal_id: str
    validation_method: str
    passed: bool
    skipped: bool = False
    value: float | None = None
    threshold: float | None = None
    summary: str = ""
    iteration_needed: bool = False
    next_transaction: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "goal_id": self.goal_id,
            "validation_method": self.validation_method,
            "passed": self.passed,
            "skipped": self.skipped,
            "value": self.value,
            "threshold": self.threshold,
            "summary": self.summary,
            "iteration_needed": self.iteration_needed,
            "next_transaction": self.next_transaction,
        }


class CriterionEvaluator(Protocol):
    """Protocol every criterion evaluator implements."""

    validation_method: str

    def applies(self, ctx: CriterionContext) -> bool: ...

    def evaluate(self, ctx: CriterionContext) -> CriterionResult: ...
