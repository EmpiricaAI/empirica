"""Two reporter-found defects where a value was measured against the wrong reference.

**#388 (FrancisFerrero)** — PREFLIGHT/CHECK retrieval gated on the
``EMPIRICA_QDRANT_URL`` env var, but ``_get_qdrant_client`` resolves a URL four
ways (explicit → per-project resolver → env → localhost:6333 probe). Anyone on
priority 1, 2, or 4 had working writes and silently empty reads. The empty
return is byte-identical to "Qdrant is up and nothing matched", so there was no
signal to notice it by.

**#393 (FrancisFerrero)** — ``calibration-report`` printed "BIAS CORRECTIONS
(apply to self-assessment)" for a number computed as ``grounded_mean − 0.5``,
a fixed prior. It is only the right correction when the self-assessment was
exactly 0.5. On any vector where the AI was *overconfident* it pointed the wrong
way, and the same number is written to ``.breadcrumbs.yaml`` as
``grounded_bias_corrections`` and injected at session start as a pattern to
internalize.

Both share the session's dominant shape: a value keyed on something that
usually correlates with the truth instead of the truth itself.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from empirica.core.bayesian_beliefs import Belief
from empirica.core.post_test.grounded_calibration import GroundedCalibrationManager

# ── #388: the retrieval gate asks the same question the data path asks ──


@pytest.fixture
def qdrant_reachable(monkeypatch):
    """Qdrant resolves — but NOT through the env var. This is the reported
    scenario: a local server on :6333, or a per-project resolver hook."""
    import empirica.core.qdrant.pattern_retrieval as pr
    import empirica.core.qdrant.vector_store as vs

    monkeypatch.delenv("EMPIRICA_QDRANT_URL", raising=False)
    monkeypatch.setattr(vs, "_check_qdrant_available", lambda *a, **k: True)
    monkeypatch.setattr(
        pr,
        "_search_memory_by_type",
        lambda project_id, query_text, memory_type, limit=3, min_score=0.5: (
            [{"text": "DEAD END: tried X Why failed: Y", "score": 0.9}] if memory_type == "dead_end" else []
        ),
    )
    return pr


def test_preflight_retrieves_when_qdrant_resolves_without_the_env_var(qdrant_reachable):
    """POSITIVE CONTROL — the exact reproduction from #388."""
    pr = qdrant_reachable

    result = pr.retrieve_task_patterns("proj", "some task", vectors=None)

    assert result["dead_ends"], "retrieval returned empty while Qdrant was reachable"


def test_check_warnings_fire_when_qdrant_resolves_without_the_env_var(qdrant_reachable):
    """The CHECK gate had the same defect and needs its own control — a fix to
    one call site would leave the other silently empty."""
    pr = qdrant_reachable

    warnings = pr.check_against_patterns("proj", current_approach="tried X", vectors=None)

    assert warnings["dead_end_matches"], "CHECK returned no warnings while Qdrant was reachable"
    assert warnings["has_warnings"] is True


def test_the_env_var_is_no_longer_the_authority(monkeypatch):
    """The inverse error: env set, Qdrant genuinely unavailable. Keying on the
    env var got this wrong in BOTH directions."""
    import empirica.core.qdrant.pattern_retrieval as pr
    import empirica.core.qdrant.vector_store as vs

    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://configured-but-dead:6333")
    monkeypatch.setattr(vs, "_check_qdrant_available", lambda *a, **k: False)

    assert pr.retrieve_task_patterns("proj", "task")["dead_ends"] == []


def test_unavailable_qdrant_still_returns_the_full_key_set(monkeypatch):
    """NEGATIVE CONTROL: the short-circuit must survive. Callers index these
    keys unconditionally — dropping the early return would turn a graceful
    empty into a KeyError at PREFLIGHT."""
    import empirica.core.qdrant.pattern_retrieval as pr
    import empirica.core.qdrant.vector_store as vs

    monkeypatch.setattr(vs, "_check_qdrant_available", lambda *a, **k: False)

    result = pr.retrieve_task_patterns("proj", "task")
    assert set(result) >= {"lessons", "dead_ends", "prior_mistakes", "relevant_findings", "time_gap"}

    warnings = pr.check_against_patterns("proj", current_approach="x")
    assert set(warnings) >= {"dead_end_matches", "mistake_matches", "mistake_risk", "has_warnings"}
    assert warnings["has_warnings"] is False


# ── #393: a correction is relative to what it corrects ──────────────────


def _belief(vector: str, mean: float, ec: int) -> Belief:
    return Belief(
        vector_name=vector,
        mean=mean,
        variance=0.05,
        evidence_count=ec,
        prior_mean=0.5,
        prior_variance=0.25,
        last_updated=datetime.now(),
    )


@pytest.fixture
def calibration(monkeypatch):
    """Wire a manager whose two belief tracks we control, exercising the real
    divergence → adjustment chain rather than stubbing the arithmetic."""
    import empirica.core.bayesian_beliefs as bb

    def _make(self_ref: dict[str, tuple[float, int]], grounded: dict[str, tuple[float, int]]):
        gcm = GroundedCalibrationManager.__new__(GroundedCalibrationManager)
        gcm.db = object()

        monkeypatch.setattr(bb.BayesianBeliefManager, "__init__", lambda self, db: None)
        monkeypatch.setattr(
            bb.BayesianBeliefManager,
            "get_beliefs",
            lambda self, ai_id: {v: _belief(v, m, ec) for v, (m, ec) in self_ref.items()},
        )
        monkeypatch.setattr(
            GroundedCalibrationManager,
            "get_grounded_beliefs",
            lambda self, ai_id: {v: _belief(v, m, ec) for v, (m, ec) in grounded.items()},
        )
        return gcm

    return _make


def test_an_overconfident_vector_is_corrected_DOWNWARD(calibration):
    """POSITIVE CONTROL — the sign flip.

    Self-assessed 0.90, evidence says 0.60. The practitioner is over-assessing,
    so the correction must be negative. The old formula returned
    0.60 − 0.50 = +0.10: 'you are under-assessing, go higher' — to someone
    already 0.30 above the evidence.
    """
    gcm = calibration(self_ref={"know": (0.9, 10)}, grounded={"know": (0.6, 10)})

    assert gcm.get_grounded_adjustments("ai")["know"] < 0


def test_an_underconfident_vector_is_corrected_upward(calibration):
    """NEGATIVE CONTROL: negating unconditionally would pass the test above
    while breaking every under-assessed vector."""
    gcm = calibration(self_ref={"know": (0.3, 10)}, grounded={"know": (0.7, 10)})

    assert gcm.get_grounded_adjustments("ai")["know"] > 0


def test_a_perfectly_calibrated_vector_gets_no_correction(calibration):
    """The sharpest case. Self-assessment matches the evidence exactly, so
    there is nothing to correct. The old formula returned 0.80 − 0.50 = +0.30,
    capped to +0.25 — it moved a perfectly calibrated vector by the maximum
    allowed correction."""
    gcm = calibration(self_ref={"know": (0.8, 10)}, grounded={"know": (0.8, 10)})

    assert gcm.get_grounded_adjustments("ai")["know"] == 0.0


def test_the_correction_magnitude_matches_the_gap(calibration):
    """Direction alone isn't enough — the size has to mean something too.
    gap = 0.80 − 0.60 = 0.20; evidence_count 10 → full weight → −0.20. Kept
    under MAX_CORRECTION_MAGNITUDE so this measures the gap, not the cap."""
    gcm = calibration(self_ref={"know": (0.8, 10)}, grounded={"know": (0.6, 10)})

    assert gcm.get_grounded_adjustments("ai")["know"] == pytest.approx(-0.20, abs=1e-4)


def test_thin_evidence_is_weighted_down(calibration):
    """evidence_count 5 → weight 0.5 → half the raw gap."""
    gcm = calibration(self_ref={"know": (0.9, 5)}, grounded={"know": (0.5, 5)})

    assert gcm.get_grounded_adjustments("ai")["know"] == pytest.approx(-0.20, abs=1e-4)


def test_the_cap_still_bounds_the_correction(calibration):
    """A 0.95 gap must not emit a 0.95 correction."""
    gcm = calibration(self_ref={"know": (0.99, 10)}, grounded={"know": (0.04, 10)})

    assert gcm.get_grounded_adjustments("ai")["know"] == pytest.approx(-0.25, abs=1e-4)


def test_a_vector_below_the_evidence_threshold_is_omitted(calibration):
    """Unchanged behaviour: fewer than 3 grounded observations is not a basis
    for telling anyone to adjust anything."""
    gcm = calibration(self_ref={"know": (0.9, 10)}, grounded={"know": (0.5, 2)})

    assert "know" not in gcm.get_grounded_adjustments("ai")


def test_a_vector_with_no_self_assessment_is_omitted(calibration):
    """There is no self-assessment to correct, so no correction is emitted.
    Falling back to the prior here is precisely the reported bug."""
    gcm = calibration(self_ref={}, grounded={"know": (0.9, 10)})

    assert "know" not in gcm.get_grounded_adjustments("ai")


def test_corrections_and_divergence_never_disagree_in_sign(calibration):
    """The regression that makes the report self-contradictory: the breadcrumbs
    file carries `divergence` and `grounded_bias_corrections` a few lines
    apart. On the live empirica practice, 5 of 12 vectors had corrections
    pointing opposite their own divergence. They are the same measurement and
    must never disagree."""
    gcm = calibration(
        self_ref={"know": (0.9, 10), "do": (0.2, 10), "impact": (0.5, 10)},
        grounded={"know": (0.6, 10), "do": (0.7, 10), "impact": (0.5, 10)},
    )

    divergence = gcm.get_calibration_divergence("ai")
    adjustments = gcm.get_grounded_adjustments("ai")

    for vector, adj in adjustments.items():
        gap = divergence[vector]["gap"]
        assert adj * gap <= 0, f"{vector}: correction {adj:+.2f} contradicts divergence {gap:+.2f}"
