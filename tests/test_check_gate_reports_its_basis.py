"""CHECK gates a practice on ITS OWN calibration, and says what the verdict rests on.

empirica-cortex, prop_vv5u7l4pr5b6bkfrnbjl4xqhg4 (routed by David):

- `compute_dynamic_thresholds(ai_id="claude-code")` was a literal, so every
  practice was gated by a calibration history that was not its own. Measured
  2026-09-21: cortex's store gives Brier 0.1246 under claude-code and 0.0322
  under its own ai_id; core's gives 0.0324 and 0.0718. Wrong in both directions,
  and well-formed either way, so nothing in the response said whose history it was.
- The response reported the inflation and never the threshold, so a gated
  practitioner could not check the verdict. Nor did it say that `work_type:
  audit` selects the `rigorous` profile, whose gate is 0.20 — which is how
  uncertainty 0.20 can gate while 0.25 passes in another transaction.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.cli.command_handlers import _workflow_check as wc


class _DB:
    def __init__(self, ai_id: str | None):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE sessions (session_id TEXT, ai_id TEXT)")
        if ai_id is not None:
            self.conn.execute("INSERT INTO sessions VALUES ('s', ?)", (ai_id,))

    def close(self):
        self.conn.close()


def test_the_sessions_own_ai_id_is_used():
    assert wc._session_ai_id(_DB("empirica-cortex"), "s") == "empirica-cortex"


def test_a_session_with_no_ai_id_falls_back_and_an_unreadable_store_says_so(caplog):
    assert wc._session_ai_id(_DB(None), "s") == "claude-code"
    broken = _DB("x")
    broken.conn.execute("DROP TABLE sessions")
    assert wc._session_ai_id(broken, "s") == "claude-code"
    assert "could not resolve" in caplog.text


@pytest.fixture
def thresholds(monkeypatch):
    """Run _check_load_dynamic_thresholds with the store and transaction faked."""
    seen: dict = {}

    def fake_compute(ai_id, db, base_thresholds=None):
        seen["ai_id"] = ai_id
        seen["base"] = base_thresholds
        return {"source": "static"}

    def run(ai_id="empirica-cortex", profile=None):
        import empirica.core.post_test.dynamic_thresholds as dt

        monkeypatch.setattr(dt, "compute_dynamic_thresholds", fake_compute)
        monkeypatch.setattr(wc, "_get_db_for_session", lambda _sid: _DB(ai_id))
        monkeypatch.setattr(wc.R, "transaction_id", staticmethod(lambda: "tx"))
        monkeypatch.setattr(wc.R, "transaction_read", staticmethod(lambda: {"cascade_profile": profile}))
        monkeypatch.setattr(wc.R, "project_path", staticmethod(lambda: None))
        return wc._check_load_dynamic_thresholds("s"), seen

    return run


def test_calibration_history_is_read_under_the_sessions_ai_id(thresholds):
    (_, _, info), seen = thresholds(ai_id="empirica-cortex")
    assert seen["ai_id"] == "empirica-cortex"
    assert info["basis"]["calibration_ai_id"] == "empirica-cortex"


def test_the_rigorous_profile_is_reported_as_the_basis(thresholds):
    (_, threshold, info), _ = thresholds(profile="rigorous")
    assert threshold == pytest.approx(0.20)
    assert info["basis"]["cascade_profile"] == "rigorous"
    assert info["basis"]["base_threshold"] == pytest.approx(0.20)
    assert info["basis"]["uncertainty_threshold"] == pytest.approx(0.20)


def test_positive_control_the_default_profile_keeps_the_default_gate(thresholds):
    (_, threshold, info), _ = thresholds(profile="default")
    assert threshold == pytest.approx(0.35)
    assert info["basis"]["cascade_profile"] == "default"


def test_the_reason_names_the_path_the_gate_took():
    quiet = {"recommend_proceed": False}
    assert wc._gate_reason(0.20, 0.335, quiet, 1) == "uncertainty 0.200 <= threshold 0.335"
    assert wc._gate_reason(0.20, 0.185, quiet, 2) == "uncertainty 0.200 > threshold 0.185"
    assert "diminishing returns" in wc._gate_reason(0.25, 0.185, {"recommend_proceed": True}, 3)
    assert "max rounds" in wc._gate_reason(0.38, 0.335, quiet, 5)


def test_the_gate_is_monotonic_for_a_fixed_threshold():
    """cortex's reproducer looked non-monotonic; with one threshold it cannot be."""
    quiet = {"recommend_proceed": False}
    decisions = [
        wc._check_gate_decision({"uncertainty": u}, 0.335, quiet, 1, None)[1]
        for u in (0.10, 0.20, 0.25, 0.33, 0.34, 0.50)
    ]
    assert decisions == ["proceed", "proceed", "proceed", "proceed", "investigate", "investigate"]
