"""
Tests for A3 Three-Vector Storage Schema (SPEC 1 Part 3).

Verifies: migration up/down, ComplianceStatus enum, GroundedAssessment
new fields, legacy data backward compatibility, trajectory state_type.
"""

from __future__ import annotations

import sqlite3

from empirica.core.post_test.compliance_status import ComplianceStatus
from empirica.core.post_test.mapper import GroundedAssessment, GroundedVectorEstimate

# ---------------------------------------------------------------------------
# ComplianceStatus enum
# ---------------------------------------------------------------------------


class TestComplianceStatus:
    def test_string_compatible(self):
        assert ComplianceStatus.GROUNDED == "grounded"
        assert ComplianceStatus.COMPLETE == "complete"

    def test_writes_to_trajectory(self):
        assert ComplianceStatus.GROUNDED.writes_to_trajectory
        assert ComplianceStatus.COMPLETE.writes_to_trajectory
        assert not ComplianceStatus.INSUFFICIENT_EVIDENCE.writes_to_trajectory
        assert not ComplianceStatus.ITERATION_NEEDED.writes_to_trajectory
        assert not ComplianceStatus.ITERATION_IN_PROGRESS.writes_to_trajectory
        assert not ComplianceStatus.MAX_ITERATIONS_EXCEEDED.writes_to_trajectory

    def test_feeds_feedback(self):
        assert ComplianceStatus.GROUNDED.feeds_feedback
        assert ComplianceStatus.COMPLETE.feeds_feedback
        assert not ComplianceStatus.UNGROUNDED_REMOTE_OPS.feeds_feedback
        assert not ComplianceStatus.ITERATION_NEEDED.feeds_feedback

    def test_all_values_present(self):
        expected = {
            "grounded",
            "insufficient_evidence",
            "ungrounded_remote_ops",
            "complete",
            "iteration_needed",
            "iteration_in_progress",
            "max_iterations_exceeded",
            "manual_override",
        }
        assert {s.value for s in ComplianceStatus} == expected


# ---------------------------------------------------------------------------
# GroundedAssessment new fields
# ---------------------------------------------------------------------------


class TestGroundedAssessmentNewFields:
    def _make_assessment(self, **kwargs) -> GroundedAssessment:
        defaults = {
            "session_id": "test-session",
            "self_assessed": {"know": 0.8},
            "grounded": {"know": GroundedVectorEstimate("know", 0.7, 0.9, 3, "git")},
            "calibration_gaps": {"know": 0.1},
            "grounded_coverage": 0.5,
            "overall_calibration_score": 0.1,
        }
        defaults.update(kwargs)
        return GroundedAssessment(**defaults)

    def test_backward_compatible_defaults(self):
        """New fields default to None — legacy code unaffected."""
        a = self._make_assessment()
        assert a.grounded_rationale is None
        assert a.criticality is None
        assert a.parent_transaction_id is None

    def test_new_fields_settable(self):
        a = self._make_assessment(
            grounded_rationale="I adjusted know because...",
            criticality="high",
            parent_transaction_id="parent-tx-123",
        )
        assert a.grounded_rationale == "I adjusted know because..."
        assert a.criticality == "high"
        assert a.parent_transaction_id == "parent-tx-123"

    def test_observed_alias(self):
        """The 'observed' property returns the same object as 'grounded'."""
        a = self._make_assessment()
        assert a.observed is a.grounded

    def test_insufficient_evidence_defaults_to_empty(self):
        a = self._make_assessment()
        assert a.insufficient_evidence_vectors == []

    def test_calibration_status_default(self):
        a = self._make_assessment()
        assert a.calibration_status == "grounded"

    def test_calibration_status_uses_enum_values(self):
        a = self._make_assessment(calibration_status=ComplianceStatus.COMPLETE)
        assert a.calibration_status == "complete"


# ---------------------------------------------------------------------------
# Migration 035 tests
# ---------------------------------------------------------------------------


class TestMigration035:
    def _run_migration(self, conn: sqlite3.Connection):
        from empirica.data.migrations.migrations import migration_035_three_vector_storage

        cursor = conn.cursor()
        migration_035_three_vector_storage(cursor)
        conn.commit()

    def _setup_pre_migration_db(self) -> sqlite3.Connection:
        """Create an in-memory DB with pre-migration schema."""
        conn = sqlite3.connect(":memory:")
        c = conn.cursor()
        # Minimal pre-migration grounded_verifications
        c.execute("""
            CREATE TABLE grounded_verifications (
                verification_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                ai_id TEXT NOT NULL,
                self_assessed_vectors TEXT NOT NULL,
                grounded_vectors TEXT,
                calibration_gaps TEXT,
                grounded_coverage REAL,
                overall_calibration_score REAL,
                evidence_count INTEGER DEFAULT 0,
                sources_available TEXT,
                sources_failed TEXT,
                domain TEXT,
                goal_id TEXT,
                phase TEXT DEFAULT 'combined',
                created_at REAL
            )
        """)
        c.execute("""
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
                phase TEXT DEFAULT 'combined'
            )
        """)
        conn.commit()
        return conn

    def test_migration_adds_columns(self):
        conn = self._setup_pre_migration_db()
        self._run_migration(conn)

        # Check new columns exist
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(grounded_verifications)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "observed_vectors" in cols
        assert "grounded_rationale" in cols
        assert "criticality" in cols
        assert "compliance_status" in cols
        assert "parent_transaction_id" in cols

    def test_migration_adds_state_type(self):
        conn = self._setup_pre_migration_db()
        self._run_migration(conn)

        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(calibration_trajectory)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "state_type" in cols

    def test_migration_creates_compliance_checks_table(self):
        conn = self._setup_pre_migration_db()
        self._run_migration(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='compliance_checks'")
        assert cursor.fetchone() is not None

    def test_migration_idempotent(self):
        """A second run must not raise AND must not change anything.

        "Ran twice without raising" misses the failure mode that matters: a
        backfill that INSERTs again on the second run does not raise. The
        fingerprint (columns + row counts, every table) is the actual no-op
        claim, asserted instead of implied.
        """
        from tests.schema_shapes import db_fingerprint

        conn = self._setup_pre_migration_db()
        self._run_migration(conn)
        before = db_fingerprint(conn)
        self._run_migration(conn)  # second run — must be a REAL no-op
        assert db_fingerprint(conn) == before, "second migration run changed schema or data"

    def test_legacy_rows_readable_after_migration(self):
        """Pre-migration data is still readable with NULL new columns."""
        conn = self._setup_pre_migration_db()
        cursor = conn.cursor()

        # Insert pre-migration row
        cursor.execute("""
            INSERT INTO grounded_verifications
            (verification_id, session_id, ai_id, self_assessed_vectors, grounded_vectors, created_at)
            VALUES ('v1', 's1', 'claude', '{"know": 0.8}', '{"know": 0.7}', 1000.0)
        """)
        conn.commit()

        # Run migration
        self._run_migration(conn)

        # Read back — new columns should be NULL
        cursor.execute(
            "SELECT observed_vectors, grounded_rationale, criticality, compliance_status FROM grounded_verifications WHERE verification_id = 'v1'"
        )
        row = cursor.fetchone()
        assert row == (None, None, None, None)

    def test_legacy_trajectory_rows_have_default_state_type(self):
        conn = self._setup_pre_migration_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO calibration_trajectory
            (point_id, session_id, ai_id, vector_name, self_assessed, grounded, gap, timestamp)
            VALUES ('p1', 's1', 'claude', 'know', 0.8, 0.7, 0.1, 1000.0)
        """)
        conn.commit()

        self._run_migration(conn)

        cursor.execute("SELECT state_type FROM calibration_trajectory WHERE point_id = 'p1'")
        row = cursor.fetchone()
        assert row[0] == "grounded"
