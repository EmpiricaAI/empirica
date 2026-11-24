#!/usr/bin/env python3
"""
Input Validation for Goal Architecture

Provides validation functions to ensure data integrity before database operations.
Designed to catch errors early and provide clear feedback.
"""

import logging

from .types import Goal, ScopeVector, SuccessCriterion

logger = logging.getLogger(__name__)

# The criterion vocabulary. ONE definition — this list was once hand-maintained in
# two places (the CLI validator and a since-removed MCP validator), the two-
# sources-of-truth shape that has bitten this repo repeatedly.
#
# `prose` and `undetermined` exist so an UNCHECKABLE done-condition can be
# recorded honestly. Without them the set forced a lie: measured 2026-09-03
# across 59 practice databases, 5,462 of 5,484 criteria (99.6%) carried
# `completion` — including 2,473 authored conditions like "SSH password auth
# disabled" that no subtask ratio can judge. That is not laziness, it is an empty
# option set. A practitioner obeying *never re-emit the default* had two moves:
# stamp `completion` (a lie the evaluator acts on) or `metric_threshold` (a lie
# that validates and silently reports a pass, having no registered evaluator).
#
#   prose        — deliberately not machine-checkable; a human reads it
#   undetermined — checkability not yet decided; revisit rather than assume
#
# Both dispatch to ProseCriterionEvaluator, which SKIPS with an explicit summary
# instead of passing. Unasked and unanswerable must not look the same, and
# neither may look like verified.
VALID_VALIDATION_METHODS: tuple[str, ...] = (
    "completion",
    "quality_gate",
    "metric_threshold",
    "prose",
    "undetermined",
    # Corpus-derived (a regex pass over the 2,473 authored criteria found people
    # already trying to say these): each maps onto evidence the collector
    # already gathers, so the evaluators are lookups, not new machinery.
    "tests_pass",  # test_pass_rate >= threshold (default 1.0)
    "committed",  # commit_count >= threshold (default 1)
    "artifact_exists",  # description IS a path; passes iff it exists
    # Deliberately absent: `count_threshold` (that IS quality_gate — a synonym
    # would be one question wearing two names) and `no_regression` (means "no
    # worse than BASELINE", and no baseline machinery exists — an absolute
    # threshold would answer a different question under the criterion's name).
)


class ValidationError(Exception):
    """Custom exception for validation errors"""

    pass


def validate_objective(objective: str) -> None:
    """
    Validate goal objective

    Args:
        objective: Goal objective string

    Raises:
        ValidationError: If objective is invalid
    """
    if not objective or not objective.strip():
        raise ValidationError("Objective cannot be empty")

    if len(objective) > 256:
        raise ValidationError(
            "Objective too long (max 256 characters — title-shaped). "
            "Use --description for the rich body (up to 8000 characters)."
        )


def validate_description(description: str | None) -> None:
    """Validate optional rich-body description.

    Empty/None is allowed — the field is optional (existing pre-migration-043
    rows have NULL description). When provided, capped at 8000 chars to keep
    embedding payloads bounded and display surfaces tractable.
    """
    if description is None:
        return
    if len(description) > 8000:
        raise ValidationError("Description too long (max 8000 characters)")


def validate_success_criteria(success_criteria: list[SuccessCriterion]) -> None:
    """
    Validate success criteria list

    Args:
        success_criteria: List of success criteria

    Raises:
        ValidationError: If criteria are invalid
    """
    if not success_criteria:
        raise ValidationError("At least one success criterion is required")

    valid_methods = list(VALID_VALIDATION_METHODS)

    for idx, sc in enumerate(success_criteria):
        # Validate description
        if not sc.description or not sc.description.strip():
            raise ValidationError(f"Success criterion {idx}: description cannot be empty")

        # Validate validation_method
        if sc.validation_method not in valid_methods:
            raise ValidationError(
                f"Success criterion {idx}: validation_method must be one of {valid_methods}, "
                f"got '{sc.validation_method}'"
            )

        # Validate threshold for metric-based criteria
        if sc.validation_method == "metric_threshold":
            if sc.threshold is None:
                raise ValidationError(f"Success criterion {idx}: threshold required for metric_threshold validation")
            if not (0.0 <= sc.threshold <= 1.0):
                raise ValidationError(
                    f"Success criterion {idx}: threshold must be between 0.0 and 1.0, got {sc.threshold}"
                )


def validate_complexity(complexity: float | None) -> None:
    """
    Validate complexity estimate

    Args:
        complexity: Complexity value (should be 0.0-1.0)

    Raises:
        ValidationError: If complexity is out of range
    """
    if complexity is not None:
        if not (0.0 <= complexity <= 1.0):
            raise ValidationError(f"Complexity must be between 0.0 and 1.0, got {complexity}")


def validate_scope_vector(scope: ScopeVector) -> None:
    """
    Validate scope vector and check coherence

    Args:
        scope: ScopeVector to validate

    Raises:
        ValidationError: If scope values are out of range

    Logs warnings for incoherent combinations (advisory, not blocking).
    """
    if not isinstance(scope, ScopeVector):
        raise ValidationError(f"scope must be ScopeVector, got {type(scope)}")

    # Ranges already validated by ScopeVector.__post_init__
    # Now check for coherence (advisory warnings, not blocking)

    if scope.coordination > 0.7 and scope.breadth < 0.4:
        logger.warning(
            f"Scope coherence: High coordination ({scope.coordination:.2f}) "
            f"with low breadth ({scope.breadth:.2f}) may indicate narrow multi-agent goal. "
            "Consider if coordination is truly needed."
        )

    if scope.duration > 0.8 and scope.breadth < 0.6:
        logger.warning(
            f"Scope coherence: Long duration ({scope.duration:.2f}) "
            f"with narrow breadth ({scope.breadth:.2f}) may indicate long-running single-task goal. "
            "Verify this is intentional."
        )

    if scope.breadth < 0.3 and scope.coordination > 0.7:
        logger.warning(
            f"Scope coherence: Very narrow breadth ({scope.breadth:.2f}) "
            f"with high coordination ({scope.coordination:.2f}) is unusual. "
            "Most narrow tasks don't need multi-agent coordination."
        )


def validate_goal(goal: Goal) -> None:
    """
    Validate complete goal object

    Args:
        goal: Goal to validate

    Raises:
        ValidationError: If goal is invalid
    """
    validate_objective(goal.objective)
    validate_success_criteria(goal.success_criteria)
    validate_complexity(goal.estimated_complexity)
    validate_scope_vector(goal.scope)
