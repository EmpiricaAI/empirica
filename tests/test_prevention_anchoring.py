"""A rate may not be published over verdicts the predicate could not discriminate.

`prevention_report` emitted `prevention_rate` over a population whose verdicts
were manufactured: 204 events on this practice, all with `goal_id` NULL, 202
resolved `failed`, `prevented` recorded zero times ever. The `failed` came from
`detection.py`'s session-wide branch, which asks *was any mistake or dead-end
logged in this session after exposure* — near-certainly true for anyone logging
mistakes at a normal rate.

I nearly cited that 202/204 to two peers as evidence for a hypothesis I already
held. That is what this gate exists to prevent, and it is why the gate fails
CLOSED while every sibling module in the package fails open: a measurement path
that breaks should degrade to empty, but a gate that breaks must withhold.

The rate keys are REMOVED rather than nulled. `None` reads as *measured, and the
answer is nothing*; an absent key reads as *not measured*. Those are different
claims and the distinction is the entire deliverable.
"""

from __future__ import annotations

from empirica.core.prevention.anchoring import anchoring_verdict, strip_unanchored_rates


def _row(**over):
    base = {
        "outcome": "failed",
        "goal_id": None,
        "subject_key": "session:7cd010f9",
        "pattern_key": "p",
        "outcome_family": "prevention",
    }
    base.update(over)
    return base


# ── the measured case ────────────────────────────────────────────────────────


def test_todays_real_shape_is_unanchored():
    """204 patterns, one session-shaped subject, no bound goals."""
    rows = [_row(pattern_key=f"p{i}") for i in range(204)]
    v = anchoring_verdict(rows)
    assert v["anchored"] is False
    assert "no bound subject" in v["reason"]
    assert v["subjectless"] == 204


def test_a_bound_subject_anchors_it():
    """The post-P1 shape: goals bound, subjects distinct."""
    rows = [_row(goal_id=f"g{i}", subject_key=f"goal:g{i}", pattern_key=f"p{i}") for i in range(5)]
    v = anchoring_verdict(rows)
    assert v["anchored"] is True
    assert v["reason"] is None
    assert v["distinct_subjects"] == 5


def test_many_patterns_on_one_subject_is_unanchored_even_when_bound():
    """The second defect survives the first fix.

    Binding goals is not enough if every pattern still adjudicates against the
    same subject — that is the collapse a peer measured as 167 patterns on 3
    subjects, and 204 on 1 here.
    """
    rows = [_row(goal_id="g1", subject_key="goal:g1", pattern_key=f"p{i}") for i in range(20)]
    v = anchoring_verdict(rows)
    assert v["anchored"] is False
    assert "does not discriminate" in v["reason"]


# ── the properties that make it a gate rather than a report ──────────────────


def test_it_fails_closed_not_open():
    """Every sibling module fails open. A gate that does so publishes the number.

    Malformed rows must yield *withheld*, never *anchored*.
    """
    v = anchoring_verdict([{"outcome": object()}])  # not comparable, not a dict shape it expects
    assert v["anchored"] is False
    v2 = anchoring_verdict(None)
    assert v2["anchored"] is False


def test_no_resolved_events_is_unanchored_not_a_zero_rate():
    """An empty population has no rate — reporting 0.0 would assert a measurement."""
    v = anchoring_verdict([_row(outcome="exposed") for _ in range(3)])
    assert v["anchored"] is False
    assert "nothing to compute a rate over" in v["reason"]


def test_unmeasurable_is_not_a_verdict():
    """P3's third outcome must not be counted as resolved once it lands."""
    v = anchoring_verdict([_row(outcome="unmeasurable") for _ in range(4)])
    assert v["resolved"] == 0
    assert v["anchored"] is False


# ── absence vs null ──────────────────────────────────────────────────────────


def test_rates_are_removed_not_nulled():
    """The whole point: absent means not-measured, null means measured-as-nothing."""
    agg = {
        "total": 202,
        "by_outcome": {"failed": 202},
        "prevention_rate": 0.0,
        "beneficiary_independent": 0,
        "beneficiary_independent_rate": None,
    }
    out = strip_unanchored_rates(agg)
    assert "prevention_rate" not in out
    assert "beneficiary_independent_rate" not in out
    assert out["total"] == 202, "counts survive — they are observations, not verdicts"
    assert out["by_outcome"] == {"failed": 202}


def test_stripping_is_total_across_rate_keys():
    """Any future *_rate key is covered by construction, not by enumeration."""
    out = strip_unanchored_rates({"a_rate": 1, "b_rate": 2, "keep": 3})
    assert out == {"keep": 3}
