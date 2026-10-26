"""The CHECK decision logic, pinned at its boundaries (goal c8e5c548, task R4).

A mutation pass over cli/command_handlers/_workflow_check.py (the CHECK handler) killed only 46 of 220 sampled mutants (21%). The functions
recent work had pinned were fully killed; the decision logic was not: `_compute_check_decision` let 7 of 7 sampled mutants survive,
`_check_gate_decision` 5 of 7, `_check_normalize_vectors` 10 of 10. These tests state the contract of those three, at the exact values where
a flipped comparison or an off-by-one changes the answer.
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.command_handlers import _workflow_check as W

NO_DIMINISHING = {"recommend_proceed": False, "reason": ""}


# ── _compute_check_decision: confidence >= 0.70, drift > 0.3, unknowns > 5 ──────────────────────────────────────


@pytest.mark.parametrize(
    ("confidence", "drift", "unknowns", "decision", "strength"),
    [
        (0.70, 0.0, 0, "proceed", "strong"),  # the confidence boundary is inclusive
        (0.6999, 0.0, 0, "investigate", "moderate"),
        (0.95, 0.30, 5, "proceed", "strong"),  # drift 0.3 and 5 unknowns are NOT over the line (strict >)
        (0.95, 0.31, 0, "proceed", "moderate"),
        (0.95, 0.0, 6, "proceed", "moderate"),
        (0.50, 0.30, 5, "investigate", "moderate"),  # low readiness, but neither drift nor unknowns is over the line
        (0.50, 0.31, 0, "investigate", "strong"),
        (0.50, 0.0, 6, "investigate", "strong"),
    ],
)
def test_the_decision_and_its_strength_at_every_boundary(confidence, drift, unknowns, decision, strength):
    got_decision, got_strength, reasoning, suggestions = W._compute_check_decision(confidence, drift, unknowns)
    assert (got_decision, got_strength) == (decision, strength)
    assert reasoning and suggestions, "every outcome explains itself"


def test_the_reasoning_states_the_numbers_it_decided_on():
    _, _, reasoning, suggestions = W._compute_check_decision(0.95, 0.31, 6)
    assert "6 unknowns" in reasoning and "0.31" in reasoning
    assert any("6 unknowns" in s for s in suggestions)


# ── _check_gate_decision: uncertainty alone decides, with two overrides ─────────────────────────────────────────


def _gate(uncertainty, *, threshold=0.35, round_num=1, decision=None, recommend=False):
    vectors = {} if uncertainty is None else {"uncertainty": uncertainty}
    diminishing = {"recommend_proceed": recommend, "reason": "flat"}
    return W._check_gate_decision(vectors, threshold, diminishing, round_num, decision)


def test_uncertainty_at_the_threshold_proceeds_and_above_it_investigates():
    assert _gate(0.35)[0] == "proceed"
    assert _gate(0.36)[0] == "investigate"


def test_diminishing_returns_override_proceeds_above_the_threshold():
    assert _gate(0.60, recommend=True)[0] == "proceed"
    assert _gate(0.60, recommend=False)[0] == "investigate"


@pytest.mark.parametrize(
    ("round_num", "uncertainty", "expected"),
    [(5, 0.40, "proceed"), (5, 0.41, "investigate"), (4, 0.40, "investigate"), (6, 0.40, "proceed")],
)
def test_the_fifth_round_proceeds_only_when_uncertainty_is_at_most_point_four(round_num, uncertainty, expected):
    assert _gate(uncertainty, round_num=round_num)[0] == expected


def test_a_missing_uncertainty_never_proceeds():
    """Whatever number stands in for it, 'not assessed' must not certify the transition."""
    assert _gate(None)[0] == "investigate"


def test_a_supplied_decision_is_kept_when_autopilot_is_off(monkeypatch):
    monkeypatch.delenv("EMPIRICA_AUTOPILOT_MODE", raising=False)
    decision, computed, autopilot, binding = _gate(0.9, decision="proceed")
    assert (decision, computed, autopilot, binding) == ("proceed", "investigate", False, False)


def test_no_supplied_decision_takes_the_computed_one(monkeypatch):
    monkeypatch.delenv("EMPIRICA_AUTOPILOT_MODE", raising=False)
    assert _gate(0.1)[0] == "proceed"
    assert _gate(0.9)[0] == "investigate"


@pytest.mark.parametrize("value", ["true", "1", "yes", "TRUE"])
def test_autopilot_overrides_a_disagreeing_decision_and_binds(monkeypatch, value):
    monkeypatch.setenv("EMPIRICA_AUTOPILOT_MODE", value)
    decision, computed, autopilot, binding = _gate(0.9, decision="proceed")
    assert (decision, computed, autopilot, binding) == ("investigate", "investigate", True, True)


@pytest.mark.parametrize("value", ["false", "0", "no", ""])
def test_autopilot_off_values_do_not_override(monkeypatch, value):
    monkeypatch.setenv("EMPIRICA_AUTOPILOT_MODE", value)
    decision, _computed, autopilot, binding = _gate(0.9, decision="proceed")
    assert (decision, autopilot, binding) == ("proceed", False, False)


# ── _check_normalize_vectors ────────────────────────────────────────────────────────────────────────────────────


def test_a_flat_dict_is_returned_as_given():
    flat = {"know": 0.8, "uncertainty": 0.2}
    assert W._check_normalize_vectors(flat) == flat


def test_a_json_string_is_parsed():
    assert W._check_normalize_vectors(json.dumps({"know": 0.8})) == {"know": 0.8}


def test_a_wrapped_vectors_dict_is_unwrapped():
    assert W._check_normalize_vectors({"vectors": {"know": 0.8}}) == {"know": 0.8}


def test_a_non_dict_vectors_value_is_not_unwrapped():
    """A 'vectors' key whose value is not a dict is just a key; the dict stays as given."""
    assert W._check_normalize_vectors({"vectors": 3, "know": 0.8}) == {"vectors": 3, "know": 0.8}


def test_grouped_vectors_are_flattened_with_engagement_and_uncertainty_kept_at_the_top():
    grouped = {
        "engagement": 0.9,
        "uncertainty": 0.2,
        "foundation": {"know": 0.8, "do": 0.7, "context": 0.6},
        "comprehension": {"clarity": 0.5, "coherence": 0.4},
        "execution": {"state": 0.3},
    }
    assert W._check_normalize_vectors(grouped) == {
        "engagement": 0.9,
        "uncertainty": 0.2,
        "know": 0.8,
        "do": 0.7,
        "context": 0.6,
        "clarity": 0.5,
        "coherence": 0.4,
        "state": 0.3,
    }


def test_a_single_group_is_enough_and_an_empty_or_null_group_contributes_nothing():
    assert W._check_normalize_vectors({"foundation": {"know": 0.8}, "execution": None}) == {"know": 0.8}
    assert W._check_normalize_vectors({"comprehension": {}}) == {}


@pytest.mark.parametrize("bad", [[0.1, 0.2], 7, None, '"just a string"'])
def test_something_that_is_not_a_dict_after_normalising_is_refused(bad):
    with pytest.raises(ValueError, match="dictionary"):
        W._check_normalize_vectors(bad)
