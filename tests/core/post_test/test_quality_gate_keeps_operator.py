"""A quality_gate criterion is evaluated with the operator its author wrote.

`_parse_criterion_entry` discarded the operator and the evaluator inferred one
from the metric's declared direction. Metrics named *_density / *_violations
carry a higher-is-better score, so `ruff_violation_density@<=0.25` was evaluated
as `>= 0.25` and a value of 0.9 passed a gate that asked for at most 0.25.

Test items mirror their producer's shape: prose_stylometry_composite_drift is a scalar raw_value with
direction lower_is_better (prose_collector); ruff_violation_density keeps its score in `value` and a dict
in `raw_value` (collector._collect_ruff_evidence). How a written operator reads that dict is pinned in
test_criterion_operator_semantics.py.
"""

from __future__ import annotations

from empirica.cli.command_handlers.goal_commands import _parse_criterion_entry
from empirica.core.goals.types import Goal
from empirica.core.post_test.collector import EvidenceBundle, EvidenceItem, EvidenceQuality
from empirica.core.post_test.criterion_evaluators.registry import dispatch


def _drift(v: float) -> EvidenceBundle:
    item = EvidenceItem(
        source="prose_stylometry",
        metric_name="prose_stylometry_composite_drift",
        value=v,
        raw_value=v,
        quality=EvidenceQuality.OBJECTIVE,
        supports_vectors=["coherence", "signal", "clarity"],
        direction="lower_is_better",
    )
    return EvidenceBundle(session_id="s1", items=[item])


def _ruff_score(score: float) -> EvidenceBundle:
    item = EvidenceItem(
        source="code_quality",
        metric_name="ruff_violation_density",
        value=score,
        raw_value={"violations": 1, "lines": 100, "per_100_lines": round((1.0 - score) * 10.0, 2), "files_checked": 1},
        quality=EvidenceQuality.SEMI_OBJECTIVE,
        supports_vectors=["clarity", "coherence"],
    )
    return EvidenceBundle(session_id="s1", items=[item])


class _Goal:
    id = "g1"


class _Ctx:
    def __init__(self, crit, bundle):
        self.criterion = crit
        self.goal = _Goal()
        self.evidence = bundle


def test_at_most_is_evaluated_as_at_most():
    crit = _parse_criterion_entry("quality_gate:prose_stylometry_composite_drift@<=0.25")
    assert crit.operator == "<="
    r = dispatch(_Ctx(crit, _drift(0.9)))
    assert r.skipped is False
    assert r.passed is False
    assert dispatch(_Ctx(crit, _drift(0.1))).passed is True


def test_a_written_at_least_overrides_the_declared_direction():
    crit = _parse_criterion_entry("quality_gate:prose_stylometry_composite_drift@>=0.25")
    assert crit.operator == ">="
    assert dispatch(_Ctx(crit, _drift(0.9))).passed is True
    assert dispatch(_Ctx(crit, _drift(0.1))).passed is False


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
    assert dispatch(_Ctx(crit, _ruff_score(0.9))).passed is True  # the score, as before the field existed
