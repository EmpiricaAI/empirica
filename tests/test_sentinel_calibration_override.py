"""The live Sentinel evaluator honors calibration overrides.

Task 4 wired calibration_config into the enforcement path: the CHECK gate reads
`ready_uncertainty` and the escalate reads `engagement_gate`. These tests pin the
*decision behavior* — that an override actually flips the evaluator's verdict —
by mocking the threshold loader (DB/Brier) and the override resolver.
"""

from __future__ import annotations

from empirica.core import calibration_config as cc
from empirica.core.canonical.empirica_git import sentinel_hooks as sh
from empirica.core.canonical.empirica_git.sentinel_hooks import (
    SentinelDecision,
    default_epistemic_evaluator,
)


def _ev(uncertainty: float, engagement: float) -> SentinelDecision:
    return default_epistemic_evaluator({"vectors": {"uncertainty": uncertainty, "engagement": engagement}})


def test_gate_default_investigates_above_035(monkeypatch):
    monkeypatch.setattr(sh, "_load_evaluator_thresholds", lambda: (0.70, 0.35))
    monkeypatch.setattr(cc, "override_thresholds", lambda *a, **k: {})
    assert _ev(uncertainty=0.42, engagement=0.9) == SentinelDecision.INVESTIGATE


def test_ready_uncertainty_override_flips_gate_to_proceed(monkeypatch):
    # Override loosened the gate to 0.45 (surfaced via the loader) → 0.42 proceeds.
    monkeypatch.setattr(sh, "_load_evaluator_thresholds", lambda: (0.70, 0.45))
    monkeypatch.setattr(cc, "override_thresholds", lambda *a, **k: {})  # no engagement override
    assert _ev(uncertainty=0.42, engagement=0.9) == SentinelDecision.PROCEED


def test_engagement_gate_default_05_does_not_escalate_at_06(monkeypatch):
    monkeypatch.setattr(sh, "_load_evaluator_thresholds", lambda: (0.70, 0.35))
    monkeypatch.setattr(cc, "override_thresholds", lambda *a, **k: {})  # default escalate 0.5
    # engagement 0.6 >= 0.5 → no escalate; low uncertainty → proceed
    assert _ev(uncertainty=0.2, engagement=0.6) == SentinelDecision.PROCEED


def test_engagement_gate_override_raises_escalate_threshold(monkeypatch):
    monkeypatch.setattr(sh, "_load_evaluator_thresholds", lambda: (0.70, 0.35))
    monkeypatch.setattr(cc, "override_thresholds", lambda *a, **k: {"engagement_gate": 0.70})
    # engagement 0.65 < 0.70 override → ESCALATE (would have proceeded at default 0.5)
    assert _ev(uncertainty=0.2, engagement=0.65) == SentinelDecision.ESCALATE


def test_evaluator_failsafe_when_override_raises(monkeypatch):
    monkeypatch.setattr(sh, "_load_evaluator_thresholds", lambda: (0.70, 0.35))

    def _boom(*_a, **_k):
        raise RuntimeError("bad calibration.yaml")

    monkeypatch.setattr(cc, "override_thresholds", _boom)
    # A throwing override must never crash the gate — falls back to default 0.5 escalate.
    assert _ev(uncertainty=0.2, engagement=0.6) == SentinelDecision.PROCEED
