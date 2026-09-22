"""
Test AI Agent Workflow - AI-First Interface

Tests the complete AI agent workflow using config files.
Demonstrates proper usage patterns for AI agents.

DB Isolation: All subprocess calls use EMPIRICA_SESSION_DB pointing to a temp
database. This prevents test sessions/goals from polluting the live database.
See docs/architecture/instance_isolation/KNOWN_ISSUES.md #11.17.
"""

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

# Marked as integration: requires empirica CLI on PATH, git-initialized
# CWD, and/or a populated sessions.db. Excluded from default CI run
# (pytest -m "not integration"). Run explicitly via:
#   pytest -m integration tests/...
pytestmark = pytest.mark.integration


@pytest.fixture(scope="class")
def isolated_env():
    """Create an isolated environment for subprocess CLI calls.

    Sets EMPIRICA_SESSION_DB to a temp database (priority 0 in path_resolver).
    This prevents test sessions/goals from polluting the live database.
    TMUX_PANE is preserved so project path resolution still works.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_dir = Path(tmpdir) / "sessions"
        db_dir.mkdir()
        db_path = db_dir / "sessions.db"

        # Initialize the database schema
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase(db_path=str(db_path))
        db.close()

        # Inherit parent env, override only the DB path
        env = dict(os.environ)
        env["EMPIRICA_SESSION_DB"] = str(db_path)
        # The suite runs under a throwaway HOME with no project registry, so the
        # CLI must take the checkout it runs in as the project. Before this, the
        # tests resolved the project from the developer's real registry.
        env["EMPIRICA_CWD_RELIABLE"] = "true"

        yield env


class TestAIAgentWorkflow:
    """Test complete AI agent workflow with config files."""

    def _run_empirica(self, args, env, input_data=None, check=True):
        """Run an empirica CLI command in the isolated environment."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(input_data, f)
            config_path = f.name

        try:
            result = subprocess.run(
                ["empirica"] + args + [config_path], capture_output=True, text=True, check=check, env=env
            )
            return result
        finally:
            os.unlink(config_path)

    def test_complete_cascade_workflow(self, isolated_env):
        """Test full CASCADE workflow: session → preflight → check → postflight"""

        # 1. CREATE SESSION
        result = self._run_empirica(["session-create"], isolated_env, {"ai_id": "test-ai-agent", "bootstrap_level": 1})
        session_response = json.loads(result.stdout)
        assert session_response["ok"] is True
        assert "session_id" in session_response
        session_id = session_response["session_id"]
        assert len(session_id) == 36
        assert session_id.count("-") == 4

        # 2. PREFLIGHT ASSESSMENT
        result = self._run_empirica(
            ["preflight-submit"],
            isolated_env,
            {
                "session_id": session_id,
                "vectors": {
                    "engagement": 0.85,
                    "know": 0.60,
                    "do": 0.85,
                    "context": 0.50,
                    "clarity": 0.80,
                    "coherence": 0.85,
                    "signal": 0.75,
                    "density": 0.65,
                    "state": 0.40,
                    "change": 0.85,
                    "completion": 0.70,
                    "impact": 0.90,
                    "uncertainty": 0.70,
                },
                "reasoning": "Baseline epistemic assessment for test",
            },
        )
        preflight_response = json.loads(result.stdout)
        assert preflight_response["ok"] is True
        assert "session_id" in preflight_response
        assert "transaction_id" in preflight_response

        # 3. CHECK DECISION GATE
        result = self._run_empirica(
            ["check"],
            isolated_env,
            {
                "session_id": session_id,
                "confidence": 0.78,
                "findings": [
                    "Test finding 1: AI-first interface works correctly",
                    "Test finding 2: JSON I/O validated",
                ],
                "unknowns": ["Test unknown: Schema validation coverage"],
                "cycle": 1,
            },
        )
        check_response = json.loads(result.stdout)
        assert check_response["ok"] is True
        assert "decision" in check_response
        assert check_response["decision"] in ["proceed", "investigate"]
        assert check_response["confidence"] == 0.78

        # 4. POSTFLIGHT ASSESSMENT
        result = self._run_empirica(
            ["postflight-submit"],
            isolated_env,
            {
                "session_id": session_id,
                "vectors": {
                    "engagement": 0.85,
                    "know": 0.85,
                    "do": 0.90,
                    "context": 0.75,
                    "clarity": 0.90,
                    "coherence": 0.90,
                    "signal": 0.85,
                    "density": 0.55,
                    "state": 0.80,
                    "change": 0.90,
                    "completion": 0.90,
                    "impact": 0.95,
                    "uncertainty": 0.30,
                },
                "reasoning": "Completed test workflow. KNOW +0.25, STATE +0.40, UNCERTAINTY -0.40",
            },
        )
        postflight_response = json.loads(result.stdout)
        assert postflight_response["ok"] is True
        assert "deltas" in postflight_response
        assert "calibration" in postflight_response

    def test_goal_creation_with_config(self, isolated_env):
        """Test goal creation using config file."""

        # Create session first
        result = self._run_empirica(["session-create"], isolated_env, {"ai_id": "test-goal-agent"})
        session_id = json.loads(result.stdout)["session_id"]

        # Create goal with unique objective
        unique_id = str(uuid.uuid4())[:8]
        result = self._run_empirica(
            ["goals-create", "--force"],
            isolated_env,
            {
                "session_id": session_id,
                "objective": f"Test AI-first goal creation [{unique_id}]",
                "scope": {"breadth": 0.3, "duration": 0.2, "coordination": 0.1},
                "success_criteria": ["Config file interface works", "JSON output validated", "No shell quoting issues"],
            },
        )
        goal_response = json.loads(result.stdout)
        assert goal_response["ok"] is True
        assert "goal_id" in goal_response
        assert goal_response["objective"].startswith("Test AI-first goal creation")

    def test_config_validation_error(self, isolated_env):
        """Test that invalid config produces helpful error message."""

        result = self._run_empirica(
            ["preflight-submit"],
            isolated_env,
            {"vectors": {"engagement": 0.85}},  # Missing required vectors
            check=False,
        )
        assert result.returncode != 0
        response = json.loads(result.stdout)
        assert response["ok"] is False
        assert "error" in response


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
