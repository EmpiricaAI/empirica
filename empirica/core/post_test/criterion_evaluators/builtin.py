"""Built-in criterion evaluators.

Auto-registered on package import (see __init__.py). Adding a new built-in:
1. Define class with `validation_method` class attribute, `applies()`, `evaluate()`
2. Append a `register(MyEvaluator())` call at the bottom

G1 ships SubtaskCompletionEvaluator. G2 will add EvidenceMetricEvaluator.
G3 (deferred) will add VectorThresholdEvaluator.
"""

from __future__ import annotations

import logging

from ._types import CriterionContext, CriterionResult
from .registry import register

logger = logging.getLogger(__name__)


class SubtaskCompletionEvaluator:
    """Evaluate `completion` criteria against goal subtask progress.

    Threshold defaults to 1.0 (all subtasks done). Compares against
    completion_percentage / 100 — Goal.calculate_progress() treats both
    COMPLETED and SKIPPED subtasks as "done", matching is_ready_for_completion.

    Goals with zero subtasks: pass if is_completed=True, otherwise skipped
    (no signal — can't measure completion of unstructured work).
    """

    validation_method = "completion"

    def applies(self, _ctx: CriterionContext) -> bool:
        return True

    def evaluate(self, ctx: CriterionContext) -> CriterionResult:
        progress = ctx.goal.calculate_progress()
        total = progress.get("total_subtasks", 0)
        threshold = ctx.criterion.threshold if ctx.criterion.threshold is not None else 1.0

        if total == 0:
            if ctx.goal.is_completed:
                return CriterionResult(
                    criterion_id=ctx.criterion.id,
                    goal_id=ctx.goal.id,
                    validation_method=self.validation_method,
                    passed=True,
                    value=1.0,
                    threshold=threshold,
                    summary="Goal marked complete (no subtasks)",
                )
            return CriterionResult(
                criterion_id=ctx.criterion.id,
                goal_id=ctx.goal.id,
                validation_method=self.validation_method,
                passed=False,
                skipped=True,
                value=0.0,
                threshold=threshold,
                summary="No subtasks and goal not marked complete — no signal",
            )

        ratio = progress.get("completion_percentage", 0.0) / 100.0
        passed = ratio >= threshold
        return CriterionResult(
            criterion_id=ctx.criterion.id,
            goal_id=ctx.goal.id,
            validation_method=self.validation_method,
            passed=passed,
            value=ratio,
            threshold=threshold,
            summary=f"subtask completion {ratio:.0%} vs threshold {threshold:.0%}",
            iteration_needed=(not passed and ctx.criterion.is_required),
            next_transaction="Complete remaining required subtasks" if not passed else None,
        )


# Auto-register on import. New built-ins: append register() calls below.
register(SubtaskCompletionEvaluator())
