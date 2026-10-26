"""A CHECK whose uncertainty was not assessed is held at investigate, never crashed and never rescued into a proceed (pre-publish sweep, A1).

The bootstrap-gate fix (a686f06d4) made a null context/uncertainty harmless THERE, and the sweep's reviewer showed the same payload then crashed two
stages later: `_check_gate_decision` and `_gate_reason` compared `None <= threshold`, the auto-checkpoint compared `None > 0.5` outside its try, and
the legacy `check` computed `1.0 - None`. `.get("uncertainty", 0.5)` only helps when the key is ABSENT, and an absent key invented a 0.5 that read as
"uncertainty 0.500 > threshold" as if the practitioner had reported it. The rule, the same one the sentinel and the diminishing-returns detector follow:
"did not assess" is not a number. The gate cannot certify what was not assessed, so it holds at investigate and says why, whatever the overrides say.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import _workflow_check as wc

QUIET = {"recommend_proceed": False, "reason": None}
RESCUE = {"recommend_proceed": True, "reason": "know stagnant, uncertainty acceptable"}
UNASSESSED = [None, "0.2", True, [], {}]


@pytest.mark.parametrize("value", UNASSESSED)
def test_an_unassessed_uncertainty_holds_the_gate_at_investigate_and_does_not_raise(value):
    _, computed, _, _ = wc._check_gate_decision({"know": 0.9, "uncertainty": value}, 0.35, QUIET, 1, None)
    assert computed == "investigate"


def test_an_absent_uncertainty_is_the_same_as_a_null_one():
    _, computed, _, _ = wc._check_gate_decision({"know": 0.9}, 0.35, QUIET, 1, None)
    assert computed == "investigate"


@pytest.mark.parametrize("value", [None, "0.2"])
def test_neither_override_can_rescue_an_unassessed_uncertainty(value):
    """Diminishing returns and the max-rounds rule are exceptions to a number that is too high, not to a number that is missing."""
    _, by_returns, _, _ = wc._check_gate_decision({"uncertainty": value}, 0.35, RESCUE, 2, None)
    _, by_rounds, _, _ = wc._check_gate_decision({"uncertainty": value}, 0.35, QUIET, 9, None)
    assert by_returns == by_rounds == "investigate"


def test_positive_controls_an_assessed_uncertainty_still_decides_as_before():
    gate = lambda u, dr=QUIET, rnd=1: wc._check_gate_decision({"uncertainty": u}, 0.35, dr, rnd, None)[1]  # noqa: E731
    assert gate(0.35) == "proceed" and gate(0.36) == "investigate"
    assert gate(0.5, RESCUE) == "proceed" and gate(0.4, QUIET, 5) == "proceed" and gate(0.41, QUIET, 5) == "investigate"


def test_the_stated_reason_is_that_it_was_not_assessed():
    assert wc._gate_reason(None, 0.35, QUIET, 1) == "uncertainty not assessed"
    assert wc._gate_reason(None, 0.35, RESCUE, 9) == "uncertainty not assessed", "an override never changes that"
    assert wc._gate_reason(0.2, 0.35, QUIET, 1) == "uncertainty 0.200 <= threshold 0.350", "control"


@pytest.mark.parametrize("value", UNASSESSED)
def test_the_auto_checkpoint_does_not_raise_on_an_unassessed_uncertainty(value, monkeypatch):
    """It is documented as non-fatal; its comparison sat outside the try."""
    ran: list = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: ran.append(a))
    wc._check_auto_checkpoint("s", {"uncertainty": value}, "investigate", {}, 1, 1)
    assert ran == [], "an unassessed uncertainty is not evidence of a risky decision"


def test_the_auto_checkpoint_still_fires_for_a_risky_one(monkeypatch):
    ran: list = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: ran.append(a))
    wc._check_auto_checkpoint("s", {"uncertainty": 0.6}, "investigate", {}, 1, 1)
    assert len(ran) == 1


def test_the_legacy_readiness_fails_closed_when_nothing_was_assessed():
    """readiness is an input to the decision, not a reported measurement: nothing assessed and nothing asserted must not read as ready."""
    assert wc._check_readiness(None, None) == 0.0
    assert wc._check_readiness(0.8, None) == 0.8, "an asserted confidence still wins"
    assert wc._check_readiness(None, 0.25) == pytest.approx(0.75), "control"
