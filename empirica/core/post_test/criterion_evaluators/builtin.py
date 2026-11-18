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


# Metrics whose `value` is a normalised score while their NAME promises a measure the collector keeps in `raw_value`:
# metric -> the raw_value key holding that measure. The densities store `1 - density/k` (higher is better), commit_count
# a maturity-normalised score; a typed threshold is written in the quantity the name promises, so it is compared there.
# `inverse_severe_issue_density` and `readability_density` are not here: their names already promise the score.
NAMED_MEASURE_KEYS = {
    "ruff_violation_density": "per_100_lines",  # collector._collect_ruff_evidence
    "proselint_violations": "per_100_words",  # prose_collector._run_proselint
    "commit_count": "commits",  # collector._collect_git_metrics
}


#: Metrics whose `value` is a transformed score (inverted, scaled or normalised) with no raw measure mapped above. A written
#: operator on one cannot be honoured: on the score, `radon_avg_complexity@<=10` passed at complexity 25 and
#: `test_coverage_percent@>=80` failed at 92% (second review pass, 2026-11-18). Such a criterion is skipped, visibly.
SCORE_VALUED_METRICS = frozenset(
    {"radon_avg_complexity", "test_coverage_percent", "mistake_ratio", "ruff_error_ratio", "loc_delta", "files_changed"}
)


def _named_measure(evidence, metric: str) -> float | None:
    """The quantity `metric`'s name promises, read from its raw_value; None when the metric is not one of these or the measure is absent.

    A clean ruff run carries `{"violations": 0}` and no per-100 figure: its density is 0."""
    key = NAMED_MEASURE_KEYS.get(metric)
    raw = evidence.raw(metric) if key else None
    if not isinstance(raw, dict):
        return None
    v = raw.get(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if raw.get("violations") == 0:
        return 0.0
    return None


def _compare(value: float, threshold: float, operator: str | None, direction: str) -> tuple[bool, str]:
    """Compare with the author's operator; a criterion without one (dict form, stored before the field existed) infers it from direction."""
    if operator not in ("<=", ">="):
        operator = "<=" if direction == "lower_is_better" else ">="
    return (value <= threshold if operator == "<=" else value >= threshold), operator


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
                    passed=_compare(1.0, threshold, getattr(ctx.criterion, "operator", None), "higher_is_better")[0],
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
        passed, op = _compare(ratio, threshold, getattr(ctx.criterion, "operator", None), "higher_is_better")
        return CriterionResult(
            criterion_id=ctx.criterion.id,
            goal_id=ctx.goal.id,
            validation_method=self.validation_method,
            passed=passed,
            value=ratio,
            threshold=threshold,
            summary=f"task completion {ratio:.0%} {op} {threshold:.0%}",
            iteration_needed=(not passed and ctx.criterion.is_required),
            next_transaction="Complete remaining required tasks" if not passed else None,
        )


class EvidenceMetricEvaluator:
    """Evaluate `quality_gate` criteria against a named metric in EvidenceBundle.

    The criterion's `description` field carries the metric name to look up
    (e.g. "prose_stylometry_composite_drift", "ruff_violation_density"). The
    evaluator reads it from the bundle and compares it against the criterion's
    threshold with the criterion's own operator when it has one.

    Threshold semantics:
      - operator "<=" / ">=" (written as "@<=" / "@>="): used as written
      - no operator (legacy criteria): inferred from the metric's direction —
        higher_is_better passes when value >= threshold, lower_is_better when <=

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
        return ctx.evidence.has(ctx.criterion.description) and not _declared_partial(
            ctx.evidence, ctx.criterion.description
        )

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
        operator = getattr(ctx.criterion, "operator", None)
        measured = metric
        if operator in ("<=", ">=") and metric in NAMED_MEASURE_KEYS:
            # A written operator is about the quantity the name promises, not the score stored as `value`:
            # on the score, `ruff_violation_density@<=0.25` failed clean code (1.0) and passed bad code.
            value = _named_measure(ctx.evidence, metric)
            measured = f"{metric}[{NAMED_MEASURE_KEYS[metric]}]"
            if value is None:
                return CriterionResult(
                    criterion_id=ctx.criterion.id,
                    goal_id=ctx.goal.id,
                    validation_method=self.validation_method,
                    passed=False,
                    skipped=True,
                    threshold=threshold,
                    summary=f"metric {metric!r} carries no {NAMED_MEASURE_KEYS[metric]!r} measure to compare",
                )
        if operator in ("<=", ">=") and metric in SCORE_VALUED_METRICS:
            return CriterionResult(
                criterion_id=ctx.criterion.id,
                goal_id=ctx.goal.id,
                validation_method=self.validation_method,
                passed=False,
                skipped=True,
                threshold=threshold,
                summary=(
                    f"{metric!r} is stored as a normalised score, so '@{operator}{threshold}' cannot be read as written; "
                    "a threshold on the score itself goes in the dict form {description, validation_method, threshold}"
                ),
            )
        passed, op_repr = _compare(value, threshold, operator, direction)

        return CriterionResult(
            criterion_id=ctx.criterion.id,
            goal_id=ctx.goal.id,
            validation_method=self.validation_method,
            passed=passed,
            value=value,
            threshold=threshold,
            summary=f"{measured}={value:.3f} {op_repr} {threshold:.3f} ({direction})",
            iteration_needed=(not passed and ctx.criterion.is_required),
            next_transaction=f"Address {metric} regression" if not passed else None,
        )


def _declared_partial(evidence, metric: str) -> bool:
    """True when the evidence for `metric` says it covers only part of what a criterion asks about (a `-k` rerun, a truncated JUnit read).

    A 3-test green rerun is not "the tests pass". Shared by every method that reads a metric by name, because `quality_gate` names
    `test_pass_rate` by its description and reaches the same evidence under another method name."""
    raw = evidence.raw(metric)
    scope = raw.get("scope") if isinstance(raw, dict) else None
    return isinstance(scope, str) and scope.startswith("partial:")


class BundleMetricThresholdEvaluator:
    """Shared engine for methods that are one named bundle metric vs a threshold.

    `tests_pass` and `committed` are corpus-derived: a regex pass over the 2,473
    authored criteria found test/suite language in 18% and commit/ship language
    in 4% — people were already trying to SAY these, with no method to say them
    in. Each maps onto evidence the collector already gathers, so the evaluator
    is a lookup, not new measurement machinery.

    Two derived axes were deliberately NOT added:
    - `count_threshold` IS `quality_gate` (a named metric vs a threshold) — a
      synonym would be one question wearing two names, the inverse of the
      one-predicate-two-questions defect this widening fixes.
    - `no_regression` means "no worse than BASELINE", and no baseline machinery
      exists. Mapping it onto an absolute threshold would answer a different
      question under the criterion's name — the exact over-claiming applies()
      was just narrowed to remove.

    `applies()` requires the metric to be present in the bundle, so an absent
    evidence source (pytest report not configured, work outside a git repo)
    surfaces as SKIPPED with the registry's did-not-apply reason — never as a
    pass, never as a confident wrong answer.
    """

    def __init__(self, validation_method: str, metric: str, default_threshold: float, direction: str):
        self.validation_method = validation_method
        self._metric = metric
        self._default_threshold = default_threshold
        self._direction = direction

    def applies(self, ctx: CriterionContext) -> bool:
        return ctx.evidence.has(self._metric) and not _declared_partial(ctx.evidence, self._metric)

    def evaluate(self, ctx: CriterionContext) -> CriterionResult:
        threshold = ctx.criterion.threshold if ctx.criterion.threshold is not None else self._default_threshold
        value = _named_measure(ctx.evidence, self._metric)
        if value is None:
            value = ctx.evidence.get(self._metric)
        if value is None:  # has() raced or returned a non-scalar — skip honestly
            return CriterionResult(
                criterion_id=ctx.criterion.id,
                goal_id=ctx.goal.id,
                validation_method=self.validation_method,
                passed=False,
                skipped=True,
                threshold=threshold,
                summary=f"metric {self._metric!r} present but carries no scalar value",
            )
        passed, op = _compare(value, threshold, getattr(ctx.criterion, "operator", None), self._direction)
        return CriterionResult(
            criterion_id=ctx.criterion.id,
            goal_id=ctx.goal.id,
            validation_method=self.validation_method,
            passed=passed,
            value=value,
            threshold=threshold,
            summary=f"{self._metric}={value:g} {op} {threshold:g}",
            iteration_needed=(not passed and ctx.criterion.is_required),
            next_transaction=f"{self.validation_method} criterion unmet ({self._metric}={value:g})"
            if not passed
            else None,
        )


class ArtifactExistsEvaluator:
    """Evaluate `artifact_exists` criteria: the description IS a file path.

    Same contract shape as `quality_gate` (whose description IS a metric name):
    the description carries the operand, not prose about it. Relative paths
    resolve against the current working directory — POSTFLIGHT runs at the
    project root.

    3% of authored criteria were file/artifact-existence claims with no way to
    check them. This is the only new evaluator that measures something the
    evidence bundle does not carry, and the measurement is a stat call.
    """

    validation_method = "artifact_exists"

    def applies(self, ctx: CriterionContext) -> bool:
        # A path has no spaces-with-sentence shape; a prose sentence stuffed in
        # here should skip, not be stat()ed as a filename.
        desc = (ctx.criterion.description or "").strip()
        return bool(desc) and "\n" not in desc and len(desc.split()) == 1

    def evaluate(self, ctx: CriterionContext) -> CriterionResult:
        from pathlib import Path

        target = Path((ctx.criterion.description or "").strip())
        exists = target.exists()
        return CriterionResult(
            criterion_id=ctx.criterion.id,
            goal_id=ctx.goal.id,
            validation_method=self.validation_method,
            passed=exists,
            value=1.0 if exists else 0.0,
            summary=f"{target} {'exists' if exists else 'MISSING'}",
            iteration_needed=(not exists and ctx.criterion.is_required),
            next_transaction=f"Produce {target}" if not exists else None,
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

# Corpus-derived methods over existing evidence. Metric names verified against
# the collector's emitted metric_name= set, not assumed.
register(BundleMetricThresholdEvaluator("tests_pass", "test_pass_rate", 1.0, "higher_is_better"))
register(BundleMetricThresholdEvaluator("committed", "commit_count", 1.0, "higher_is_better"))
register(ArtifactExistsEvaluator())
