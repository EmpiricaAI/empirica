"""Goal-driven post-test criterion evaluators.

Bridges `goals.success_criteria` rows into the POSTFLIGHT pipeline so that
declared criteria become live checks against collected evidence.

Public API:
- CriterionContext, CriterionResult, CriterionEvaluator: shapes
- register / dispatch: registry plumbing
- evaluate_goal_criteria: top-level orchestrator called from POSTFLIGHT

Built-in evaluators auto-register on import of this package.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

# Auto-register built-ins on package import. Side-effect import — module-level
# register() calls are what we want, not the names.
from . import builtin  # type: ignore[unused-import]
from ._types import CriterionContext, CriterionEvaluator, CriterionResult
from .registry import dispatch, register

del builtin

if TYPE_CHECKING:
    from empirica.core.post_test.collector import EvidenceBundle

logger = logging.getLogger(__name__)


def evaluate_goal_criteria(
    session_id: str,
    evidence: EvidenceBundle,
    transaction_id: str | None = None,
    project_id: str | None = None,
    postflight_vectors: dict[str, float] | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Evaluate active goal criteria for a session against POSTFLIGHT evidence.

    Loads active (non-completed, non-planned) goals' criteria from the goal
    repository, dispatches each to its registered evaluator, and persists the
    is_met flag back to the database. Returns a structured block suitable for
    inclusion in the POSTFLIGHT response.

    Failures are logged and absorbed — never raised. Goal criteria are
    advisory checks, not hard blockers.
    """
    block: dict[str, Any] = {
        "evaluated": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "results": [],
        "iteration_needed": False,
    }

    try:
        from empirica.core.goals.repository import GoalRepository

        repo = GoalRepository(db_path=db_path)
        pairs = repo.list_active_criteria_for_session(session_id)
    except Exception as e:
        logger.debug(f"Failed to load active criteria for session {session_id}: {e}")
        return block

    if not pairs:
        return block

    iteration_needed = False
    for goal, criterion in pairs:
        ctx = CriterionContext(
            criterion=criterion,
            goal=goal,
            evidence=evidence,
            session_id=session_id,
            project_id=project_id,
            transaction_id=transaction_id,
            postflight_vectors=postflight_vectors,
        )
        result = dispatch(ctx)
        block["results"].append(result.to_dict())
        block["evaluated"] += 1
        if result.skipped:
            block["skipped"] += 1
        elif result.passed:
            block["passed"] += 1
        else:
            block["failed"] += 1
        if result.iteration_needed:
            iteration_needed = True

        # Persist is_met for non-skipped results so external readers
        # (goals-complete strict mode, future dashboards) see live state.
        if not result.skipped:
            repo.update_is_met(criterion.id, result.passed)

    block["iteration_needed"] = iteration_needed
    return block


__all__ = [
    "CriterionContext",
    "CriterionEvaluator",
    "CriterionResult",
    "dispatch",
    "evaluate_goal_criteria",
    "register",
]
