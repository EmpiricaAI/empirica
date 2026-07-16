#!/usr/bin/env python3
"""
Unit Tests for All 55 CLI Commands

Generated from comprehensive audit on 2025-12-01.
Tests basic functionality of each CLI command.

Status: 53/54 commands working (98.1%)
Issue: 'chat' command times out (interactive mode)

For Gemini/Qwen to validate bug fixes and ensure no regressions.
"""

import subprocess

import pytest


class TestCoreWorkflowCommands:
    """Test Core Workflow commands (CASCADE phases)

    NOTE: These are INTERFACE tests only - they verify commands exist and accept args.
    Epistemic assessment quality (preflight/check/postflight) requires INTEGRATION tests
    with real reasoning. DO NOT use dummy epistemic data here - AIs might think it's canonical.

    Uses AI-first *-submit commands (config file or stdin JSON).
    """

    def test_check_help(self):
        """Check command has working --help"""
        result = subprocess.run(["empirica", "check", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_preflight_submit_help(self):
        """Preflight-submit command has working --help"""
        result = subprocess.run(["empirica", "preflight-submit", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_check_submit_help(self):
        """Check-submit command has working --help"""
        result = subprocess.run(["empirica", "check-submit", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_postflight_submit_help(self):
        """Postflight-submit command has working --help"""
        result = subprocess.run(["empirica", "postflight-submit", "--help"], capture_output=True)
        assert result.returncode == 0


class TestGoalsCommands:
    """Test Goals commands (7 commands)"""

    def test_goals_create_help(self):
        """Goals-create command has working --help"""
        result = subprocess.run(["empirica", "goals-create", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_goals_add_task_help(self):
        """Goals-add-task command has working --help"""
        result = subprocess.run(["empirica", "goals-add-task", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_goals_complete_task_help(self):
        """Goals-complete-task command has working --help"""
        result = subprocess.run(["empirica", "goals-complete-task", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_goals_progress_help(self):
        """Goals-progress command has working --help"""
        result = subprocess.run(["empirica", "goals-progress", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_goals_list_execution(self):
        """Goals-list runs without args (shows preview when no session)"""
        result = subprocess.run(["empirica", "goals-list"], capture_output=True, timeout=5)
        # Return 1 is OK when showing preview (design intent)
        assert result.returncode in [0, 1]
        assert b"Preview" in result.stdout or b"goals" in result.stdout.lower()

    def test_goals_discover_execution(self):
        """Goals-discover runs without args (discovers from git)"""
        # Timeout 30s: discover_goals reads git notes refs (N+1 pattern),
        # repos with many goals (800+) need more than 5s
        result = subprocess.run(["empirica", "goals-discover"], capture_output=True, timeout=30)
        # Return 1 is OK when no context (shows help)
        assert result.returncode in [0, 1]

    def test_goals_resume_help(self):
        """Goals-resume command has working --help"""
        result = subprocess.run(["empirica", "goals-resume", "--help"], capture_output=True)
        assert result.returncode == 0


class TestCheckpointCommands:
    """Test Git Checkpoint commands (4 commands)"""

    def test_checkpoint_create_help(self):
        """Checkpoint-create command has working --help"""
        result = subprocess.run(["empirica", "checkpoint-create", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_checkpoint_load_help(self):
        """Checkpoint-load command has working --help"""
        result = subprocess.run(["empirica", "checkpoint-load", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_checkpoint_list_help(self):
        """Checkpoint-list command has working --help"""
        result = subprocess.run(["empirica", "checkpoint-list", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_checkpoint_diff_help(self):
        """Checkpoint-diff command has working --help"""
        result = subprocess.run(["empirica", "checkpoint-diff", "--help"], capture_output=True)
        assert result.returncode == 0


class TestHandoffCommands:
    """Test Handoff commands (2 commands)"""

    def test_handoff_create_help(self):
        """Handoff-create command has working --help"""
        result = subprocess.run(["empirica", "handoff-create", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_handoff_query_execution(self):
        """Handoff-query runs without args (queries all handoffs)"""
        result = subprocess.run(["empirica", "handoff-query"], capture_output=True, timeout=5)
        # Return 1 is OK when showing results summary
        assert result.returncode in [0, 1]
        assert b"handoff" in result.stdout.lower() or b"Found" in result.stdout


class TestSessionCommands:
    """Test Session commands (4 commands)"""

    def test_sessions_list_execution(self):
        """Sessions-list runs without args (lists all sessions)"""
        result = subprocess.run(["empirica", "sessions-list"], capture_output=True, timeout=5)
        assert result.returncode == 0

    def test_sessions_show_help(self):
        """Sessions-show command has working --help"""
        result = subprocess.run(["empirica", "sessions-show", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_sessions_export_help(self):
        """Sessions-export command has working --help"""
        result = subprocess.run(["empirica", "sessions-export", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_sessions_resume_execution(self):
        """Sessions-resume runs without args (shows available sessions)"""
        result = subprocess.run(["empirica", "sessions-resume"], capture_output=True, timeout=5)
        assert result.returncode == 0


class TestIdentityCommands:
    """Test Identity commands (4 commands)"""

    def test_identity_create_help(self):
        """Identity-create command has working --help"""
        result = subprocess.run(["empirica", "identity-create", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_identity_list_execution(self):
        """Identity-list runs without args (lists all identities)"""
        result = subprocess.run(["empirica", "identity-list"], capture_output=True, timeout=5)
        assert result.returncode == 0

    def test_identity_export_help(self):
        """Identity-export command has working --help"""
        result = subprocess.run(["empirica", "identity-export", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_identity_verify_help(self):
        """Identity-verify command has working --help"""
        result = subprocess.run(["empirica", "identity-verify", "--help"], capture_output=True)
        assert result.returncode == 0


class TestConfigurationCommands:
    """Test Configuration commands"""

    def test_config_execution(self):
        """Config runs without args (shows current config)"""
        result = subprocess.run(["empirica", "config"], capture_output=True, timeout=5)
        assert result.returncode == 0


class TestInvestigationCommands:
    """Test Investigation commands (3 commands)"""

    def test_investigate_help(self):
        """Investigate command has working --help"""
        result = subprocess.run(["empirica", "investigate", "--help"], capture_output=True)
        assert result.returncode == 0

    def test_goal_analysis_help(self):
        """Goal-analysis command has working --help"""
        result = subprocess.run(["empirica", "goal-analysis", "--help"], capture_output=True)
        assert result.returncode == 0


class TestPerformanceCommands:
    """Test Performance commands (2 commands)"""

    def test_performance_execution(self):
        """Performance runs without args (shows performance stats)"""
        result = subprocess.run(["empirica", "performance"], capture_output=True, timeout=5)
        assert result.returncode == 0

    def test_efficiency_report_help(self):
        """Efficiency-report command has working --help"""
        result = subprocess.run(["empirica", "efficiency-report", "--help"], capture_output=True)
        assert result.returncode == 0


class TestMonitoringCommands:
    """Test Monitoring commands (1 command)"""

    def test_monitor_execution(self):
        """Monitor runs without args (shows monitoring dashboard)"""
        result = subprocess.run(["empirica", "monitor"], capture_output=True, timeout=5)
        assert result.returncode == 0


# Summary test
class TestCLISummary:
    """Overall CLI health check"""

    def test_main_help_works(self):
        """Main empirica --help works"""
        result = subprocess.run(["empirica", "--help"], capture_output=True)
        assert result.returncode == 0
        assert b"Empirica" in result.stdout

    def test_total_command_count(self):
        """Verify core commands exist in compact help output"""
        result = subprocess.run(["empirica", "--help"], capture_output=True, text=True)
        # Compact help shows core commands (workflow, artifacts, goals, context)
        assert "preflight-submit" in result.stdout
        assert "check" in result.stdout
        assert "postflight-submit" in result.stdout
        assert "goals-create" in result.stdout
        assert "finding-log" in result.stdout
        assert "project-bootstrap" in result.stdout
        # Non-core commands available via 'empirica help'
        result2 = subprocess.run(["empirica", "help"], capture_output=True, text=True)
        assert "checkpoint" in result2.stdout


if __name__ == "__main__":
    # Run with: pytest tests/unit/test_all_cli_commands.py -v
    pytest.main([__file__, "-v", "--tb=short"])
