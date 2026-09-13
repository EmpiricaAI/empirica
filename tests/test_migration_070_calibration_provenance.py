"""Migration 070 — a grounded value carries where it came from.

`calibration_trajectory` stored `grounded` and `gap` and nothing about the scope
that produced them, so `grounded=0.0` from a transaction-scoped ratio and 0.0 from
a session-cumulative one were byte-identical rows. Downstream reads either as
calibration error.

These tests cover the column, the write path that fills it, and the scope marker
that makes the collector's fallback legible — the three places the ambiguity lived.
"""

from __future__ import annotations

import json
import sqlite3

from empirica.core.post_test.mapper import GroundedAssessment, GroundedVectorEstimate
from empirica.core.post_test.trajectory_tracker import TrajectoryTracker
from empirica.data.migrations.migrations import migration_070_calibration_trajectory_provenance

_NEW_COLUMNS = {"transaction_id", "evidence_count", "primary_source", "grounded_raw"}


def _trajectory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE calibration_trajectory (
            point_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            ai_id TEXT NOT NULL,
            vector_name TEXT NOT NULL,
            self_assessed REAL NOT NULL,
            grounded REAL,
            gap REAL,
            domain TEXT,
            goal_id TEXT,
            timestamp REAL NOT NULL,
            phase TEXT DEFAULT 'combined',
            state_type TEXT DEFAULT 'grounded'
        )
    """)
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, ai_id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s1', 'empirica')")
    return conn


def _cols(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(calibration_trajectory)").fetchall()}


class _Db:
    """Minimal stand-in for the db object TrajectoryTracker takes."""

    def __init__(self, conn):
        self.conn = conn


def test_adds_the_provenance_columns():
    conn = _trajectory_db()
    migration_070_calibration_trajectory_provenance(conn.cursor())
    conn.commit()
    assert _cols(conn) >= _NEW_COLUMNS


def test_columns_are_nullable():
    """Historical rows must survive: their provenance is what was never recorded."""
    conn = _trajectory_db()
    migration_070_calibration_trajectory_provenance(conn.cursor())
    conn.commit()
    conn.execute(
        "INSERT INTO calibration_trajectory (point_id, session_id, ai_id, vector_name, self_assessed, timestamp) "
        "VALUES ('p1', 's1', 'empirica', 'completion', 1.0, 0.0)"
    )
    row = conn.execute("SELECT transaction_id, evidence_count, grounded_raw FROM calibration_trajectory").fetchone()
    assert row == (None, None, None)


def test_idempotent():
    conn = _trajectory_db()
    cur = conn.cursor()
    migration_070_calibration_trajectory_provenance(cur)
    migration_070_calibration_trajectory_provenance(cur)  # must not raise
    conn.commit()
    assert _cols(conn) >= _NEW_COLUMNS


def test_a_recorded_point_carries_its_raw_counts():
    """The whole point: `completed=0, total=29` visible on the row.

    A zero with these counts beside it is self-diagnosing; the same zero without
    them cost two practices a diagnosis and one wrong mechanism.
    """
    conn = _trajectory_db()
    migration_070_calibration_trajectory_provenance(conn.cursor())
    conn.commit()

    assessment = GroundedAssessment(
        session_id="s1",
        self_assessed={"completion": 1.0},
        grounded={
            "completion": GroundedVectorEstimate(
                vector_name="completion",
                estimated_value=0.0,
                confidence=0.8,
                evidence_count=1,
                primary_source="goals",
                contributing={"subtask_completion_ratio": {"completed": 0, "total": 29, "scope": "session"}},
            )
        },
        calibration_gaps={"completion": 1.0},
        grounded_coverage=1.0,
        overall_calibration_score=0.0,
        phase="praxic",
    )

    TrajectoryTracker(_Db(conn)).record_trajectory_point("s1", assessment, phase="praxic", transaction_id="tx-42")

    row = conn.execute(
        "SELECT grounded, transaction_id, evidence_count, primary_source, grounded_raw "
        "FROM calibration_trajectory WHERE vector_name = 'completion'"
    ).fetchone()

    assert row[0] == 0.0, "the observation itself must still be recorded"
    assert row[1] == "tx-42", "without the transaction the row cannot be placed in a window"
    assert row[2] == 1
    assert row[3] == "goals"
    raw = json.loads(row[4])
    assert raw["subtask_completion_ratio"] == {"completed": 0, "total": 29, "scope": "session"}


def test_unserialisable_raw_evidence_costs_the_counts_not_the_row():
    """Degrade, but never lose the observation — and never raise at POSTFLIGHT.

    The payload has to genuinely defeat the serialiser. A bare `object()` does
    NOT: `json.dumps(..., default=str)` stringifies it happily, so a test built
    on one exercises the success path while claiming to test the failure path —
    green for a reason that has nothing to do with its name. A circular
    reference is the case that actually raises.
    """
    conn = _trajectory_db()
    migration_070_calibration_trajectory_provenance(conn.cursor())
    conn.commit()

    circular: dict = {}
    circular["self"] = circular

    assessment = GroundedAssessment(
        session_id="s1",
        self_assessed={"completion": 0.5},
        grounded={
            "completion": GroundedVectorEstimate(
                vector_name="completion",
                estimated_value=0.5,
                confidence=0.5,
                evidence_count=1,
                primary_source="goals",
                contributing={"weird": circular},
            )
        },
        calibration_gaps={"completion": 0.0},
        grounded_coverage=1.0,
        overall_calibration_score=1.0,
        phase="praxic",
    )

    recorded = TrajectoryTracker(_Db(conn)).record_trajectory_point(
        "s1", assessment, phase="praxic", transaction_id="tx-43"
    )

    assert recorded == 1
    row = conn.execute("SELECT grounded, evidence_count, grounded_raw FROM calibration_trajectory").fetchone()
    assert row[0] == 0.5, "the observation survives a serialisation failure"
    assert row[1] == 1, "provenance that survives serialisation is still written"
    assert row[2] is None, "only the raw counts are lost — and they are lost as NULL, not as a lie"
