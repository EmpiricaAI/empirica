"""
Tests for B2 Compliance Loop (SPEC 1 Wave 3).

Verifies: checklist resolution, check execution, status determination,
iteration advisory, empty checklist pass-through.
"""

from __future__ import annotations

import pytest

from empirica.config.service_registry import (
    CheckDeclaration,
    CheckResult,
    ServiceRegistry,
)
from empirica.core.post_test.compliance_loop import (
    ComplianceResult,
    run_compliance_checks,
)


@pytest.fixture(autouse=True)
def clean_registry():
    ServiceRegistry._registered.clear()
    ServiceRegistry.clear_cache()
    yield
    ServiceRegistry._registered.clear()
    ServiceRegistry.clear_cache()


def _make_passing_runner(check_id: str = "test_check"):
    def runner(ctx):
        import time

        return CheckResult(check_id, True, {}, "passed", 1, time.time())

    return runner


def _make_failing_runner(check_id: str = "test_check"):
    def runner(ctx):
        import time

        return CheckResult(check_id, False, {"error": "failed"}, "failed", 1, time.time())

    return runner


class TestComplianceResult:
    def test_complete_status(self):
        r = ComplianceResult(
            status="complete",
            domain="default",
            criticality="medium",
            checks_run=2,
            checks_passed=2,
            checks_failed=0,
            check_results=[],
        )
        assert r.is_complete

    def test_iteration_needed(self):
        r = ComplianceResult(
            status="iteration_needed",
            domain="cybersec",
            criticality="high",
            checks_run=3,
            checks_passed=2,
            checks_failed=1,
            check_results=[],
            next_transaction={"intent": "fix X"},
        )
        assert not r.is_complete
        assert r.next_transaction is not None

    def test_to_dict(self):
        r = ComplianceResult(
            status="complete",
            domain="default",
            criticality="low",
            checks_run=1,
            checks_passed=1,
            checks_failed=0,
            check_results=[{"check_id": "tests", "passed": True}],
        )
        d = r.to_dict()
        assert d["status"] == "complete"
        assert d["checks_run"] == 1
        assert "next_transaction" not in d  # not present when None


class TestRunComplianceChecks:
    def test_no_domain_returns_none(self):
        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type=None,
            domain=None,
            criticality=None,
        )
        assert result is None

    def test_remote_ops_returns_complete(self):
        """remote-ops has empty checklist — always complete."""
        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type="remote-ops",
            domain="remote-ops",
            criticality="low",
        )
        assert result is not None
        assert result.is_complete
        assert result.checks_run == 0

    def test_all_checks_pass_returns_complete(self):
        """When all required checks pass, status is complete."""
        # default/low requires only tests
        ServiceRegistry.register(
            CheckDeclaration(
                check_id="tests",
                tool="pytest",
                applies_to=(("code", "*"),),
                criterion_description="tests pass",
                runner=_make_passing_runner("tests"),
            )
        )

        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type="code",
            domain="default",
            criticality="low",
            execution_tier="goal_completion",
        )
        assert result is not None
        assert result.is_complete
        assert result.checks_passed == 1
        assert result.checks_failed == 0

    def test_failed_check_returns_iteration_needed(self):
        """When a check fails, status is iteration_needed with advisory."""
        # default/low requires only tests — register it as failing
        ServiceRegistry.register(
            CheckDeclaration(
                check_id="tests",
                tool="pytest",
                applies_to=(("code", "*"),),
                criterion_description="tests pass",
                runner=_make_failing_runner("tests"),
            )
        )

        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type="code",
            domain="default",
            criticality="low",
            execution_tier="goal_completion",  # tests tier=goal_completion
        )
        assert result is not None
        assert result.status == "iteration_needed"
        assert result.checks_failed == 1
        assert result.next_transaction is not None
        assert "tests" in result.next_transaction["intent"]

    def test_max_iterations_exceeded(self):
        """After max_iterations, status changes to max_iterations_exceeded."""
        ServiceRegistry.register(
            CheckDeclaration(
                check_id="tests",
                tool="pytest",
                applies_to=(("code", "*"),),
                criterion_description="tests pass",
                runner=_make_failing_runner("tests"),
            )
        )

        # default/low has max_iterations=3, requires only tests
        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type="code",
            domain="default",
            criticality="low",
            iteration_number=3,
            execution_tier="goal_completion",
        )
        assert result is not None
        assert result.status == "max_iterations_exceeded"
        assert result.next_transaction is None

    def test_unregistered_check_treated_as_failure(self, tmp_path):
        """Checks not in ServiceRegistry are reported as failures."""
        # Use cybersec/high which requires semgrep_full, trivy_deps, gitleaks
        # — none of which are in the builtins
        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type="code",
            domain="cybersec",
            criticality="high",
        )
        assert result is not None
        assert result.checks_failed >= 1
        assert any("not registered" in r["summary"] for r in result.check_results)


class TestSkippedIsNotPassed:
    """A check that could not answer is counted as skipped, never as passed.

    The registry's checks return passed=True with details {"skipped": True}
    (tool absent) or {"error": ...} (raised, non-blocking) in 27 places. The
    loop counted every passed=True as a pass, so a POSTFLIGHT block read
    "3 run, 3 passed, 0 failed" over one pass, one skip and one error. The
    status stays complete - they do not block - but the numbers say what
    happened.
    """

    @staticmethod
    def _register(check_id, passed, details):
        import time

        def runner(ctx):
            return CheckResult(check_id, passed, details, "x", 1, time.time())

        ServiceRegistry.register(
            CheckDeclaration(
                check_id=check_id,
                tool="t",
                applies_to=(("code", "*"),),
                criterion_description="d",
                runner=runner,
            )
        )

    def test_skipped_and_errored_checks_are_not_passes(self, clean_registry):
        self._register("tests", True, {"skipped": True})
        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type="code",
            domain="default",
            criticality="low",
            execution_tier="goal_completion",
        )
        assert result is not None
        assert result.is_complete  # non-blocking, as before
        assert result.checks_run == 1
        assert result.checks_passed == 0
        assert result.checks_skipped == 1
        assert result.checks_failed == 0
        assert result.check_results[0]["skipped"] is True
        assert result.to_dict()["checks_skipped"] == 1

    def test_an_errored_non_blocking_check_is_skipped_not_passed(self, clean_registry):
        self._register("tests", True, {"error": "pytest exploded"})
        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type="code",
            domain="default",
            criticality="low",
            execution_tier="goal_completion",
        )
        assert result is not None
        assert result.checks_passed == 0 and result.checks_skipped == 1 and result.checks_failed == 0

    def test_a_real_pass_is_still_a_pass(self, clean_registry):
        self._register("tests", True, {"tests_run": 12})
        result = run_compliance_checks(
            session_id="s1",
            transaction_id="t1",
            work_type="code",
            domain="default",
            criticality="low",
            execution_tier="goal_completion",
        )
        assert result is not None
        assert result.checks_passed == 1 and result.checks_skipped == 0
        assert "skipped" not in result.check_results[0]
