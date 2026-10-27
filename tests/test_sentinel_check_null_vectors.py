"""A vector the practitioner did not assess must not reach the gate as a number (goal e4344283, the A7 follow-through).

Since 1.14.16 `store_vectors` writes NULL for a vector that was not supplied (a CHECK supplies 7 of 13). `sentinel-gate.py` read the
CHECK's two vectors with `know or 0` and `uncertainty or 1`, which does two wrong things:

* NULL uncertainty became 1.0, so a CHECK that skipped uncertainty printed "U=100%" as if the practitioner had reported total doubt;
* a REAL 0.0 uncertainty (maximal certainty) is falsy, so it also became 1.0, and a PREFLIGHT reporting uncertainty 0.0 was refused the
  auto-proceed it had earned.

A below-threshold CHECK stays advisory (the Sentinel judges readiness holistically, not by fixed numbers). A CHECK that did not assess one of the
two is a different thing: there is nothing to be advisory about, `check-submit` computes `investigate` for the same payload, and a gate's floor for
what was not assessed is not certified, never allow (ecodex, prop_h2ofrznefjcjpj3b3b44rgrizm; the "decision downgraded across a boundary" row). It
denies and names what to resubmit; it still never prints or decides on a number nobody reported.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("sentinel_gate_null_vectors", _HOOK_DIR / "sentinel-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__dict__["_get_dynamic_thresholds"] = lambda _db: (0.7, 0.35)
    module.__dict__["_get_domain_scaled_thresholds"] = lambda unc, *_args, **_kwargs: unc
    return module


def test_a_check_that_meets_both_thresholds_passes(gate):
    decision, reason = gate._evaluate_check_threshold(0.9, 0.2, None, "")
    assert decision == "allow"
    assert reason.startswith("CHECK passed"), reason


def test_a_real_zero_uncertainty_is_maximal_certainty_not_total_doubt(gate):
    """`0.0 or 1` is 1: the falsy zero was read as missing and the CHECK was reported as below threshold."""
    decision, reason = gate._evaluate_check_threshold(0.9, 0.0, None, "")
    assert decision == "allow"
    assert reason.startswith("CHECK passed"), reason


def test_a_null_uncertainty_is_not_certified_and_is_not_reported_as_100_percent(gate):
    decision, reason = gate._evaluate_check_threshold(0.9, None, None, "")
    assert decision == "deny"
    assert "100%" not in reason, reason
    assert "uncertainty" in reason.lower() and "not assess" in reason.lower() and "check-submit" in reason, reason


def test_a_null_know_is_not_certified_and_is_not_reported_as_0_percent(gate):
    decision, reason = gate._evaluate_check_threshold(None, 0.2, None, "")
    assert decision == "deny"
    assert "K=0%" not in reason, reason
    assert "know" in reason.lower() and "not assess" in reason.lower(), reason


def test_a_check_that_assessed_neither_names_both(gate):
    decision, reason = gate._evaluate_check_threshold(None, None, None, "")
    assert decision == "deny" and "know and uncertainty" in reason, reason


def test_the_denial_keeps_the_environment_annotation(gate):
    assert gate._evaluate_check_threshold(None, 0.2, None, " [env]")[1].endswith(" [env]")


def test_a_non_numeric_vector_is_not_assessed_either_so_it_is_not_a_threshold_comparison(gate):
    """A string would raise on `>=`; whatever the store holds, only a number is an assessment."""
    for bad in ("0.9", [], {}, True):
        decision, reason = gate._evaluate_check_threshold(bad, 0.2, None, "")
        assert decision == "deny" and "know" in reason, (bad, reason)


def test_a_check_below_threshold_still_reports_its_real_numbers(gate):
    """Negative control: the advisory text for an assessed-but-low CHECK keeps its numbers."""
    decision, reason = gate._evaluate_check_threshold(0.5, 0.6, None, "")
    assert decision == "allow"
    assert reason.startswith("ADVISORY")
    assert "K=50%" in reason and "U=60%" in reason, reason


def test_auto_proceed_accepts_a_real_zero_uncertainty(gate):
    result = gate._check_auto_proceed(0.9, gate._assessed(0.0, 1.0), None, None, None, None, "")
    assert result is not None and result[0] == "allow", result


@pytest.mark.parametrize(
    ("value", "default", "expected"), [(None, 1.0, 1.0), (0.0, 1.0, 0.0), (0.4, 1.0, 0.4), (0, 1, 0)]
)
def test_assessed_substitutes_only_for_a_missing_value(gate, value, default, expected):
    assert gate._assessed(value, default) == expected
