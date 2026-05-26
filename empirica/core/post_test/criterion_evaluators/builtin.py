"""Built-in criterion evaluators.

Auto-registered on package import (see __init__.py). Adding a new built-in:
1. Define class with `validation_method` class attribute, `applies()`, `evaluate()`
2. Append a `register(MyEvaluator())` call at the bottom

G1 ships SubtaskCompletionEvaluator. G2 ships EvidenceMetricEvaluator.
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
                    summary="Goal marked complete (no tasks)",
                )
            return CriterionResult(
                criterion_id=ctx.criterion.id,
                goal_id=ctx.goal.id,
                validation_method=self.validation_method,
                passed=False,
                skipped=True,
                value=0.0,
                threshold=threshold,
                summary="No tasks and goal not marked complete — no signal",
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
            summary=f"task completion {ratio:.0%} vs threshold {threshold:.0%}",
            iteration_needed=(not passed and ctx.criterion.is_required),
            next_transaction="Complete remaining required tasks" if not passed else None,
        )


class EvidenceMetricEvaluator:
    """Evaluate `quality_gate` criteria against a named metric in EvidenceBundle.

    The criterion's `description` field carries the metric name to look up
    (e.g. "prose_stylometry_adherence", "ruff_violation_density"). The
    evaluator reads it from the bundle, applies the metric's declared
    direction, and compares against the criterion's threshold.

    Threshold semantics:
      - higher_is_better: passes when value >= threshold
      - lower_is_better: passes when value <= threshold

    Skips with a clear summary if:
      - The metric isn't present in the bundle (collector didn't run, or
        bundle is empty — common for goal_criteria evaluation when the
        quality_gate metric needs a collector that wasn't profile-active)
      - threshold is None (criterion declared without a numeric target)
    """

    validation_method = "quality_gate"

    def applies(self, ctx: CriterionContext) -> bool:
        # Skip if no metric name to look up
        if not ctx.criterion.description:
            return False
        return ctx.evidence.has(ctx.criterion.description)

    def evaluate(self, ctx: CriterionContext) -> CriterionResult:
        metric = ctx.criterion.description
        threshold = ctx.criterion.threshold

        if threshold is None:
            return CriterionResult(
                criterion_id=ctx.criterion.id,
                goal_id=ctx.goal.id,
                validation_method=self.validation_method,
                passed=False,
                skipped=True,
                summary=f"quality_gate criterion {metric!r} declared without threshold",
            )

        value = ctx.evidence.get(metric)
        if value is None:
            return CriterionResult(
                criterion_id=ctx.criterion.id,
                goal_id=ctx.goal.id,
                validation_method=self.validation_method,
                passed=False,
                skipped=True,
                threshold=threshold,
                summary=f"metric {metric!r} not present in evidence bundle",
            )

        direction = ctx.evidence.direction(metric)
        if direction == "lower_is_better":
            passed = value <= threshold
            op_repr = "<="
        else:
            passed = value >= threshold
            op_repr = ">="

        return CriterionResult(
            criterion_id=ctx.criterion.id,
            goal_id=ctx.goal.id,
            validation_method=self.validation_method,
            passed=passed,
            value=value,
            threshold=threshold,
            summary=f"{metric}={value:.3f} {op_repr} {threshold:.3f} ({direction})",
            iteration_needed=(not passed and ctx.criterion.is_required),
            next_transaction=f"Address {metric} regression" if not passed else None,
        )


# Auto-register on import. New built-ins: append register() calls below.
register(SubtaskCompletionEvaluator())
register(EvidenceMetricEvaluator())
