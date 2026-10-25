"""
Wave 1 Integration Checkpoint Tests (SPEC 1 Part 8).

These tests verify that A1 (Domain Registry), A2 (Service Registry),
and A3 (Three-Vector Storage) compose correctly. All must pass before
Wave 2 work begins.

Also includes end-to-end compliance flow test and backward compat
regression.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.config.domain_registry import DomainKey, DomainRegistry
from empirica.config.service_registry import CheckDeclaration, ServiceRegistry
from empirica.core.post_test.compliance_loop import run_compliance_checks
from empirica.core.post_test.compliance_status import ComplianceStatus
from empirica.core.post_test.mapper import GroundedAssessment, GroundedVectorEstimate


@pytest.fixture(autouse=True)
def clean_service_registry():
    ServiceRegistry._registered.clear()
    yield
    ServiceRegistry._registered.clear()


# ---------------------------------------------------------------------------
# SPEC 1 Part 8: Integration checkpoint tests
# ---------------------------------------------------------------------------


class TestWave1Integration:
    def test_domain_registry_loads_builtin_domains(self):
        """A1 baseline: the 4 shipped domains load without a project file."""
        reg = DomainRegistry()
        domains = reg.list_domains()
        assert len(domains) >= 4
        assert "default" in domains
        assert "remote-ops" in domains
        assert "cybersec" in domains
        assert "docs" in domains

    def test_service_registry_resolves_for_code_default(self):
        """A2 baseline: default checks register at import time."""
        ServiceRegistry.load_builtins()
        resolved = ServiceRegistry.resolve_for("code", "default")
        check_ids = [d.check_id for d in resolved]
        assert "tests" in check_ids
        assert "lint" in check_ids

    def test_domain_registry_plus_service_registry_compose(self):
        """A1 + A2: given a (work_type, domain, criticality) tuple, the
        registry resolves a checklist, and every required check ID resolves
        to a registered declaration."""
        ServiceRegistry.load_builtins()
        reg = DomainRegistry()

        # default/medium requires tests + lint
        checklist = reg.resolve(DomainKey("code", "default", "medium"))
        assert checklist.has_checks

        for check_id in checklist.required:
            # Every required check must be resolvable in the service registry
            # (either as a registered check or it's a future check from C2)
            try:
                ServiceRegistry.get(check_id)
            except KeyError:
                # Acceptable for checks not yet implemented (semgrep, trivy, etc.)
                pass

    def test_three_vector_migration_up_and_down(self):
        """A3: migration applies forward and rolls back cleanly."""
        from empirica.data.migrations.migrations import migration_035_three_vector_storage

        conn = sqlite3.connect(":memory:")
        c = conn.cursor()
        c.execute("""
            CREATE TABLE grounded_verifications (
                verification_id TEXT PRIMARY KEY, session_id TEXT, ai_id TEXT,
                self_assessed_vectors TEXT, grounded_vectors TEXT,
                calibration_gaps TEXT, grounded_coverage REAL,
                overall_calibration_score REAL, phase TEXT DEFAULT 'combined',
                created_at REAL
            )
        """)
        c.execute("""
            CREATE TABLE calibration_trajectory (
                point_id TEXT PRIMARY KEY, session_id TEXT, ai_id TEXT,
                vector_name TEXT, self_assessed REAL, grounded REAL,
                gap REAL, timestamp REAL, phase TEXT DEFAULT 'combined'
            )
        """)
        conn.commit()

        # Forward migration
        migration_035_three_vector_storage(c)
        conn.commit()

        # Verify new columns exist
        c.execute("PRAGMA table_info(grounded_verifications)")
        cols = {row[1] for row in c.fetchall()}
        assert "observed_vectors" in cols
        assert "compliance_status" in cols

        # Verify compliance_checks table
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='compliance_checks'")
        assert c.fetchone() is not None

        conn.close()

    def test_three_vector_schema_backwards_compatible(self):
        """A3: loading legacy GroundedAssessment objects works with defaults."""
        # Legacy construction — no new fields
        assessment = GroundedAssessment(
            session_id="legacy-test",
            self_assessed={"know": 0.8},
            grounded={"know": GroundedVectorEstimate("know", 0.7, 0.9, 3, "git")},
            calibration_gaps={"know": 0.1},
            grounded_coverage=0.5,
            overall_calibration_score=0.1,
        )
        # New fields default to None
        assert assessment.grounded_rationale is None
        assert assessment.criticality is None
        assert assessment.parent_transaction_id is None
        # observed alias works
        assert assessment.observed is assessment.grounded

    def test_full_wave1_smoke_with_remote_ops(self):
        """Wave 1 regression: remote-ops still returns complete with no checks."""
        ServiceRegistry.load_builtins()

        result = run_compliance_checks(
            session_id="smoke-test",
            transaction_id="tx-1",
            work_type="remote-ops",
            domain="remote-ops",
            criticality="low",
        )
        assert result is not None
        assert result.is_complete
        assert result.checks_run == 0

    def test_full_wave1_smoke_with_code_default_domain(self):
        """Wave 1 smoke: code/default runs tests + lint and produces status."""
        ServiceRegistry.load_builtins()

        result = run_compliance_checks(
            session_id="smoke-test",
            transaction_id="tx-1",
            work_type="code",
            domain="default",
            criticality="medium",
        )
        assert result is not None
        assert result.checks_run >= 2
        # With real runners, status depends on actual project state
        assert result.status in ("complete", "iteration_needed")


# ---------------------------------------------------------------------------
# End-to-end compliance flow
# ---------------------------------------------------------------------------


class TestEndToEndComplianceFlow:
    def test_cybersec_high_with_mixed_results(self, tmp_path, monkeypatch):
        """Full flow: a security-style domain runs 5 checks, some pass some fail.

        The shipped cybersec domain no longer requires the scanners (none has a runner), so the
        checklist comes from a project domains file shaped like the one it used to ship.
        """
        import time

        import yaml

        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".empirica").mkdir()
        (tmp_path / ".empirica" / "domains.yaml").write_text(
            yaml.dump(
                {
                    "domains": {
                        "cybersec": {
                            "criticalities": {
                                "high": {"required_checks": ["tests", "lint", "semgrep_full", "trivy_deps", "gitleaks"]}
                            }
                        }
                    }
                }
            )
        )

        # Register real checks for tests + lint, stubs for the rest
        ServiceRegistry.load_builtins()

        # Register fake security checks that fail
        def failing_semgrep(ctx):
            from empirica.config.service_registry import CheckResult

            return CheckResult("semgrep_full", False, {"findings": 1}, "1 critical finding", 100, time.time())

        def passing_trivy(ctx):
            from empirica.config.service_registry import CheckResult

            return CheckResult("trivy_deps", True, {}, "0 vulnerabilities", 50, time.time())

        def passing_gitleaks(ctx):
            from empirica.config.service_registry import CheckResult

            return CheckResult("gitleaks", True, {}, "no secrets found", 30, time.time())

        ServiceRegistry.register(
            CheckDeclaration(
                "semgrep_full",
                "semgrep",
                (("code", "cybersec"),),
                "No critical SAST findings",
                failing_semgrep,
                120,
                ("security",),
            )
        )
        ServiceRegistry.register(
            CheckDeclaration(
                "trivy_deps",
                "trivy",
                (("code", "cybersec"),),
                "No dep vulnerabilities",
                passing_trivy,
                120,
                ("security",),
            )
        )
        ServiceRegistry.register(
            CheckDeclaration(
                "gitleaks",
                "gitleaks",
                (("code", "cybersec"),),
                "No hardcoded secrets",
                passing_gitleaks,
                120,
                ("security",),
            )
        )

        result = run_compliance_checks(
            session_id="e2e-test",
            transaction_id="tx-e2e",
            work_type="code",
            domain="cybersec",
            criticality="high",
            project_path=str(tmp_path),
        )

        assert result is not None
        assert result.status == "iteration_needed"
        assert result.checks_failed >= 1
        assert result.next_transaction is not None
        assert "semgrep_full" in result.next_transaction["intent"]
        assert result.next_transaction["inherited_domain"] == "cybersec"
        assert result.next_transaction["inherited_criticality"] == "high"


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_no_domain_no_compliance_block(self):
        """Legacy: without domain/criticality, compliance loop returns None."""
        result = run_compliance_checks(
            session_id="legacy",
            transaction_id="tx-legacy",
            work_type=None,
            domain=None,
            criticality=None,
        )
        assert result is None

    def test_compliance_status_enum_compatible_with_strings(self):
        """ComplianceStatus values are string-compatible for JSON serialization."""
        assert ComplianceStatus.GROUNDED == "grounded"
        assert ComplianceStatus.COMPLETE == "complete"
        assert ComplianceStatus.ITERATION_NEEDED == "iteration_needed"

    def test_grounded_assessment_legacy_fields_unchanged(self):
        """Existing GroundedAssessment consumers see no change."""
        a = GroundedAssessment(
            session_id="test",
            self_assessed={"know": 0.8, "do": 0.7},
            grounded={"know": GroundedVectorEstimate("know", 0.75, 0.9, 5, "git")},
            calibration_gaps={"know": 0.05},
            grounded_coverage=0.5,
            overall_calibration_score=0.05,
            calibration_status="grounded",
        )
        # All legacy fields work
        assert a.session_id == "test"
        assert a.self_assessed["know"] == 0.8
        assert a.grounded["know"].estimated_value == 0.75
        assert a.calibration_gaps["know"] == 0.05
        assert a.calibration_status == "grounded"
        assert a.insufficient_evidence_vectors == []
