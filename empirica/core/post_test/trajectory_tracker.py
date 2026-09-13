"""
Trajectory Tracker

Tracks POSTFLIGHT-to-POSTFLIGHT evolution per vector.
Unlike the existing calibration (which compares PREFLIGHT→POSTFLIGHT within a session),
this compares POSTFLIGHTs across sessions to detect calibration trends.

Key question: Is the gap between self-assessment and objective evidence
closing, widening, or stable over time?
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from .mapper import UNGROUNDABLE_VECTORS, GroundedAssessment

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryPoint:
    """A single point in the calibration trajectory."""

    point_id: str
    session_id: str
    ai_id: str
    vector_name: str
    self_assessed: float
    grounded: float | None
    gap: float | None
    domain: str | None
    goal_id: str | None
    timestamp: float


@dataclass
class CalibrationTrend:
    """Detected trend for a vector's calibration gap."""

    vector_name: str
    direction: str  # "closing", "widening", "stable"
    slope: float  # Negative = closing, positive = widening
    recent_gap: float  # Most recent gap value
    mean_gap: float
    points_analyzed: int


class TrajectoryTracker:
    """Tracks POSTFLIGHT-to-POSTFLIGHT calibration evolution."""

    def __init__(self, db):
        self.db = db
        self.conn = db.conn

    def record_trajectory_point(
        self,
        session_id: str,
        assessment: GroundedAssessment,
        domain: str | None = None,
        goal_id: str | None = None,
        phase: str = "combined",
        transaction_id: str | None = None,
    ) -> int:
        """
        Record a trajectory point for each vector in the assessment.

        Called after each POSTFLIGHT + grounded verification.
        Phase can be "noetic", "praxic", or "combined".
        Returns number of points recorded.

        Each row also carries the PROVENANCE of its grounded value — which
        transaction, how many evidence items, which source dominated, and the
        raw counts behind them. A bare number cannot say whether it measured the
        thing the self-assessment was describing, and that ambiguity is not
        theoretical: a session-cumulative subtask ratio and a transaction-scoped
        one both write a plain 0.0 here, and downstream reads either as
        calibration error (mesh-support / Carly R. Anderson, 2026-09-13).
        """
        cursor = self.conn.cursor()

        # Get AI ID
        cursor.execute(
            "SELECT ai_id FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            return 0
        ai_id = row[0]

        timestamp = datetime.now().timestamp()
        recorded = 0

        # Vectors that were instrument-blind for this work_type — skip
        # entirely so we don't pollute trajectory with absent-signal points
        # that downstream drift analysis could mistake for real overconfidence.
        # The AI's self-assessment stands as the best available estimate
        # without any false grounded value to subtract from.
        insufficient = set(getattr(assessment, "insufficient_evidence_vectors", []) or [])

        for vector_name, self_val in assessment.self_assessed.items():
            if vector_name in UNGROUNDABLE_VECTORS:
                continue
            if vector_name in insufficient:
                continue  # Honest absence — no row written.

            grounded_est = assessment.grounded.get(vector_name)
            grounded_val = grounded_est.estimated_value if grounded_est else None
            gap = assessment.calibration_gaps.get(vector_name)

            # Provenance travels with the number or it is not recoverable later.
            # Serialisation must never cost a trajectory row: a value that cannot
            # be JSON-encoded loses its raw counts, not its observation.
            evidence_count = grounded_est.evidence_count if grounded_est else None
            primary_source = grounded_est.primary_source if grounded_est else None
            grounded_raw = None
            contributing = getattr(grounded_est, "contributing", None) if grounded_est else None
            if contributing:
                try:
                    grounded_raw = json.dumps(contributing, default=str)
                except (TypeError, ValueError) as exc:
                    logger.debug("trajectory: raw evidence for %s not serialisable (%s)", vector_name, exc)

            point_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO calibration_trajectory (
                    point_id, session_id, ai_id, vector_name,
                    self_assessed, grounded, gap,
                    domain, goal_id, timestamp, phase,
                    transaction_id, evidence_count, primary_source, grounded_raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    point_id,
                    session_id,
                    ai_id,
                    vector_name,
                    self_val,
                    grounded_val,
                    gap,
                    domain,
                    goal_id,
                    timestamp,
                    phase,
                    transaction_id,
                    evidence_count,
                    primary_source,
                    grounded_raw,
                ),
            )
            recorded += 1

        self.conn.commit()
        return recorded

    def get_trajectory(
        self,
        ai_id: str,
        vector_name: str,
        lookback: int = 20,
        phase: str | None = None,
    ) -> list[TrajectoryPoint]:
        """Get recent trajectory points for a vector, optionally filtered by phase."""
        cursor = self.conn.cursor()

        if phase:
            cursor.execute(
                """
                SELECT point_id, session_id, ai_id, vector_name,
                       self_assessed, grounded, gap,
                       domain, goal_id, timestamp
                FROM calibration_trajectory
                WHERE ai_id = ? AND vector_name = ? AND phase = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (ai_id, vector_name, phase, lookback),
            )
        else:
            cursor.execute(
                """
                SELECT point_id, session_id, ai_id, vector_name,
                       self_assessed, grounded, gap,
                       domain, goal_id, timestamp
                FROM calibration_trajectory
                WHERE ai_id = ? AND vector_name = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (ai_id, vector_name, lookback),
            )

        points = []
        for row in cursor.fetchall():
            points.append(
                TrajectoryPoint(
                    point_id=row[0],
                    session_id=row[1],
                    ai_id=row[2],
                    vector_name=row[3],
                    self_assessed=row[4],
                    grounded=row[5],
                    gap=row[6],
                    domain=row[7],
                    goal_id=row[8],
                    timestamp=row[9],
                )
            )

        # Return in chronological order
        points.reverse()
        return points

    def detect_calibration_trend(
        self,
        ai_id: str,
        lookback: int = 10,
        phase: str | None = None,
    ) -> dict[str, CalibrationTrend]:
        """
        Detect calibration trend per vector, optionally filtered by phase.

        Uses simple linear regression on absolute gap values.
        Negative slope = gap is closing (improving).
        Positive slope = gap is widening (degrading).
        Near-zero slope = stable.

        Requires at least 3 data points to compute trend.
        """
        cursor = self.conn.cursor()

        # Get all vectors with trajectory data
        if phase:
            cursor.execute(
                """
                SELECT DISTINCT vector_name
                FROM calibration_trajectory
                WHERE ai_id = ? AND phase = ?
            """,
                (ai_id, phase),
            )
        else:
            cursor.execute(
                """
                SELECT DISTINCT vector_name
                FROM calibration_trajectory
                WHERE ai_id = ?
            """,
                (ai_id,),
            )
        vectors = [row[0] for row in cursor.fetchall()]

        trends = {}
        for vector in vectors:
            points = self.get_trajectory(ai_id, vector, lookback, phase=phase)

            # Need at least 3 points with grounded values
            grounded_points = [p for p in points if p.gap is not None]
            if len(grounded_points) < 3:
                continue

            # Simple linear regression on absolute gaps
            n = len(grounded_points)
            abs_gaps = [abs(p.gap) for p in grounded_points]  # type: ignore[arg-type]
            x_vals = list(range(n))

            # Slope via least squares
            x_mean = sum(x_vals) / n
            y_mean = sum(abs_gaps) / n

            numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, abs_gaps))
            denominator = sum((x - x_mean) ** 2 for x in x_vals)

            if denominator == 0:
                slope = 0.0
            else:
                slope = numerator / denominator

            # Classify trend
            if slope < -0.01:
                direction = "closing"
            elif slope > 0.01:
                direction = "widening"
            else:
                direction = "stable"

            trends[vector] = CalibrationTrend(
                vector_name=vector,
                direction=direction,
                slope=round(slope, 4),
                recent_gap=grounded_points[-1].gap or 0.0,
                mean_gap=round(y_mean, 4),
                points_analyzed=n,
            )

        return trends

    def get_trajectory_summary(self, ai_id: str) -> dict:
        """
        Get a summary of calibration trajectory for reporting.

        Returns structured data suitable for CLI output or .breadcrumbs.yaml.
        """
        trends = self.detect_calibration_trend(ai_id)

        if not trends:
            return {
                "status": "insufficient_data",
                "message": "Need at least 3 POSTFLIGHT sessions with grounded evidence",
                "vectors": {},
            }

        closing = [v for v, t in trends.items() if t.direction == "closing"]
        widening = [v for v, t in trends.items() if t.direction == "widening"]
        stable = [v for v, t in trends.items() if t.direction == "stable"]

        # Overall direction: majority vote
        if len(closing) > len(widening):
            overall = "closing"
        elif len(widening) > len(closing):
            overall = "widening"
        else:
            overall = "stable"

        total_points = sum(t.points_analyzed for t in trends.values())

        return {
            "status": "active",
            "overall_direction": overall,
            "sessions_analyzed": total_points // max(len(trends), 1),
            "vectors": {
                name: {
                    "direction": t.direction,
                    "slope": t.slope,
                    "recent_gap": t.recent_gap,
                    "mean_gap": t.mean_gap,
                    "points": t.points_analyzed,
                }
                for name, t in trends.items()
            },
            "summary": {
                "closing": closing,
                "widening": widening,
                "stable": stable,
            },
        }
