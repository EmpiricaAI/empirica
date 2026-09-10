"""Tests for phase-weighted calibration and calibration insights."""

from unittest.mock import MagicMock

from empirica.core.post_test.calibration_insights import (
    CalibrationInsight,
    CalibrationInsightsAnalyzer,
)
from empirica.core.post_test.grounded_calibration import (
    _compute_phase_weights,
)

# --- Phase Weight Tests ---


class TestPhaseWeights:
    def test_no_tool_counts(self):
        """Without tool counts, default to equal weights."""
        w = _compute_phase_weights(None, None, {"noetic": {}, "praxic": {}})
        assert w["noetic"] == 0.5
        assert w["praxic"] == 0.5
        assert w["source"] == "default"

    def test_empty_tool_counts(self):
        w = _compute_phase_weights({"noetic_tool_calls": 0, "praxic_tool_calls": 0}, None, {"noetic": {}})
        assert w["noetic"] == 0.5
        assert w["praxic"] == 0.5

    def test_noetic_dominant(self):
        """95% noetic tools. Praxic has evidence so floor applies -> 0.9/0.1."""
        w = _compute_phase_weights(
            {"noetic_tool_calls": 95, "praxic_tool_calls": 5},
            None,
            {"noetic": {}, "praxic": {}},
        )
        # Floor: praxic has evidence, so minimum 0.1 weight
        assert w["noetic"] == 0.9
        assert w["praxic"] == 0.1
        assert w["source"] == "tool_classification"

    def test_praxic_dominant(self):
        w = _compute_phase_weights(
            {"noetic_tool_calls": 10, "praxic_tool_calls": 90},
            None,
            {"noetic": {}, "praxic": {}},
        )
        assert w["noetic"] == 0.1
        assert w["praxic"] == 0.9

    def test_floor_prevents_zero_weight(self):
        """A phase with evidence should get at least 0.1 weight."""
        w = _compute_phase_weights(
            {"noetic_tool_calls": 1, "praxic_tool_calls": 99},
            None,
            {"noetic": {}, "praxic": {}},
        )
        assert w["noetic"] == 0.1
        assert w["praxic"] == 0.9

    def test_noetic_only(self):
        """noetic_only phase boundary = 100% noetic weight."""
        w = _compute_phase_weights(
            {"noetic_tool_calls": 50, "praxic_tool_calls": 10},
            {"noetic_only": True},
            {"noetic": {}},
        )
        assert w["noetic"] == 1.0
        assert w["praxic"] == 0.0
        assert w["source"] == "noetic_only"

    def test_no_results(self):
        w = _compute_phase_weights(
            {"noetic_tool_calls": 50, "praxic_tool_calls": 50},
            None,
            {},
        )
        assert w["source"] == "default"

    def test_balanced(self):
        w = _compute_phase_weights(
            {"noetic_tool_calls": 50, "praxic_tool_calls": 50},
            None,
            {"noetic": {}, "praxic": {}},
        )
        assert w["noetic"] == 0.5
        assert w["praxic"] == 0.5

    def test_floor_only_with_evidence(self):
        """Floor should NOT apply if the phase has no evidence results."""
        w = _compute_phase_weights(
            {"noetic_tool_calls": 1, "praxic_tool_calls": 99},
            None,
            {"praxic": {}},  # Only praxic has evidence
        )
        # noetic has no evidence, so no floor applied
        assert w["noetic"] == 0.01
        assert w["praxic"] == 0.99


# --- Calibration Insights Tests ---


class TestChronicBias:
    def _make_records(self, vector, gaps):
        return [{"gaps": {vector: g}, "phase": "combined", "evidence_count": 5, "grounded_coverage": 0.5} for g in gaps]

    def test_chronic_overestimate(self):
        # 8/10 positive gaps for 'know'
        records = self._make_records("know", [0.2, 0.3, 0.15, 0.25, 0.1, 0.2, 0.3, 0.15, -0.05, -0.02])
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        analyzer.MIN_OBSERVATIONS = 5
        insights = analyzer._detect_chronic_bias(records)
        assert len(insights) == 1
        assert insights[0].pattern == "chronic_overestimate"
        assert insights[0].vector == "know"
        assert insights[0].severity > 0

    def test_chronic_underestimate(self):
        records = self._make_records("uncertainty", [-0.3, -0.2, -0.15, -0.25, -0.1, -0.2, -0.3, -0.15, 0.02, 0.01])
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        analyzer.MIN_OBSERVATIONS = 5
        insights = analyzer._detect_chronic_bias(records)
        assert len(insights) == 1
        assert insights[0].pattern == "chronic_underestimate"

    def test_no_bias_balanced(self):
        records = self._make_records("know", [0.1, -0.1, 0.05, -0.05, 0.1, -0.1, 0.05, -0.05, 0.1, -0.1])
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        analyzer.MIN_OBSERVATIONS = 5
        insights = analyzer._detect_chronic_bias(records)
        assert len(insights) == 0

    def test_too_few_observations(self):
        records = self._make_records("know", [0.3, 0.3, 0.3])
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        analyzer.MIN_OBSERVATIONS = 5
        insights = analyzer._detect_chronic_bias(records)
        assert len(insights) == 0


class TestEvidenceGaps:
    def test_missing_vector(self):
        # Records that never mention 'do'
        records = [{"gaps": {"know": 0.1, "signal": 0.05}, "grounded_coverage": 0.3} for _ in range(10)]
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        analyzer.MIN_OBSERVATIONS = 5
        insights = analyzer._detect_evidence_gaps(records)
        # Should flag vectors that appear in <30% of records
        missing_vectors = {i.vector for i in insights}
        assert "do" in missing_vectors
        assert "completion" in missing_vectors

    def test_well_covered(self):
        all_vectors = [
            "know",
            "do",
            "context",
            "clarity",
            "coherence",
            "signal",
            "density",
            "state",
            "change",
            "completion",
            "impact",
            "uncertainty",
        ]
        records = [{"gaps": dict.fromkeys(all_vectors, 0.05), "grounded_coverage": 0.9} for _ in range(10)]
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        analyzer.MIN_OBSERVATIONS = 5
        insights = analyzer._detect_evidence_gaps(records)
        assert len(insights) == 0


class TestPhaseMismatch:
    def test_noetic_much_worse(self):
        records = []
        for _ in range(5):
            records.append({"gaps": {"know": 0.4}, "phase": "noetic"})
            records.append({"gaps": {"know": 0.1}, "phase": "praxic"})
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        # setattr, not attribute syntax: MIN_OBSERVATIONS is class-level and
        # the checker flags an instance shadow. The runtime semantics ARE an
        # instance shadow, deliberately - this test narrows the threshold
        # for one analyzer without touching the class.
        setattr(analyzer, "MIN_OBSERVATIONS", 3)  # noqa: B010
        insights = analyzer._detect_phase_mismatch(records)
        assert len(insights) == 1
        assert insights[0].phase == "noetic"
        assert insights[0].pattern == "phase_mismatch"

    def test_balanced_phases(self):
        records = []
        for _ in range(5):
            records.append({"gaps": {"know": 0.2}, "phase": "noetic"})
            records.append({"gaps": {"know": 0.2}, "phase": "praxic"})
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        # setattr, not attribute syntax: MIN_OBSERVATIONS is class-level and
        # the checker flags an instance shadow. The runtime semantics ARE an
        # instance shadow, deliberately - this test narrows the threshold
        # for one analyzer without touching the class.
        setattr(analyzer, "MIN_OBSERVATIONS", 3)  # noqa: B010
        insights = analyzer._detect_phase_mismatch(records)
        assert len(insights) == 0


class TestVolatileVectors:
    def test_high_volatility(self):
        # Alternating positive/negative gaps
        gaps = [0.2, -0.2, 0.15, -0.15, 0.3, -0.1, 0.2, -0.25]
        records = [{"gaps": {"know": g}} for g in gaps]
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        analyzer.MIN_OBSERVATIONS = 5
        insights = analyzer._detect_volatile_vectors(records)
        assert len(insights) == 1
        assert insights[0].pattern == "volatile"

    def test_stable_vector(self):
        gaps = [0.2, 0.18, 0.22, 0.19, 0.21, 0.17, 0.23]
        records = [{"gaps": {"know": g}} for g in gaps]
        analyzer = CalibrationInsightsAnalyzer.__new__(CalibrationInsightsAnalyzer)
        analyzer.MIN_OBSERVATIONS = 5
        insights = analyzer._detect_volatile_vectors(records)
        assert len(insights) == 0


class TestInsightsAnalyzer:
    def test_min_observations_filter(self):
        """Analyzer returns nothing with too few records."""
        db = MagicMock()
        db.conn.cursor.return_value.fetchall.return_value = [
            ("v1", "s1", "combined", 5, 0.5, 0.2, '{"know": 0.1}', "{}", 1000),
        ]
        analyzer = CalibrationInsightsAnalyzer(db, "test-session", lookback=10)
        insights = analyzer.analyze()
        assert insights == []

    def test_severity_filter(self):
        """Only insights with severity >= 0.3 are returned."""
        db = MagicMock()
        # Create enough records with very small gaps (low severity)
        rows = [(f"v{i}", "s1", "combined", 5, 0.5, 0.2, '{"know": 0.06}', "{}", 1000 + i) for i in range(10)]
        db.conn.cursor.return_value.fetchall.return_value = rows
        analyzer = CalibrationInsightsAnalyzer(db, "test-session", lookback=10)
        insights = analyzer.analyze()
        for i in insights:
            assert i.severity >= 0.3


class TestInsightStorage:
    def test_store_creates_table(self):
        db = MagicMock()
        analyzer = CalibrationInsightsAnalyzer(db, "test-session")
        insight = CalibrationInsight(
            vector="know",
            phase="noetic",
            pattern="chronic_overestimate",
            severity=0.6,
            description="test",
            suggestion="test_suggestion",
            observation_count=10,
        )
        analyzer.store_insights([insight], transaction_id="tx-123")
        # Should have called CREATE TABLE IF NOT EXISTS
        db.conn.execute.assert_called_once()
        assert "calibration_insights" in str(db.conn.execute.call_args)
        # Should have inserted the insight
        db.conn.cursor.return_value.execute.assert_called_once()
