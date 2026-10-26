"""CHECK's diminishing-returns detector, pinned (goal c69effce).

`_check_detect_diminishing_returns` decides whether repeated CHECK rounds have stopped improving, and its `recommend_proceed` is one of the two
overrides that turn the CHECK gate from `investigate` to `proceed` (the other is the round-5 rule). The mutation pass over the CHECK handler
(finding 67545968) let 11 of 18 sampled mutants of it survive: nothing pinned which deltas count, when know is "stagnant", what "uncertainty not
decreasing" tolerates, or where the proceed override stops. `previous_check_vectors` is newest first; the current round is `know`, `uncertainty`.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers._workflow_check import _check_detect_diminishing_returns as detect


def _prev(*pairs):
    """previous_check_vectors, newest first, from (know, uncertainty) pairs."""
    return [{"know": k, "uncertainty": u} for k, u in pairs]


def test_fewer_than_two_previous_rounds_is_never_a_verdict():
    for previous in ([], _prev((0.7, 0.4))):
        out = detect(previous, 0.7, 0.4)
        assert out["detected"] is False and out["recommend_proceed"] is False
        assert out["rounds_analyzed"] == 0 and out["know_deltas"] == [] and out["uncertainty_deltas"] == []
        assert out["reason"] is None


def test_the_deltas_are_current_minus_previous_then_each_round_minus_the_one_before_newest_first():
    out = detect(_prev((0.60, 0.50), (0.50, 0.55)), 0.65, 0.40)
    assert out["know_deltas"] == pytest.approx([0.05, 0.10])  # 0.65-0.60, 0.60-0.50
    assert out["uncertainty_deltas"] == pytest.approx([-0.10, -0.05])  # 0.40-0.50, 0.50-0.55
    assert out["rounds_analyzed"] == 3


def test_a_plateau_with_acceptable_uncertainty_recommends_proceeding():
    out = detect(_prev((0.70, 0.40), (0.70, 0.40)), 0.70, 0.40)
    assert out["detected"] is True and out["recommend_proceed"] is True
    assert "know stagnant" in out["reason"] and "uncertainty acceptable" in out["reason"]


def test_a_plateau_with_uncertainty_too_high_is_detected_but_does_not_override():
    out = detect(_prev((0.70, 0.60), (0.70, 0.60)), 0.70, 0.60)
    assert out["detected"] is True and out["recommend_proceed"] is False
    assert "uncertainty too high" in out["reason"]


@pytest.mark.parametrize(("uncertainty", "proceeds"), [(0.45, True), (0.46, False), (0.30, True), (0.90, False)])
def test_the_proceed_override_stops_above_point_four_five(uncertainty, proceeds):
    out = detect(_prev((0.70, uncertainty), (0.70, uncertainty)), 0.70, uncertainty)
    assert out["detected"] is True and out["recommend_proceed"] is proceeds


@pytest.mark.parametrize(
    ("know_step", "stagnant"),
    [(0.00, True), (0.04, True), (-0.04, True), (0.06, False), (-0.06, False), (0.30, False)],
)
def test_know_is_stagnant_only_while_the_newest_move_is_small_in_either_direction(know_step, stagnant):
    out = detect(_prev((0.70, 0.40), (0.70, 0.40)), 0.70 + know_step, 0.40)
    assert out["detected"] is stagnant


def test_one_big_know_move_in_the_older_of_the_two_recent_deltas_also_breaks_the_plateau():
    out = detect(_prev((0.70, 0.40), (0.50, 0.40)), 0.70, 0.40)  # deltas: 0.00 then +0.20
    assert out["detected"] is False


@pytest.mark.parametrize(
    ("unc_step", "stagnant"),
    [(0.00, True), (-0.04, True), (0.10, True), (0.40, True), (-0.06, False), (-0.30, False)],
)
def test_uncertainty_counts_as_not_decreasing_unless_it_fell_by_more_than_point_oh_five(unc_step, stagnant):
    out = detect(_prev((0.70, 0.40), (0.70, 0.40)), 0.70, 0.40 + unc_step)
    assert out["detected"] is stagnant


def test_only_the_two_most_recent_deltas_are_judged():
    # the oldest delta (0.30 -> 0.70) is huge and must not matter
    out = detect(_prev((0.70, 0.40), (0.70, 0.40), (0.30, 0.90)), 0.70, 0.40)
    assert out["detected"] is True
    assert out["rounds_analyzed"] == 4 and len(out["know_deltas"]) == 3


def test_a_round_that_did_not_assess_know_or_uncertainty_contributes_no_delta():
    """A missing value used to read as 0.5 and rounds of unassessed vectors looked like stagnation."""
    out = detect(_prev((None, None), (0.70, 0.40)), 0.70, 0.40)
    assert len(out["know_deltas"]) < 2
    assert out["detected"] is False, "one real delta is not a plateau"
    assert out["rounds_analyzed"] == 3


def test_a_current_round_without_know_or_uncertainty_contributes_no_delta():
    out = detect(_prev((0.70, 0.40), (0.70, 0.40)), None, None)
    assert out["detected"] is False
    assert len(out["know_deltas"]) == 1 and len(out["uncertainty_deltas"]) == 1, (
        "only the older pair of rounds has both sides"
    )


def test_a_plateau_needs_two_deltas_on_BOTH_sides_not_just_one():
    """(`len(know) >= 2 and len(unc) >= 2` -> `or`): two stagnant know deltas with only one uncertainty delta is a half-observed plateau."""
    unc_short = detect(_prev((0.70, 0.40), (0.70, None)), 0.70, 0.40)
    assert len(unc_short["know_deltas"]) == 2 and len(unc_short["uncertainty_deltas"]) == 1
    assert unc_short["detected"] is False and unc_short["recommend_proceed"] is False
    know_short = detect(_prev((0.70, 0.40), (None, 0.40)), 0.70, 0.40)
    assert len(know_short["know_deltas"]) == 1 and len(know_short["uncertainty_deltas"]) == 2
    assert know_short["detected"] is False and know_short["recommend_proceed"] is False
