"""A quality_gate criterion is evaluated with the operator its author wrote.

`_parse_criterion_entry` discarded the operator and the evaluator inferred one
from the metric's declared direction. Metrics named *_density / *_violations
carry a higher-is-better score, so `ruff_violation_density@<=0.25` was evaluated
as `>= 0.25` and a value of 0.9 passed a gate that asked for at most 0.25.
"""

from __future__ import annotations

from empirica.cli.command_handlers.goal_commands import _parse_criterion_entry
from empirica.core.goals.types import Goal
from empirica.core.post_test.collector import EvidenceBundle, EvidenceItem, EvidenceQuality
from empirica.core.post_test.criterion_evaluators.registry import dispatch


def _bundle(**metrics):
    items = [
        EvidenceItem(
            source="code_quality",
            metric_name=k,
            value=float(v),
            raw_value=v,
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=[],
        )
        for k, v in metrics.items()
    ]
    return EvidenceBundle(session_id="s1", items=items)


class _Goal:
    id = "g1"


class _Ctx:
    def __init__(self, crit, bundle):
        self.criterion = crit
        self.goal = _Goal()
        self.evidence = bundle


def test_at_most_is_evaluated_as_at_most():
    crit = _parse_criterion_entry("quality_gate:ruff_violation_density@<=0.25")
    assert crit.operator == "<="
    r = dispatch(_Ctx(crit, _bundle(ruff_violation_density=0.9)))
    assert r.skipped is False
    assert r.passed is False
    r = dispatch(_Ctx(crit, _bundle(ruff_violation_density=0.1)))
    assert r.passed is True


def test_at_least_still_works():
    crit = _parse_criterion_entry("quality_gate:ruff_violation_density@>=0.25")
    assert crit.operator == ">="
    assert dispatch(_Ctx(crit, _bundle(ruff_violation_density=0.9))).passed is True
    assert dispatch(_Ctx(crit, _bundle(ruff_violation_density=0.1))).passed is False


def test_the_operator_survives_the_goal_round_trip():
    """Criteria live in the goal_data blob; the operator must be in it or it is lost on read."""
    crit = _parse_criterion_entry("quality_gate:ruff_violation_density@<=0.25")
    goal = Goal.create(objective="o", success_criteria=[crit])
    back = Goal.from_dict(goal.to_dict())
    assert back.success_criteria[0].operator == "<="


def test_a_criterion_without_an_operator_keeps_direction_inference():
    """Legacy criteria (dict or stored before the field existed) have no operator."""
    crit = _parse_criterion_entry(
        {"description": "ruff_violation_density", "validation_method": "quality_gate", "threshold": 0.25}
    )
    assert crit.operator is None
    assert dispatch(_Ctx(crit, _bundle(ruff_violation_density=0.9))).passed is True
