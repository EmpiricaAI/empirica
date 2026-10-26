"""A POSTFLIGHT payload that leaves a vector null does not drop the other deltas (pre-publish sweep, second pass).

The delta loop computed `post_val - pre_val` for every vector in the payload; an explicit null (or text) raised TypeError, and the outer handler
swallowed it, so every delta after that vector was silently lost and the trajectory-issue checks never ran. The PREFLIGHT-side NULL was fixed
earlier (the a7 reader tests); this is the POSTFLIGHT side of the same loop. `impact` comes FIRST on purpose: an early null is the case that
drops everything after it, while a late one would leave the earlier deltas and pass by accident.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import _workflow_postflight as wp


class _Logger:
    def __init__(self, pre):
        self._pre = pre

    def get_last_checkpoint(self, phase=None):
        return {"vectors": self._pre}


PRE = {"impact": 0.5, "know": 0.5, "clarity": 0.4, "completion": 0.0, "uncertainty": 0.3}


def _deltas(post):
    return wp._calculate_postflight_deltas(_Logger(PRE), post, "sid")


@pytest.mark.parametrize("unassessed", [None, "0.9", True])
def test_an_unassessed_vector_in_the_payload_is_skipped_and_the_rest_still_get_their_deltas(unassessed):
    _pre, deltas, _issues = _deltas({"impact": unassessed, "know": 0.9, "clarity": 0.7})
    assert deltas == {"know": 0.4, "clarity": 0.3}, deltas
    assert "impact" not in deltas, "no assessed value, so no delta against an invented one"


def test_completion_high_with_an_unassessed_uncertainty_raises_nothing_and_flags_nothing():
    # completion first: its uncertainty read is where the exception was swallowed, and `know` after it is what would be lost
    _pre, deltas, issues = _deltas({"completion": 0.95, "uncertainty": None, "know": 0.6})
    assert deltas == {"completion": 0.95, "know": 0.1}, deltas
    assert not [i for i in issues if i["pattern"] == "completion_high_uncertainty_high"]


def test_completion_high_with_an_assessed_high_uncertainty_is_still_flagged():
    """Control: the check itself is unchanged for an assessed value."""
    _pre, _deltas_out, issues = _deltas({"completion": 0.95, "uncertainty": 0.6})
    assert [i["pattern"] for i in issues] == ["completion_high_uncertainty_high"]


def test_assessed_payloads_compute_exactly_the_deltas_they_did_before():
    _pre, deltas, _issues = _deltas({"impact": 0.8, "know": 0.9, "clarity": 0.7})
    assert deltas == {"impact": 0.3, "know": 0.4, "clarity": 0.3}
