"""Evaluator registry — keyed on validation_method.

Multiple evaluators per method are allowed; first applicable wins. Unmatched
methods return a skipped CriterionResult so the response surface stays
predictable even if a goal declares an unknown method.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ._types import CriterionContext, CriterionEvaluator, CriterionResult

logger = logging.getLogger(__name__)

_EVALUATORS: dict[str, list[CriterionEvaluator]] = defaultdict(list)


def register(evaluator: CriterionEvaluator) -> None:
    """Register an evaluator. Call at module import time from builtin.py."""
    _EVALUATORS[evaluator.validation_method].append(evaluator)
    logger.debug(
        "Registered criterion evaluator: %s for validation_method=%r",
        type(evaluator).__name__,
        evaluator.validation_method,
    )


def dispatch(ctx: CriterionContext) -> CriterionResult:
    """Find first applicable evaluator for the criterion's validation_method.

    Returns a skipped result if no evaluator applies — never raises.
    Distinguishes between "no evaluator registered" and "registered but
    none applied" so the summary is diagnosable instead of misleading.
    """
    method = ctx.criterion.validation_method
    candidates = _EVALUATORS.get(method, [])
    for evaluator in candidates:
        try:
            if evaluator.applies(ctx):
                return evaluator.evaluate(ctx)
        except Exception as e:
            logger.debug(
                "Evaluator %s raised on criterion %s: %s",
                type(evaluator).__name__,
                ctx.criterion.id,
                e,
            )
            return CriterionResult(
                criterion_id=ctx.criterion.id,
                goal_id=ctx.goal.id,
                validation_method=method,
                passed=False,
                skipped=True,
                summary=f"Evaluator {type(evaluator).__name__} raised: {type(e).__name__}",
            )

    if not candidates:
        summary = f"No evaluator registered for validation_method={method!r}"
    else:
        names = ", ".join(type(e).__name__ for e in candidates)
        summary = (
            f"Registered evaluator(s) for {method!r} did not apply ({names}) "
            f"— required input absent (e.g. metric not in evidence bundle)"
        )

    # `passed=False` on a skipped result, deliberately. It used to be True, and
    # while the one in-tree consumer branches on `skipped` first (so nothing was
    # wrong today), a nothing-happened outcome carrying `passed: True` is a
    # landmine for the next reader — and it ships in `to_dict()`, so it reaches
    # dashboards and peers who never see this branch. An unchecked criterion is
    # not a met one; the honest encoding is skipped=True, passed=False.
    return CriterionResult(
        criterion_id=ctx.criterion.id,
        goal_id=ctx.goal.id,
        validation_method=method,
        passed=False,
        skipped=True,
        summary=summary,
    )


def reset_for_tests() -> None:
    """Clear all registered evaluators. Tests use this to isolate registry state."""
    _EVALUATORS.clear()
