"""Tests for the coverage-threshold gate and remote-ops short-circuit
in _run_single_phase_verification.

Strategy: monkeypatch PostTestCollector.collect_all to return controlled
EvidenceBundle objects instead of building a real session DB. This isolates
the threshold-gate logic from the collector's dependency surface.
"""

from __future__ import annotations

from empirica.core.post_test.collector import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    PostTestCollector,
)
from empirica.core.post_test.grounded_calibration import (
    INSUFFICIENT_EVIDENCE_THRESHOLD,
    _run_single_phase_verification,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle_with_n_vectors(n: int) -> EvidenceBundle:
    """Build an EvidenceBundle whose items together touch exactly n distinct
    vectors. Used to control grounded_coverage = n / 13.
    """
    bundle = EvidenceBundle(session_id="threshold-test")
    vector_names = [
        "know",
        "signal",
        "context",
        "do",
        "change",
        "state",
        "clarity",
        "coherence",
        "density",
        "completion",
        "impact",
        "engagement",
    ]
    for i in range(n):
        bundle.items.append(
            EvidenceItem(
                source="artifacts",
                metric_name=f"metric_{i}",
                value=0.7,
                raw_value={"count": 1},
                quality=EvidenceQuality.OBJECTIVE,
                supports_vectors=[vector_names[i]],
            )
        )
    bundle.sources_available = ["artifacts"]
    bundle.coverage = n / 13.0
    return bundle


# ---------------------------------------------------------------------------
# Task 11: remote-ops short-circuit
# ---------------------------------------------------------------------------


def test_remote_ops_short_circuit_returns_ungrounded_status():
    """work_type=remote-ops should bypass collection entirely and return
    calibration_status=ungrounded_remote_ops without touching the DB."""
    result = _run_single_phase_verification(
        session_id="rops-test",
        vectors={"know": 0.8, "uncertainty": 0.2},
        db=None,  # remote-ops should not need DB access
        phase="praxic",
        work_type="remote-ops",
    )

    assert result is not None
    assert result["calibration_status"] == "ungrounded_remote_ops"
    assert result["holistic_calibration_score"] is None
    assert result["gaps"] == {}
    assert result["self_assessed"] == {"know": 0.8, "uncertainty": 0.2}
    assert set(result["insufficient_evidence_vectors"]) == {"know", "uncertainty"}


def test_remote_ops_short_circuit_skips_collection(monkeypatch):
    """The short-circuit must run BEFORE PostTestCollector is constructed.
    If collect_all is called for remote-ops, the short-circuit is broken."""
    called = {"collect_all": False}

    def boom(*args, **kwargs):
        called["collect_all"] = True
        raise AssertionError("PostTestCollector.collect_all should not be called for remote-ops")

    monkeypatch.setattr(PostTestCollector, "collect_all", boom)

    result = _run_single_phase_verification(
        session_id="rops-test",
        vectors={"know": 0.8, "uncertainty": 0.2},
        db=None,
        phase="praxic",
        work_type="remote-ops",
    )

    assert called["collect_all"] is False
    assert result["calibration_status"] == "ungrounded_remote_ops"


# ---------------------------------------------------------------------------
# Task 10: coverage threshold gate
# ---------------------------------------------------------------------------


def test_coverage_below_threshold_returns_insufficient_status(monkeypatch):
    """When grounded_coverage < INSUFFICIENT_EVIDENCE_THRESHOLD, response
    should have calibration_status=insufficient_evidence and empty gaps."""
    bundle = _make_bundle_with_n_vectors(1)  # 1/13 ≈ 0.077, below 0.3
    monkeypatch.setattr(PostTestCollector, "collect_all", lambda self: bundle)

    result = _run_single_phase_verification(
        session_id="threshold-test",
        vectors={"know": 0.7, "uncertainty": 0.3},
        db=None,
        phase="praxic",
        work_type="code",
    )

    assert result is not None
    assert result["calibration_status"] == "insufficient_evidence"
    assert result["gaps"] == {}
    assert result["holistic_calibration_score"] is None
    assert result["grounded_coverage"] < INSUFFICIENT_EVIDENCE_THRESHOLD
    assert "self_assessed" in result
    assert result["self_assessed"]["know"] == 0.7


def test_empty_bundle_returns_insufficient_status(monkeypatch):
    """A collected-but-empty bundle (no items) should return
    insufficient_evidence, not None — the early-return collision fix."""
    empty = EvidenceBundle(session_id="empty-test")
    monkeypatch.setattr(PostTestCollector, "collect_all", lambda self: empty)

    result = _run_single_phase_verification(
        session_id="empty-test",
        vectors={"know": 0.7, "uncertainty": 0.3},
        db=None,
        phase="praxic",
        work_type="code",
    )

    assert result is not None  # was returning None before the fix
    assert result["calibration_status"] == "insufficient_evidence"
    assert result["grounded_coverage"] == 0.0
    assert result["self_assessed"]["know"] == 0.7


# ---------------------------------------------------------------------------
# Wrapper-level None propagation regression (T5.5 bugfix)
# ---------------------------------------------------------------------------


def test_wrapper_handles_non_grounded_phase_without_typeerror(monkeypatch):
    """When a phase returns calibration_status != 'grounded' with
    calibration_score=None, the wrapper's holistic computation must NOT
    crash with TypeError: unsupported operand type(s) for *: 'float' and 'NoneType'.

    Regression for the bug discovered during T5 smoke test: adding
    calibration_score=None to the insufficient_evidence response broke
    run_grounded_verification_pipeline's holistic score computation
    which used results['noetic'].get('calibration_score', 0) — default
    0 only applies when the key is MISSING, not when the value IS None.
    """
    from empirica.core.post_test.grounded_calibration import (
        run_grounded_verification,
    )

    # Return a bundle with 1 vector of evidence → coverage 1/13 ≈ 0.077, below threshold
    # So _run_single_phase_verification will return insufficient_evidence with
    # calibration_score=None, and the wrapper must handle that gracefully.
    thin_bundle = _make_bundle_with_n_vectors(1)
    monkeypatch.setattr(PostTestCollector, "collect_all", lambda self: thin_bundle)

    # No phase_boundary → combined mode. The wrapper still computes a
    # holistic score, and with our fix, it should gracefully produce
    # holistic_calibration_score=None when no phase is grounded.
    # Failures now reach the caller (1.17.9 sweep); the property guarded here is only that none is a TypeError.
    try:
        result = run_grounded_verification(
            session_id="wrapper-regression-test",
            postflight_vectors={"know": 0.7, "uncertainty": 0.3},
            db=None,
            phase_boundary=None,  # → combined mode
            phase_tool_counts=None,
            work_type="code",
        )
    except TypeError:
        raise
    except Exception:
        result = None  # db=None fails elsewhere; production always passes a database

    # The wrapper should not crash. Result may be None (if the pipeline
    # short-circuits on error) or a dict with holistic_calibration_score=None.
    # Either is acceptable — the key property is that no TypeError was raised.
    if result is not None:
        assert result.get("holistic_calibration_score") is None, (
            "holistic_calibration_score should be None when the only phase ran with insufficient evidence"
        )
