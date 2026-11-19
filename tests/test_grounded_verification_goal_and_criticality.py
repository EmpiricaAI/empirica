"""A grounded verification names the goal it graded and the stakes the transaction declared (goal dffe317d).

Measured 2026-11-19: goal_id and criticality were empty in all 4198 grounded rows of core's own practice, though PREFLIGHT
names goal_ids and accepts criticality. Cortex's expertise export needs both (to join to goals and to gate on stakes); the
goal's work kind then joins through goal_id.
"""

from __future__ import annotations

import sqlite3

from empirica.cli.command_handlers._workflow_postflight import _verification_goal_and_criticality


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE goals (id TEXT, transaction_id TEXT, created_timestamp REAL)")
    conn.executemany(
        "INSERT INTO goals VALUES (?, ?, ?)",
        [("aaaa1111-0000-0000-0000-000000000001", "tx-1", 1.0), ("bbbb2222-0000-0000-0000-000000000002", "tx-1", 2.0)],
    )
    return conn


def test_the_first_goal_preflight_named_wins_and_a_prefix_resolves():
    tx = {"goal_ids": ["aaaa1111"], "criticality": "high"}
    assert _verification_goal_and_criticality(tx, _db(), "tx-9") == ("aaaa1111-0000-0000-0000-000000000001", "high")


def test_without_named_goals_the_newest_goal_created_in_the_transaction():
    assert _verification_goal_and_criticality({}, _db(), "tx-1") == ("bbbb2222-0000-0000-0000-000000000002", None)


def test_nothing_known_is_none_never_a_guess():
    assert _verification_goal_and_criticality({"goal_ids": ["ffff"]}, _db(), "tx-9") == (None, None)


def test_store_verification_writes_the_criticality_it_is_given(tmp_path):
    from types import SimpleNamespace

    from empirica.core.post_test.grounded_calibration import GroundedCalibrationManager

    conn = sqlite3.connect(str(tmp_path / "s.db"))
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    manager = GroundedCalibrationManager(db)
    assessment = SimpleNamespace(
        grounded={}, self_assessed={}, calibration_gaps={}, grounded_coverage=0.5, overall_calibration_score=0.1
    )
    bundle = SimpleNamespace(items=[], sources_available=[], sources_failed=[])
    vid = manager.store_verification("s1", assessment, bundle, goal_id="g1", criticality="high", transaction_id="tx")
    row = db.conn.execute(
        "SELECT goal_id, criticality FROM grounded_verifications WHERE verification_id = ?", (vid,)
    ).fetchone()
    assert tuple(row) == ("g1", "high")
    conn.close()
    db.close()


def test_postflight_captures_goal_ids_and_criticality_before_it_closes_the_transaction(tmp_path, monkeypatch):
    """The verification runs after the transaction file is closed, so close must carry both (the first wiring read the
    closed file and stamped nothing)."""
    import json

    from empirica.cli.command_handlers import _workflow_postflight as wp

    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "active_transaction_x.json").write_text(
        json.dumps({"transaction_id": "tx-1", "status": "open", "goal_ids": ["g1"], "criticality": "high"})
    )
    monkeypatch.setattr(wp.R, "transaction_write", lambda **k: None, raising=False)
    result = {"transaction_id": None, "tool_call_count": 0, "avg_turns": 0, "phase_tool_counts": None, "tool_trace": [],
              "edited_files": [], "work_context": None, "work_type": None}  # fmt: skip
    wp._postflight_close_and_capture_counters(result, str(tmp_path), "_x")
    assert result["goal_ids"] == ["g1"] and result["criticality"] == "high"
