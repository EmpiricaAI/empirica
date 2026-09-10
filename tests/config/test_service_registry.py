"""
Tests for A2 Service Registry (SPEC 1 Part 2).

Verifies: registration, resolution, idempotency, timeout handling,
exception capture, built-in check declarations, run() dispatch.
"""

from __future__ import annotations

import time

import pytest

from empirica.config.service_registry import (
    CheckDeclaration,
    CheckResult,
    RegistrationConflict,
    ServiceRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset the registry and cache between tests."""
    ServiceRegistry._registered.clear()
    ServiceRegistry.clear_cache()
    yield
    ServiceRegistry._registered.clear()
    ServiceRegistry.clear_cache()


def _make_result(check_id: str = "test_check", passed: bool = True) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        passed=passed,
        details={},
        summary="ok",
        duration_ms=10,
        ran_at=time.time(),
    )


def _make_declaration(
    check_id: str = "test_check",
    tool: str = "test_tool",
    applies_to: tuple[tuple[str, str], ...] = (("*", "*"),),
    runner=None,
    timeout_seconds: int = 120,
) -> CheckDeclaration:
    if runner is None:
        # Named distinctly from the parameter: `def runner(...)` under
        # `if runner is None` redeclares the parameter, which type checkers
        # rightly flag — the def rebinds the name whether or not the branch
        # runs is knowable statically.
        def _default_runner(ctx):
            return _make_result(check_id)

        runner = _default_runner

    return CheckDeclaration(
        check_id=check_id,
        tool=tool,
        applies_to=applies_to,
        criterion_description=f"Test check: {check_id}",
        runner=runner,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# CheckResult tests
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_basic_fields(self):
        r = _make_result("lint", passed=True)
        assert r.check_id == "lint"
        assert r.passed is True
        assert r.predicted_pass is None

    def test_with_prediction(self):
        r = CheckResult(
            check_id="tests",
            passed=False,
            details={"failed": 2},
            summary="2 tests failed",
            duration_ms=1500,
            ran_at=time.time(),
            predicted_pass=0.9,
            predicted_at=time.time() - 60,
        )
        assert r.predicted_pass == 0.9
        assert not r.passed


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_and_get(self):
        decl = _make_declaration("lint")
        ServiceRegistry.register(decl)
        assert ServiceRegistry.get("lint") is decl

    def test_idempotent_same_declaration(self):
        decl = _make_declaration("lint")
        ServiceRegistry.register(decl)
        ServiceRegistry.register(decl)  # no-op
        assert ServiceRegistry.get("lint") is decl

    def test_conflict_on_different_declaration(self):
        ServiceRegistry.register(_make_declaration("lint", tool="ruff"))
        with pytest.raises(RegistrationConflict):
            ServiceRegistry.register(_make_declaration("lint", tool="flake8"))

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError):
            ServiceRegistry.get("nonexistent")

    def test_list_all(self):
        ServiceRegistry.register(_make_declaration("a"))
        ServiceRegistry.register(_make_declaration("b"))
        ServiceRegistry.register(_make_declaration("c"))
        all_ids = ServiceRegistry.list_all()
        assert sorted(all_ids) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Resolution tests
# ---------------------------------------------------------------------------


class TestResolution:
    def test_wildcard_matches_everything(self):
        ServiceRegistry.register(_make_declaration("universal", applies_to=(("*", "*"),)))
        result = ServiceRegistry.resolve_for("code", "cybersec")
        assert len(result) == 1
        assert result[0].check_id == "universal"

    def test_specific_match(self):
        ServiceRegistry.register(_make_declaration("sec_check", applies_to=(("code", "cybersec"),)))
        # Matches
        assert len(ServiceRegistry.resolve_for("code", "cybersec")) == 1
        # Doesn't match different work_type
        assert len(ServiceRegistry.resolve_for("docs", "cybersec")) == 0
        # Doesn't match different domain
        assert len(ServiceRegistry.resolve_for("code", "payments")) == 0

    def test_wildcard_work_type(self):
        ServiceRegistry.register(_make_declaration("any_code", applies_to=(("*", "cybersec"),)))
        assert len(ServiceRegistry.resolve_for("code", "cybersec")) == 1
        assert len(ServiceRegistry.resolve_for("infra", "cybersec")) == 1
        assert len(ServiceRegistry.resolve_for("code", "default")) == 0

    def test_wildcard_domain(self):
        ServiceRegistry.register(_make_declaration("all_domains", applies_to=(("code", "*"),)))
        assert len(ServiceRegistry.resolve_for("code", "cybersec")) == 1
        assert len(ServiceRegistry.resolve_for("code", "default")) == 1
        assert len(ServiceRegistry.resolve_for("docs", "default")) == 0

    def test_ordered_by_check_id(self):
        ServiceRegistry.register(_make_declaration("z_check", applies_to=(("*", "*"),)))
        ServiceRegistry.register(_make_declaration("a_check", applies_to=(("*", "*"),)))
        ServiceRegistry.register(_make_declaration("m_check", applies_to=(("*", "*"),)))
        result = ServiceRegistry.resolve_for("code", "default")
        assert [d.check_id for d in result] == ["a_check", "m_check", "z_check"]

    def test_multiple_applies_to(self):
        ServiceRegistry.register(_make_declaration("multi", applies_to=(("code", "cybersec"), ("infra", "cybersec"))))
        assert len(ServiceRegistry.resolve_for("code", "cybersec")) == 1
        assert len(ServiceRegistry.resolve_for("infra", "cybersec")) == 1
        assert len(ServiceRegistry.resolve_for("docs", "cybersec")) == 0


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------


class TestRunner:
    def test_run_returns_result(self):
        ServiceRegistry.register(_make_declaration("lint"))
        result = ServiceRegistry.run("lint", {})
        assert isinstance(result, CheckResult)
        assert result.passed is True

    def test_run_captures_exception_as_failure(self):
        def failing_runner(ctx):
            raise RuntimeError("tool crashed")

        ServiceRegistry.register(_make_declaration("crasher", runner=failing_runner))
        result = ServiceRegistry.run("crasher", {})
        assert result.passed is False
        assert "RuntimeError" in result.summary

    def test_run_unknown_check_raises(self):
        with pytest.raises(KeyError):
            ServiceRegistry.run("nonexistent", {})

    def test_run_measures_duration(self):
        def slow_runner(ctx):
            time.sleep(0.05)
            return _make_result("slow")

        ServiceRegistry.register(_make_declaration("slow", runner=slow_runner))
        result = ServiceRegistry.run("slow", {})
        assert result.duration_ms >= 40  # at least 40ms

    def test_run_passes_context(self):
        received = {}

        def ctx_runner(ctx):
            received.update(ctx)
            return _make_result("ctx_check")

        ServiceRegistry.register(_make_declaration("ctx_check", runner=ctx_runner))
        ServiceRegistry.run("ctx_check", {"project_path": "/tmp/test", "work_type": "code"})
        assert received["project_path"] == "/tmp/test"

    def test_run_with_prediction(self):
        ServiceRegistry.register(_make_declaration("predicted"))
        result = ServiceRegistry.run(
            "predicted",
            {},
            predicted_pass=0.8,
        )
        assert result.predicted_pass == 0.8
        assert result.predicted_at is not None


# ---------------------------------------------------------------------------
# Built-in checks
# ---------------------------------------------------------------------------


class TestBuiltinChecks:
    def test_load_builtins(self):
        """Built-in checks register when load_builtins() is called."""
        ServiceRegistry.load_builtins()
        all_ids = ServiceRegistry.list_all()
        assert "tests" in all_ids
        assert "lint" in all_ids
        assert len(all_ids) >= 2

    def test_builtin_tests_applies_to_code(self):
        ServiceRegistry.load_builtins()
        ServiceRegistry.get("tests")
        resolved = ServiceRegistry.resolve_for("code", "default")
        assert any(d.check_id == "tests" for d in resolved)

    def test_builtin_lint_applies_to_code(self):
        ServiceRegistry.load_builtins()
        resolved = ServiceRegistry.resolve_for("code", "default")
        assert any(d.check_id == "lint" for d in resolved)

    def test_builtins_not_for_remote_ops(self):
        """remote-ops should not have tests/lint checks."""
        ServiceRegistry.load_builtins()
        resolved = ServiceRegistry.resolve_for("remote-ops", "remote-ops")
        assert len(resolved) == 0
