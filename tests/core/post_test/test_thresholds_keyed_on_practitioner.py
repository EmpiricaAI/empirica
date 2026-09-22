"""CHECK thresholds read the practitioner's own trajectory inside the practice.

David, 2026-09-21: calibration accrues to the practitioner, artifacts to the
practice. `ai_id` names the practice store, which pools every model that has
inhabited it; core's store mixes Opus 5 and Fable 5.1 transactions from one day.
So the thresholds key on (practice, model) and fall back to the practice while a
model has too few points, and every phase says which basis it used.
"""

from __future__ import annotations

import sqlite3

from empirica.core.post_test.dynamic_thresholds import compute_dynamic_thresholds


class _DB:
    def __init__(self, rows):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE calibration_trajectory (ai_id TEXT, phase TEXT, self_assessed REAL, grounded REAL,"
            " timestamp REAL, practitioner_model TEXT)"
        )
        self.conn.executemany("INSERT INTO calibration_trajectory VALUES (?,?,?,?,?,?)", rows)


def _points(model, n, self_assessed, grounded, start=0):
    return [("prac", "noetic", self_assessed, grounded, start + i, model) for i in range(n)]


# A well-calibrated model and a badly overconfident one in the same practice.
ROWS = _points("good", 10, 0.8, 0.8) + _points("bad", 10, 0.95, 0.2, start=100)


def test_each_model_is_graded_on_its_own_trajectory():
    db = _DB(ROWS)
    good = compute_dynamic_thresholds("prac", db, min_transactions=5, practitioner_model="good")["noetic"]
    bad = compute_dynamic_thresholds("prac", db, min_transactions=5, practitioner_model="bad")["noetic"]
    assert good["basis"] == bad["basis"] == "practitioner"
    assert good["practitioner_points"] == bad["practitioner_points"] == 10
    assert good["brier_score"] < bad["brier_score"]
    assert good["ready_know_threshold"] < bad["ready_know_threshold"], "the overconfident model gets the tighter gate"


def test_the_pooled_practice_reading_differs_from_both():
    """Positive control: without the key the two models are averaged together."""
    db = _DB(ROWS)
    pooled = compute_dynamic_thresholds("prac", db, min_transactions=5)["noetic"]
    good = compute_dynamic_thresholds("prac", db, min_transactions=5, practitioner_model="good")["noetic"]
    assert pooled["basis"] == "practice"
    assert pooled["practitioner_points"] is None
    assert pooled["brier_score"] != good["brier_score"]


def test_a_model_with_too_few_points_falls_back_and_says_so():
    db = _DB(ROWS + _points("new", 2, 0.5, 0.5, start=200))
    r = compute_dynamic_thresholds("prac", db, min_transactions=5, practitioner_model="new")["noetic"]
    assert r["basis"] == "practice"
    assert r["practitioner_points"] == 2
    assert r["transactions_analyzed"] > 2


def test_rows_without_a_model_count_for_the_practice_only():
    """History from before migration 074 has NULL and is never attributed to a model."""
    db = _DB(_points(None, 10, 0.9, 0.1) + _points("m", 6, 0.7, 0.7, start=100))
    r = compute_dynamic_thresholds("prac", db, min_transactions=5, practitioner_model="m")["noetic"]
    assert r["basis"] == "practitioner" and r["transactions_analyzed"] == 6
