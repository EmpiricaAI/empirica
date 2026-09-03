"""An unchecked success criterion must not be able to look like a met one.

Measured 2026-09-03 across 59 practice databases: 5,462 of 5,484 criteria (99.6%)
carried ``validation_method: completion``, of which 2,473 were AUTHORED
conditions — *SSH password auth disabled*, *Enterprise TURTLE gated* — that no
subtask ratio can judge. And ``SubtaskCompletionEvaluator.applies()`` was
``return True``, so it claimed every one of them.

The field was degenerate, not unused: it held one value because there was one
choice. A practitioner obeying *never re-emit the default* had two moves, both
lies — stamp ``completion`` (which the evaluator acts on) or ``metric_threshold``
(which validates, has no registered evaluator, and reports a pass).

Three fixes, tested here:

1. ``completion`` only claims criteria it can judge.
2. ``prose`` / ``undetermined`` exist, and skip with an explicit reason.
3. A skipped result carries ``passed=False`` — nothing-happened is not success.
"""

from __future__ import annotations

import pytest

from empirica.core.goals.types import SuccessCriterion
from empirica.core.goals.validation import VALID_VALIDATION_METHODS, ValidationError, validate_success_criteria
from empirica.core.post_test.criterion_evaluators.builtin import (
    AUTO_FILLED_CRITERION,
    ProseCriterionEvaluator,
    SubtaskCompletionEvaluator,
    _is_completion_shaped,
)


def _crit(desc, method="completion", threshold=None):
    return SuccessCriterion(id="c1", description=desc, validation_method=method, threshold=threshold)


# ── the vocabulary ───────────────────────────────────────────────────────────


def test_prose_and_undetermined_are_valid_methods():
    """THE regression. Authoring a goal for this very work, `prose` was rejected —
    so the protocol's own constraint (unasked and unanswerable must not look the
    same) was unsatisfiable in the tool that implements it."""
    validate_success_criteria([_crit("a human reads this one", method="prose")])
    validate_success_criteria([_crit("checkability not yet decided", method="undetermined")])


def test_the_old_methods_still_validate():
    """NEGATIVE CONTROL. Widening must not invalidate the 5,462 existing rows."""
    validate_success_criteria([_crit(AUTO_FILLED_CRITERION)])
    validate_success_criteria([_crit("ruff_violation_density", method="quality_gate", threshold=0.0)])


def test_an_unknown_method_is_still_rejected():
    """The set is widened, not opened. A typo must not become a silent skip."""
    with pytest.raises(ValidationError):
        validate_success_criteria([_crit("x", method="nonsense")])


def test_both_validators_read_ONE_vocabulary():
    """The list was hand-maintained in two places — the CLI validator and the MCP
    validator. Widening one and not the other would make a criterion legal over
    one surface and rejected over the other, which is the two-sources-of-truth
    shape this repo keeps producing. Asserted by reading the source: no literal
    method list may survive alongside the constant."""
    import inspect

    import empirica.core.goals.validation as mod

    src = inspect.getsource(mod)
    body = src[src.index("class ValidationError") :]
    assert '"completion", "quality_gate", "metric_threshold"]' not in body, (
        "a second hand-maintained copy of the vocabulary has reappeared"
    )
    assert body.count("VALID_VALIDATION_METHODS") >= 2, "both validators must read the constant"


# ── completion stops over-claiming ───────────────────────────────────────────


def test_completion_declines_an_authored_prose_criterion():
    """THE regression, and the 99.6% case. A criterion reading *SSH password auth
    disabled* had its completion judged by counting subtasks."""
    assert not _is_completion_shaped(_crit("SSH password auth disabled"))
    assert not _is_completion_shaped(_crit("Enterprise TURTLE gated"))
    assert not _is_completion_shaped(_crit("Pro/Team tiers working"))


def test_completion_still_claims_the_auto_filled_sentinel():
    """POSITIVE CONTROL, and the majority of real traffic — 3,011 of 5,484 rows.
    Declining these would make the narrowing a regression, not a fix."""
    assert _is_completion_shaped(_crit(AUTO_FILLED_CRITERION))
    assert _is_completion_shaped(_crit(f"{AUTO_FILLED_CRITERION} for goal X"))


def test_completion_claims_a_criterion_that_names_the_metric():
    assert _is_completion_shaped(_crit("subtask_ratio"))
    assert _is_completion_shaped(_crit("task_completion"))


def test_completion_claims_a_criterion_carrying_an_explicit_threshold():
    """An author who set a number was thinking in ratios, not prose."""
    assert _is_completion_shaped(_crit("most of the work done", threshold=0.8))


def test_applies_delegates_to_the_shape_check(monkeypatch):
    """The evaluator's gate and the shape predicate must not drift apart."""

    class _Ctx:
        criterion = _crit("SSH password auth disabled")

    assert not SubtaskCompletionEvaluator().applies(_Ctx())


# ── prose evaluates to an honest nothing ─────────────────────────────────────


class _Goal:
    id = "g1"


class _Ctx:
    def __init__(self, crit):
        self.criterion = crit
        self.goal = _Goal()


def test_prose_skips_and_does_not_pass():
    """A criterion nobody can check must not read as a met one."""
    r = ProseCriterionEvaluator().evaluate(_Ctx(_crit("a human reads this", method="prose")))

    assert r.skipped is True
    assert r.passed is False
    assert "PROSE" in r.summary


def test_undetermined_says_something_DIFFERENT_from_prose():
    """The two are not synonyms: prose is a decision, undetermined is a deferral.
    Collapsing their summaries would relocate the silence rather than remove it."""
    ev = ProseCriterionEvaluator()
    ev.validation_method = "undetermined"
    r = ev.evaluate(_Ctx(_crit("not yet decided", method="undetermined")))

    assert r.skipped is True
    assert r.passed is False
    assert "UNDETERMINED" in r.summary
    assert r.validation_method == "undetermined"


def test_both_methods_are_actually_registered():
    """POSITIVE CONTROL on registration. An evaluator defined but never wired
    would leave `dispatch` reporting 'no evaluator registered' — which reads as a
    misconfiguration, exactly the ambiguity this class removes."""
    from empirica.core.post_test.criterion_evaluators.registry import _EVALUATORS

    assert "prose" in _EVALUATORS
    assert "undetermined" in _EVALUATORS


# ── a skipped dispatch is not a pass ─────────────────────────────────────────


def test_an_unmatched_method_does_not_report_passed():
    """It returned `passed=True, skipped=True`. The one in-tree consumer branches
    on `skipped` first so nothing was wrong today — but `to_dict()` ships that
    field to dashboards and peers who never see that branch."""
    from empirica.core.post_test.criterion_evaluators.registry import dispatch

    class _C:
        criterion = _crit("x", method="metric_threshold", threshold=1.0)
        goal = _Goal()

    r = dispatch(_C())

    assert r.skipped is True
    assert r.passed is False, "an unchecked criterion is not a met one"
    assert "metric_threshold" in r.summary


def test_metric_threshold_is_still_advertised_but_has_no_evaluator():
    """Recorded rather than fixed: `metric_threshold` validates and dispatches to
    nothing. It is now at least honestly SKIPPED rather than reported as passed.
    Removing it from the vocabulary would break the criteria already using it, so
    that call is deferred — this test pins the current state so the deferral
    stays visible instead of becoming folklore."""
    from empirica.core.post_test.criterion_evaluators.registry import _EVALUATORS

    assert "metric_threshold" in VALID_VALIDATION_METHODS
    assert "metric_threshold" not in _EVALUATORS
