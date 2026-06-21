"""
Calibration Insights Analyzer

Analyzes recent grounded verification records to detect systemic calibration
patterns. Feeds back into improving evidence collection methods over time.

Patterns detected:
- chronic_overestimate / chronic_underestimate: Same direction bias in >70% of records
- evidence_gap: Vector has low evidence count across records
- phase_mismatch: Large gap in one phase but not the other
- volatile: Gap direction flips frequently (noisy evidence or inconsistent self-assessment)

These insights are INFORMATIVE — they surface drift patterns for the AI to
investigate, not commands to mechanically adjust vectors. A chronic overestimate
pattern may reflect genuine proxy limitations (e.g., deep understanding not
captured by test pass rates) rather than AI miscalibration. The AI should
examine each insight critically rather than treat it as an error to correct.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CalibrationInsight:
    vector: str
    phase: str                          # "noetic", "praxic", or "both"
    pattern: str                        # Pattern type
    severity: float                     # 0.0-1.0
    description: str
    suggestion: str                     # Machine-readable improvement hint
    evidence_sources: list[str] = field(default_factory=list)
    observation_count: int = 0


class CalibrationInsightsAnalyzer:
    """Analyzes recent grounded verifications for systemic calibration patterns."""

    # Minimum records before patterns are considered meaningful
    MIN_OBSERVATIONS = 5
    # Minimum severity to surface an insight
    MIN_SEVERITY = 0.3

    def __init__(self, db, session_id: str, lookback: int = 10):
        self.db = db
        self.session_id = session_id
        self.lookback = lookback

    def analyze(self) -> list[CalibrationInsight]:
        """Run all pattern detectors on recent verification records."""
        records = self._get_recent_verifications()
        if len(records) < self.MIN_OBSERVATIONS:
            return []

        insights = []
        insights.extend(self._detect_chronic_bias(records))
        insights.extend(self._detect_evidence_gaps(records))
        insights.extend(self._detect_phase_mismatch(records))
        insights.extend(self._detect_volatile_vectors(records))
        return [i for i in insights if i.severity >= self.MIN_SEVERITY]

    def store_insights(self, insights: list[CalibrationInsight], transaction_id: str | None = None):
        """Store insights in the calibration_insights table."""
        self._ensure_table()
        cursor = self.db.conn.cursor()
        import json
        for insight in insights:
            cursor.execute(
                """INSERT OR REPLACE INTO calibration_insights
                   (insight_id, session_id, transaction_id, vector, phase,
                    pattern, severity, description, suggestion,
                    evidence_sources, observation_count, acted_on, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    self.session_id,
                    transaction_id,
                    insight.vector,
                    insight.phase,
                    insight.pattern,
                    insight.severity,
                    insight.description,
                    insight.suggestion,
                    json.dumps(insight.evidence_sources),
                    insight.observation_count,
                    False,
                    time.time(),
                ),
            )
        self.db.conn.commit()

    def _ensure_table(self):
        """Create calibration_insights table if it doesn't exist."""
        self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_insights (
                insight_id TEXT PRIMARY KEY,
                session_id TEXT,
                transaction_id TEXT,
                vector TEXT,
                phase TEXT,
                pattern TEXT,
                severity REAL,
                description TEXT,
                suggestion TEXT,
                evidence_sources TEXT,
                observation_count INTEGER,
                acted_on BOOLEAN DEFAULT FALSE,
                created_at REAL
            )
        """)
        self.db.conn.commit()

    def _get_recent_verifications(self) -> list[dict]:
        """Fetch the last N grounded verification records."""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(
                """SELECT verification_id, session_id, phase, evidence_count,
                          grounded_coverage, overall_calibration_score,
                          calibration_gaps, created_at
                   FROM grounded_verifications
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (self.lookback,),
            )
            rows = cursor.fetchall()
            import json
            records = []
            for row in rows:
                gaps = {}
                if row[6]:
                    try:
                        gaps = json.loads(row[6])
                    except (json.JSONDecodeError, TypeError):
                        pass
                records.append({
                    'verification_id': row[0],
                    'session_id': row[1],
                    'phase': row[2],
                    'evidence_count': row[3],
                    'grounded_coverage': row[4],
                    'calibration_score': row[5],
                    'gaps': gaps,
                    'created_at': row[7],
                })
            return records
        except Exception as e:
            logger.debug(f"Failed to fetch verification records: {e}")
            return []

    def _detect_chronic_bias(self, records: list[dict]) -> list[CalibrationInsight]:
        """Detect vectors that are consistently over/under-estimated."""
        insights = []
        # Collect gap values per vector across all records
        vector_gaps: dict[str, list[float]] = {}
        for record in records:
            for vector, gap in record.get('gaps', {}).items():
                if isinstance(gap, (int, float)):
                    vector_gaps.setdefault(vector, []).append(gap)

        for vector, gaps in vector_gaps.items():
            if len(gaps) < self.MIN_OBSERVATIONS:
                continue

            positive_count = sum(1 for g in gaps if g > 0.05)  # Overestimate threshold
            negative_count = sum(1 for g in gaps if g < -0.05)  # Underestimate threshold
            total = len(gaps)
            mean_gap = sum(gaps) / total
            abs_mean = abs(mean_gap)

            if positive_count / total > 0.7:
                severity = min(1.0, abs_mean * 3)  # Scale: 0.33 gap = 1.0 severity
                insights.append(CalibrationInsight(
                    vector=vector,
                    phase="both",
                    pattern="chronic_overestimate",
                    severity=severity,
                    description=(
                        f"{vector} overestimated in {positive_count}/{total} "
                        f"calibrations (mean gap +{abs_mean:.2f})"
                    ),
                    suggestion=f"reduce_self_assessment:{vector}",
                    observation_count=total,
                ))
            elif negative_count / total > 0.7:
                severity = min(1.0, abs_mean * 3)
                insights.append(CalibrationInsight(
                    vector=vector,
                    phase="both",
                    pattern="chronic_underestimate",
                    severity=severity,
                    description=(
                        f"{vector} underestimated in {negative_count}/{total} "
                        f"calibrations (mean gap {mean_gap:.2f})"
                    ),
                    suggestion=f"increase_self_assessment:{vector}",
                    observation_count=total,
                ))

        return insights

    def _detect_evidence_gaps(self, records: list[dict]) -> list[CalibrationInsight]:
        """Detect vectors with consistently low evidence coverage."""
        insights = []
        # Track which vectors appear in gaps (meaning they had evidence)
        vector_appearances: dict[str, int] = {}
        total_records = len(records)

        for record in records:
            for vector in record.get('gaps', {}):
                vector_appearances[vector] = vector_appearances.get(vector, 0) + 1

        # Also check coverage
        coverage_values = [r.get('grounded_coverage', 0) for r in records]
        mean_coverage = sum(coverage_values) / len(coverage_values) if coverage_values else 0

        # Vectors that appear in <30% of records have an evidence gap
        from empirica.core.post_test.grounded_calibration import UNGROUNDABLE_VECTORS
        expected_vectors = {
            'know', 'do', 'context', 'clarity', 'coherence',
            'signal', 'density', 'state', 'change', 'completion',
            'impact', 'uncertainty',
        } - UNGROUNDABLE_VECTORS

        for vector in expected_vectors:
            appearances = vector_appearances.get(vector, 0)
            if appearances < total_records * 0.3:
                severity = min(1.0, (1.0 - appearances / max(total_records, 1)) * 0.7)
                insights.append(CalibrationInsight(
                    vector=vector,
                    phase="both",
                    pattern="evidence_gap",
                    severity=severity,
                    description=(
                        f"{vector} has evidence in only {appearances}/{total_records} "
                        f"verifications (mean coverage {mean_coverage:.2f})"
                    ),
                    suggestion=f"add_evidence_source:{vector}",
                    observation_count=total_records,
                ))

        return insights

    def _detect_phase_mismatch(self, records: list[dict]) -> list[CalibrationInsight]:
        """Detect vectors with large gap in one phase but not the other."""
        insights = []
        # Separate noetic and praxic records
        noetic_gaps: dict[str, list[float]] = {}
        praxic_gaps: dict[str, list[float]] = {}

        for record in records:
            phase = record.get('phase', 'combined')
            for vector, gap in record.get('gaps', {}).items():
                if not isinstance(gap, (int, float)):
                    continue
                if phase == 'noetic':
                    noetic_gaps.setdefault(vector, []).append(gap)
                elif phase == 'praxic':
                    praxic_gaps.setdefault(vector, []).append(gap)

        # Compare mean gaps between phases
        all_vectors = set(noetic_gaps.keys()) | set(praxic_gaps.keys())
        for vector in all_vectors:
            n_gaps = noetic_gaps.get(vector, [])
            p_gaps = praxic_gaps.get(vector, [])
            if len(n_gaps) < 3 or len(p_gaps) < 3:
                continue

            n_mean = abs(sum(n_gaps) / len(n_gaps))
            p_mean = abs(sum(p_gaps) / len(p_gaps))

            # Significant mismatch: one phase > 2x the other
            # At least the larger phase must be meaningful (>0.1)
            if max(n_mean, p_mean) > 0.1 and min(n_mean, p_mean) > 0.02:
                ratio = max(n_mean, p_mean) / min(n_mean, p_mean)
                if ratio > 2.0:
                    weak_phase = "noetic" if n_mean > p_mean else "praxic"
                    severity = min(1.0, (ratio - 2.0) * 0.3 + 0.3)
                    insights.append(CalibrationInsight(
                        vector=vector,
                        phase=weak_phase,
                        pattern="phase_mismatch",
                        severity=severity,
                        description=(
                            f"{vector} gap is {ratio:.1f}x larger in {weak_phase} phase "
                            f"(noetic={n_mean:.2f}, praxic={p_mean:.2f})"
                        ),
                        suggestion=f"phase_evidence_imbalance:{vector}:{weak_phase}",
                        observation_count=len(n_gaps) + len(p_gaps),
                    ))

        return insights

    def _detect_volatile_vectors(self, records: list[dict]) -> list[CalibrationInsight]:
        """Detect vectors where gap direction flips frequently."""
        insights = []
        vector_gaps: dict[str, list[float]] = {}
        for record in records:
            for vector, gap in record.get('gaps', {}).items():
                if isinstance(gap, (int, float)) and abs(gap) > 0.02:
                    vector_gaps.setdefault(vector, []).append(gap)

        for vector, gaps in vector_gaps.items():
            if len(gaps) < self.MIN_OBSERVATIONS:
                continue

            # Count sign changes between consecutive gaps
            sign_changes = 0
            for i in range(1, len(gaps)):
                if (gaps[i] > 0) != (gaps[i - 1] > 0):
                    sign_changes += 1

            flip_rate = sign_changes / (len(gaps) - 1)
            if flip_rate > 0.5:
                severity = min(1.0, flip_rate * 0.8)
                insights.append(CalibrationInsight(
                    vector=vector,
                    phase="both",
                    pattern="volatile",
                    severity=severity,
                    description=(
                        f"{vector} gap flips direction in {sign_changes}/{len(gaps)-1} "
                        f"consecutive pairs (rate={flip_rate:.2f})"
                    ),
                    suggestion=f"stabilize_evidence:{vector}",
                    observation_count=len(gaps),
                ))

        return insights
