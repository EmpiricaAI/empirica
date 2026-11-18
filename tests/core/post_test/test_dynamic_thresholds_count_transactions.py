"""Dynamic CHECK thresholds count transactions, not vector rows.

calibration_trajectory holds one row per vector per phase per POSTFLIGHT, about
12 per transaction. Counting rows made `min_transactions`, the lookback LIMIT and
the n/20 cold-start damper measure vectors: two transactions read as 24
"transactions" and cleared a minimum of 5.
"""

from __future__ import annotations

import sqlite3

from empirica.core.post_test.dynamic_thresholds import compute_dynamic_thresholds

VECTORS = [
    "know", "do", "context", "clarity", "coherence", "signal",
    "density", "state", "change", "completion", "impact", "engagement",
]  # fmt: skip


class _DB:
    def __init__(self, rows):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE calibration_trajectory (ai_id TEXT, phase TEXT, vector_name TEXT, self_assessed REAL,"
            " grounded REAL, timestamp REAL, practitioner_model TEXT, transaction_id TEXT)"
        )
        self.conn.executemany("INSERT INTO calibration_trajectory VALUES (?,?,?,?,?,?,?,?)", rows)


def _transactions(n, self_assessed=0.95, grounded=0.2, model=None, legacy=False):
    """n transactions x 12 vectors; one POSTFLIGHT's rows share a timestamp."""
    rows = []
    for t in range(n):
        tx = None if legacy else f"tx-{t}"
        rows += [("prac", "noetic", v, self_assessed, grounded, 1000.0 + t, model, tx) for v in VECTORS]
    return rows


def test_two_transactions_of_twelve_vectors_are_two_transactions():
    r = compute_dynamic_thresholds("prac", _DB(_transactions(2)), min_transactions=5)
    assert r["source"] == "static"


def test_enough_transactions_go_dynamic_and_the_damper_counts_transactions():
    """Positive control: 6 transactions clear a minimum of 5; the damper reads 6/20, not 72/20."""
    r = compute_dynamic_thresholds("prac", _DB(_transactions(6)), min_transactions=5)
    assert r["source"] == "dynamic"
    noetic = r["noetic"]
    assert noetic["transactions_analyzed"] == 6
    # badly overconfident -> raw inflation at the cap; the damper scales it by 6/20
    assert 0 < noetic["threshold_inflation"] <= round(0.05 * 6 / 20, 3) + 1e-9


def test_lookback_limits_transactions_not_rows():
    r = compute_dynamic_thresholds("prac", _DB(_transactions(10)), min_transactions=5, lookback=7)
    assert r["noetic"]["transactions_analyzed"] == 7
    assert r["noetic"]["points_analyzed"] == 7 * len(VECTORS)


def test_rows_from_before_transaction_ids_group_by_their_postflight_timestamp():
    r = compute_dynamic_thresholds("prac", _DB(_transactions(3, legacy=True)), min_transactions=5)
    assert r["source"] == "static"
    r = compute_dynamic_thresholds("prac", _DB(_transactions(6, legacy=True)), min_transactions=5)
    assert r["source"] == "dynamic" and r["noetic"]["transactions_analyzed"] == 6
