"""Read-time recency re-ranking of PREFLIGHT findings (decay P1/a, 2026-05-28).

retrieve_task_patterns previously ranked findings by raw cosine score, so a
finding about code removed months ago ranked identically to one written today.
_apply_recency_rerank folds FindingsDeprecationEngine's 30-day-half-life
time-decay into the ranking at read time (no stored mutation). These tests pin
the ranking behaviour + the ISO-timestamp normalisation (the payload stores ISO
strings, which calculate_time_decay alone would silently score 0.5).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from empirica.core.findings_deprecation import FindingsDeprecationEngine
from empirica.core.qdrant.pattern_retrieval import _apply_recency_rerank


def test_fresh_finding_outranks_stale_with_higher_cosine():
    now = datetime.now()
    items = [
        # Stale but higher cosine similarity
        {"text": "stale", "score": 0.90, "timestamp": (now - timedelta(days=120)).isoformat()},
        # Fresh, slightly lower cosine
        {"text": "fresh", "score": 0.85, "timestamp": now.isoformat()},
    ]
    ranked = _apply_recency_rerank(items, limit=2)
    # 120d decay = e^-4 ~= 0.018 -> stale effective ~0.016; fresh ~0.85
    assert ranked[0]["text"] == "fresh"
    assert ranked[0]["recency_weight"] > ranked[1]["recency_weight"]
    assert ranked[0]["effective_score"] > ranked[1]["effective_score"]


def test_missing_timestamp_is_neutral_weight():
    ranked = _apply_recency_rerank(
        [{"text": "x", "score": 0.7, "timestamp": None}], limit=1
    )
    assert ranked[0]["recency_weight"] == 1.0
    assert ranked[0]["effective_score"] == 0.7


def test_unparseable_timestamp_is_neutral_weight():
    ranked = _apply_recency_rerank(
        [{"text": "x", "score": 0.6, "timestamp": "not-a-date"}], limit=1
    )
    assert ranked[0]["recency_weight"] == 1.0


def test_limit_truncates_after_rerank():
    now = datetime.now().isoformat()
    items = [{"text": str(i), "score": 0.5, "timestamp": now} for i in range(5)]
    assert len(_apply_recency_rerank(items, limit=3)) == 3


def test_empty_input_returns_empty():
    assert _apply_recency_rerank([], limit=3) == []


# --- impact-modulated half-life (David-locked: tau = 30*(1+2*impact)) ---


def test_high_impact_resists_decay_vs_low_impact_same_age():
    ts = (datetime.now() - timedelta(days=90)).isoformat()
    items = [
        {"text": "low", "score": 0.8, "timestamp": ts, "impact": 0.1},
        {"text": "high", "score": 0.8, "timestamp": ts, "impact": 0.9},
    ]
    ranked = _apply_recency_rerank(items, limit=2)
    high = next(f for f in ranked if f["text"] == "high")
    low = next(f for f in ranked if f["text"] == "low")
    # Same age + same cosine → impact alone decides: high-impact decays slower.
    assert high["recency_weight"] > low["recency_weight"]
    assert ranked[0]["text"] == "high"


def test_calculate_time_decay_impact_lengthens_tau():
    age_90d = (datetime.now() - timedelta(days=90)).timestamp()
    flat = FindingsDeprecationEngine.calculate_time_decay(age_90d)            # tau=30
    high = FindingsDeprecationEngine.calculate_time_decay(age_90d, longevity=0.9)  # tau=84
    assert high > flat
    # longevity=0.0 gives tau=30 too (same as None/flat); tolerance covers the
    # microsecond drift between the two internal datetime.now() calls.
    flat_zero = FindingsDeprecationEngine.calculate_time_decay(age_90d, longevity=0.0)
    assert abs(flat_zero - flat) < 1e-6


def test_calculate_time_decay_backwards_compatible_default():
    # No impact arg → same as before (flat 30d e-folding). Guards callers like
    # calculate_relevance_score that must not get impact double-counted.
    age_30d = (datetime.now() - timedelta(days=30)).timestamp()
    weight = FindingsDeprecationEngine.calculate_time_decay(age_30d)
    assert 0.35 < weight < 0.40  # exp(-1) ~= 0.368


# --- lessons/eidetic use CONFIDENCE as the longevity modulator, off first_seen ---


def test_recency_rerank_confidence_modulator():
    # Same age + same cosine → confidence (not impact) decides; eidetic ages
    # off first_seen, not timestamp. High-confidence facts resist ageing.
    old = (datetime.now() - timedelta(days=90)).isoformat()
    items = [
        {"text": "low-conf", "score": 0.8, "first_seen": old, "confidence": 0.1},
        {"text": "high-conf", "score": 0.8, "first_seen": old, "confidence": 0.9},
    ]
    ranked = _apply_recency_rerank(items, 2, modulator_key="confidence", ts_key="first_seen")
    hi = next(i for i in ranked if i["text"] == "high-conf")
    lo = next(i for i in ranked if i["text"] == "low-conf")
    assert hi["recency_weight"] > lo["recency_weight"]
    assert ranked[0]["text"] == "high-conf"
