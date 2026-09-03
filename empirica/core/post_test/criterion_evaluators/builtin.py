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

# The sentinel `goals-create` writes when the practitioner supplies no criterion.
# 3,011 of 5,484 criteria in the measured corpus are exactly this string.
AUTO_FILLED_CRITERION = "Goal completion achieved"

# Descriptions that genuinely NAME the subtask-ratio metric, as opposed to prose
# that merely happens to carry the method label. Kept deliberately small: a
# guessy match here would re-create the over-claiming this narrowing removes.
COMPLETION_METRIC_NAMES = frozenset(
    {
        "subtask_ratio",
        "task_completion",
        "task_completion_ratio",
        "completion",
    }
)


def _is_completion_shaped(criterion) -> bool:
    """Is this criterion actually ABOUT subtask completion?

    Three ways to qualify, in descending certainty:

    1. It is the auto-filled sentinel — by construction it means nothing more
       specific than "the goal is done", which is exactly a subtask ratio.
    2. Its description names the metric (`subtask_ratio`, `task_completion`, …).
    3. It carries an explicit numeric threshold, i.e. the author was thinking in
       ratios rather than writing prose.

    Anything else is authored prose wearing the `completion` label because it was
    the only label on offer, and this evaluator cannot judge it.
    """
    desc = (getattr(criterion, "description", "") or "").strip()
    if AUTO_FILLED_CRITERION in desc:
        return True
    if desc.lower() in COMPLETION_METRIC_NAMES:
        return True
    return getattr(criterion, "threshold", None) is not None


class ProseCriterionEvaluator:
    """Evaluate nothing, and SAY SO — the honest outcome for `prose` / `undetermined`.

    A criterion that no machine can judge must be distinguishable from one that
    was judged and passed. Registering an explicit evaluator (rather than leaving
    the method unregistered) is what makes the distinction legible: the generic
    *no evaluator registered for validation_method=...* reads like a
    misconfiguration, while this reads like a deliberate declaration, which it is.

    Never passes and never fails. `skipped=True` keeps it out of both counts.
    """

    validation_method = "prose"

    def applies(self, _ctx: CriterionContext) -> bool:
        return True

    def evaluate(self, ctx: CriterionContext) -> CriterionResult:
        kind = ctx.criterion.validation_method
        if kind == "undetermined":
            summary = "declared UNDETERMINED — checkability not yet decided; revisit rather than assume"
        else:
            summary = "declared PROSE — not machine-checkable by design; a human reads this one"
        return CriterionResult(
            criterion_id=ctx.criterion.id,
            goal_id=ctx.goal.id,
            validation_method=kind,
            passed=False,
            skipped=True,
            summary=summary,
        )


class SubtaskCompletionEvaluator:
    """Evaluate `completion` criteria against goal subtask progress.

    Threshold defaults to 1.0 (all subtasks done). Compares against
    completion_percentage / 100 — Goal.calculate_progress() treats both
    COMPLETED and SKIPPED subtasks as "done", matching is_ready_for_completion.

    Goals with zero subtasks: pass if is_completed=True, otherwise skipped
    (no signal — can't measure completion of unstructured work).
    """

    validation_method = "completion"

    def applies(self, ctx: CriterionContext) -> bool:
        """Only claim criteria this evaluator can actually judge.

        This was `return True`, and combined with a one-value vocabulary it meant
        the evaluator claimed EVERY criterion in the corpus — 5,462 of 5,484
        measured across 59 practices. So a criterion reading *SSH password auth
        disabled* had its completion judged by counting subtasks. The description
        asked one question, the evaluator answered a different one, and the
        answer was reported under the description's name. Broccoli:
        one-predicate-two-questions, at 99.6% scale.

        It is also the mechanical cause of the *task completion 0% vs threshold
        100%* nudge that fired on ~15 consecutive POSTFLIGHTs and was ignored
        every time — it was structurally incapable of saying anything else, so
        dismissing it was the correct local response.

        Declining is the honest outcome for an authored prose criterion: it
        surfaces as SKIPPED with a reason, which is what *nobody checked this*
        should look like. Over-claiming produces a confident wrong answer, and a
        missing evaluator is strictly better than that — the missing one is
        visibly absent.
        """
        return _is_completion_shaped(ctx.criterion)

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

# `prose` and `undetermined` are two names for the same honest outcome, so one
# evaluator serves both — registered twice because the registry keys on the
# method string. A shared instance would report the wrong `validation_method`
# back, so each registration carries its own.
register(ProseCriterionEvaluator())
_undetermined = ProseCriterionEvaluator()
_undetermined.validation_method = "undetermined"
register(_undetermined)
