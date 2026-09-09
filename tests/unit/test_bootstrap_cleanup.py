#!/usr/bin/env python3
"""
Unit Tests for Bootstrap Cleanup

Tests to verify bootstrap works correctly after removing dead components.
Run BEFORE and AFTER cleanup to ensure no regressions.

Created: 2025-12-01
For: Qwen to validate bootstrap cleanup
"""

import subprocess

import pytest

# Marked as integration: requires empirica CLI on PATH, git-initialized
# CWD, and/or a populated sessions.db. Excluded from default CI run
# (pytest -m "not integration"). Run explicitly via:
#   pytest -m integration tests/...
pytestmark = pytest.mark.integration


class TestBootstrapComponents:
    """Test that bootstrap loads correctly after cleanup"""

    def test_bootstrap_command_works(self):
        """Bootstrap command executes without errors"""
        result = subprocess.run(
            ["empirica", "project-bootstrap", "--output", "json"], capture_output=True, text=True, timeout=10
        )

        # Should succeed
        assert result.returncode == 0, f"Bootstrap failed: {result.stderr}"

        # Should return valid JSON with "ok": true
        import json

        try:
            output = json.loads(result.stdout)
            assert output.get("ok"), "Bootstrap should return ok=true"
        except json.JSONDecodeError:
            pytest.fail(f"Bootstrap did not return valid JSON: {result.stdout[:200]}")

    def test_bootstrap_no_import_errors(self):
        """Bootstrap runs without import errors in stderr"""
        result = subprocess.run(
            ["empirica", "project-bootstrap", "--output", "json"], capture_output=True, text=True, timeout=10
        )

        # Should not have ModuleNotFoundError or ImportError
        stderr_lower = result.stderr.lower()
        assert "modulenotfounderror" not in stderr_lower, f"Import error: {result.stderr}"
        assert "importerror" not in stderr_lower, f"Import error: {result.stderr}"

        # Bayesian deprecation warning is OK
        # But no other errors
        lines = result.stderr.split("\n")
        error_lines = [l for l in lines if "error" in l.lower() and "deprecated" not in l.lower()]
        assert len(error_lines) == 0, f"Unexpected errors: {error_lines}"

    def test_bootstrap_json_output(self):
        """Bootstrap with --output json works"""
        result = subprocess.run(
            ["empirica", "project-bootstrap", "--output", "json"], capture_output=True, text=True, timeout=10
        )

        assert result.returncode == 0

        # Should return valid JSON
        import json

        try:
            output = json.loads(result.stdout)
            assert "ok" in output, "JSON output should have 'ok' field"
            assert "breadcrumbs" in output, "JSON output should have 'breadcrumbs' field"
        except json.JSONDecodeError:
            pytest.fail(f"Bootstrap did not return valid JSON: {result.stdout[:200]}")

    def test_bootstrap_returns_breadcrumbs(self):
        """Bootstrap returns breadcrumbs data structure"""
        result = subprocess.run(
            ["empirica", "project-bootstrap", "--output", "json"], capture_output=True, text=True, timeout=10
        )

        import json

        output = json.loads(result.stdout)

        # Should have breadcrumbs structure
        assert "breadcrumbs" in output, "Should have breadcrumbs"
        breadcrumbs = output["breadcrumbs"]

        # Should have expected fields
        expected_fields = ["project", "last_activity", "findings", "unknowns", "dead_ends"]
        for field in expected_fields:
            assert field in breadcrumbs, f"Breadcrumbs should have {field}"

    def test_bootstrap_fast_execution(self):
        """Bootstrap succeeds and is not pathologically slow.

        Two corrections to what this test used to claim (goal 4d79340c):

        1. The old `elapsed < 5.0` on a cold subprocess measured MACHINE LOAD,
           not bootstrap speed — measured failing at 5.01s while two full pytest
           suites ran concurrently, and at 1.9-2.0s in isolation on the same
           box, same code. A wall-clock bound that tight on a shared box fails
           on exactly the runs where nothing is wrong.
        2. It shells out to `empirica` on PATH, so it times the INSTALLED
           binary, not this checkout — it cannot regress on tree changes at
           all, and silently benchmarks whatever release happens to be on the
           box. That stays true here (the subprocess boundary is the point of
           the test), so the timing claim is scoped to what it can honestly
           make: a sanity ceiling against hangs, not a performance gate.

        The subprocess timeout is the real guard against a hang; the generous
        assert below exists only so a wildly slow bootstrap (a new N+1 query, a
        blocking network call) still fails rather than sliding under a timeout
        that would mask it as "passed slowly".
        """
        import time

        start = time.time()
        result = subprocess.run(
            ["empirica", "project-bootstrap", "--output", "json"], capture_output=True, text=True, timeout=30
        )
        elapsed = time.time() - start

        assert result.returncode == 0, f"Bootstrap should succeed (stderr: {result.stderr[:200]})"
        assert elapsed < 20.0, (
            f"Bootstrap took {elapsed:.2f}s — an order of magnitude past normal (~2s). "
            "This bound is deliberately generous: it catches hangs and pathological slowdowns, "
            "not machine load. If it fires, something is genuinely wrong."
        )


# `TestBootstrapImports` was deleted here, deliberately and with its story told:
# it "verified" the imports of two bootstrap modules that no longer exist
# anywhere in the repo. Two of its tests referenced UNDEFINED names whose
# NameError fell into `except Exception: pass` — an assert that can never be
# reached still counts as an assert to any AST audit, so this shape is
# invisible to scripts/audit_test_assertions.py by construction. The third
# looped over the two file paths with `if not exists(): continue`, so once the
# files were deleted the loop body never ran again and the test passed
# vacuously forever — an exemption that reports clean by never checking.
# Three tests, zero possible failures, all claiming verification in their names.


class TestBootstrapFallback:
    """Test MCP server fallback behavior"""

    def test_project_bootstrap_works(self):
        """Project bootstrap command is functional"""
        result = subprocess.run(
            ["empirica", "project-bootstrap", "--output", "json"], capture_output=True, text=True, timeout=10
        )

        # Should work and return JSON
        assert result.returncode == 0

        import json

        output = json.loads(result.stdout)
        assert output.get("ok")


class TestBootstrapVerboseMode:
    """Test bootstrap verbose output"""

    def test_bootstrap_verbose_mode(self):
        """Bootstrap --verbose mode works"""
        result = subprocess.run(
            ["empirica", "project-bootstrap", "--verbose", "--output", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0

        # Verbose mode should still return valid JSON
        import json

        output = json.loads(result.stdout)
        assert "ok" in output


# Integration test
class TestBootstrapIntegration:
    """End-to-end bootstrap tests"""

    def test_bootstrap_full_workflow(self):
        """Complete bootstrap workflow"""
        # Bootstrap
        result = subprocess.run(
            ["empirica", "project-bootstrap", "--output", "json"], capture_output=True, text=True, timeout=10
        )

        assert result.returncode == 0

        import json

        output = json.loads(result.stdout)
        assert output.get("ok")

        # Should be able to run other commands after bootstrap
        result2 = subprocess.run(
            ["empirica", "sessions-list", "--output", "json"], capture_output=True, text=True, timeout=10
        )

        # Sessions-list should work after bootstrap
        assert result2.returncode == 0


if __name__ == "__main__":
    # Run with: pytest tests/unit/test_bootstrap_cleanup.py -v
    pytest.main([__file__, "-v", "--tb=short"])
