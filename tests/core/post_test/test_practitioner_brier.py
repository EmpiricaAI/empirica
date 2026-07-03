"""EPIC B / B5 — per-practitioner Brier profile + practice-vs-practitioner divergence.

The raw per-practitioner reliability surface (session-keyed Brier), latent in the
already-instrumented ``calibration_trajectory`` table. Seeds the table directly
(``record_trajectory_point`` needs a full GroundedAssessment + sessions row) so
the query/aggregation is pinned. Credibility shrinkage stays autonomy's lane.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from empirica.core.post_test.dynamic_thresholds import (
    compute_practitioner_divergence,
    get_brier_profile,
    get_practitioner_brier_profile,
)


def _db_with_trajectory(rows):
    """rows: (session_id, ai_id, phase, self_assessed, grounded, ts)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE calibration_trajectory (
               session_id TEXT, ai_id TEXT, vector_name TEXT, phase TEXT,
               self_assessed REAL, grounded REAL, gap REAL, timestamp REAL)"""
    )
    for sid, ai, phase, sa, gr, ts in rows:
        conn.execute(
            "INSERT INTO calibration_trajectory "
            "(session_id, ai_id, vector_name, phase, self_assessed, grounded, timestamp) "
            "VALUES (?, ?, 'know', ?, ?, ?, ?)",
            (sid, ai, phase, sa, gr, ts),
        )
    conn.commit()
    return SimpleNamespace(conn=conn)


# A well-calibrated practitioner (self ≈ grounded) vs an overconfident one (self ≫ grounded).
_A = [(0.7, 0.7), (0.8, 0.8), (0.9, 0.85), (0.6, 0.65), (0.75, 0.75), (0.85, 0.8)]
_B = [(0.9, 0.4), (0.85, 0.3), (0.95, 0.5), (0.8, 0.35), (0.9, 0.45), (0.88, 0.4)]


def _rows():
    rows = [("sess-A", "empirica", "combined", sa, gr, i) for i, (sa, gr) in enumerate(_A)]
    rows += [("sess-B", "empirica", "combined", sa, gr, 100 + i) for i, (sa, gr) in enumerate(_B)]
    return rows


def test_practitioner_brier_scoped_to_session():
    db = _db_with_trajectory(_rows())
    pa = get_practitioner_brier_profile("sess-A", "empirica", db)
    pb = get_practitioner_brier_profile("sess-B", "empirica", db)
    practice = get_brier_profile("empirica", db)  # all 12 rows, both sessions

    # the well-calibrated practitioner has a lower (better) Brier than the overconfident one
    assert pa["combined"]["brier_score"] < pb["combined"]["brier_score"]
    # each practitioner profile is scoped to its own session; the practice sees both
    assert pa["combined"]["n_predictions"] == len(_A)
    assert pb["combined"]["n_predictions"] == len(_B)
    assert practice["combined"]["n_predictions"] == len(_A) + len(_B)


def test_practitioner_divergence_signals_direction():
    db = _db_with_trajectory(_rows())
    # A (well-calibrated) vs the mixed practice aggregate → better-than-practice (negative delta)
    da = compute_practitioner_divergence("sess-A", "empirica", db)["combined"]
    assert da["practitioner_n"] == len(_A) and da["practice_n"] == len(_A) + len(_B)
    assert da["brier_delta"] <= 0  # A is better-calibrated than the practice
    # B (overconfident) vs the practice → worse-than-practice (positive delta)
    db_ = compute_practitioner_divergence("sess-B", "empirica", db)["combined"]
    assert db_["brier_delta"] >= 0


def test_divergence_insufficient_data():
    db = _db_with_trajectory([("sess-thin", "empirica", "combined", 0.8, 0.8, 0)])  # 1 point
    d = compute_practitioner_divergence("sess-thin", "empirica", db)["combined"]
    assert d["status"] == "insufficient_data"
    assert d["practitioner_n"] == 1


def test_brier_variance_matches_sample_variance():
    # Per-sample Brier components (self−grounded)²: 0.0, 0.16, 0.04 — hand-check
    # the ddof=1 sample variance the profile exposes for the true-SE fold (B7).
    rows = [
        ("sess-x", "empirica", "combined", 0.9, 0.9, 0),  # (0.9-0.9)² = 0.0
        ("sess-x", "empirica", "combined", 0.5, 0.9, 1),  # (0.5-0.9)² = 0.16
        ("sess-x", "empirica", "combined", 0.7, 0.9, 2),  # (0.7-0.9)² = 0.04
    ]
    prof = get_practitioner_brier_profile("sess-x", "empirica", _db_with_trajectory(rows))["combined"]
    comps = [0.0, 0.16, 0.04]
    mean = sum(comps) / len(comps)
    expected_var = sum((c - mean) ** 2 for c in comps) / (len(comps) - 1)  # ddof=1
    assert abs(prof["brier_variance"] - round(expected_var, 6)) < 1e-6
    # SE = sqrt(brier_variance / n) is well-defined and non-negative.
    assert (prof["brier_variance"] / prof["n_predictions"]) ** 0.5 >= 0.0


def test_divergence_exposes_both_variances():
    db = _db_with_trajectory(_rows())
    d = compute_practitioner_divergence("sess-A", "empirica", db)["combined"]
    # both sides' variance ride alongside the means + n's so the consumer can
    # form a per-side SE for the asymmetric shrink.
    assert d["practitioner_brier_variance"] >= 0.0
    assert d["practice_brier_variance"] >= 0.0
