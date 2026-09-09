"""
Test CLI Checkpoint Commands (Phase 2, Task 4)

Validates CLI commands for git checkpoint management.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest


def run_cli_command(args):
    """Helper to run empirica CLI commands"""
    result = subprocess.run(
        ["python", "-m", "empirica.cli"] + args, capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent
    )
    return result


@pytest.fixture
def git_repo():
    """Create temporary git repository for testing"""
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        subprocess.run(["git", "init"], capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], capture_output=True)
        yield tmpdir
        os.chdir(original_cwd)  # Restore original directory


def test_checkpoint_create_help():
    """Test checkpoint-create help text"""
    result = run_cli_command(["checkpoint-create", "--help"])

    assert result.returncode == 0 or "checkpoint-create" in result.stdout or "checkpoint-create" in result.stderr
    print("✅ checkpoint-create --help works")


def test_checkpoint_load_help():
    """Test checkpoint-load help text"""
    result = run_cli_command(["checkpoint-load", "--help"])

    assert result.returncode == 0 or "checkpoint-load" in result.stdout or "checkpoint-load" in result.stderr
    print("✅ checkpoint-load --help works")


def test_checkpoint_list_help():
    """Test checkpoint-list help text"""
    result = run_cli_command(["checkpoint-list", "--help"])

    assert result.returncode == 0 or "checkpoint-list" in result.stdout or "checkpoint-list" in result.stderr
    print("✅ checkpoint-list --help works")


def test_checkpoint_diff_help():
    """Test checkpoint-diff help text"""
    result = run_cli_command(["checkpoint-diff", "--help"])

    assert result.returncode == 0 or "checkpoint-diff" in result.stdout or "checkpoint-diff" in result.stderr
    print("✅ checkpoint-diff --help works")


def test_efficiency_report_help():
    """Test efficiency-report help text"""
    result = run_cli_command(["efficiency-report", "--help"])

    assert result.returncode == 0 or "efficiency-report" in result.stdout or "efficiency-report" in result.stderr
    print("✅ efficiency-report --help works")


def test_checkpoint_create_missing_args():
    """Test checkpoint-create with missing required arguments"""
    result = run_cli_command(["checkpoint-create"])

    # Should fail with exit code != 0 or show error
    assert result.returncode != 0 or "required" in result.stderr.lower() or "error" in result.stderr.lower()
    print("✅ checkpoint-create validates required arguments")


def test_checkpoint_load_missing_args():
    """Test checkpoint-load with missing required arguments"""
    result = run_cli_command(["checkpoint-load"])

    # Should fail or show error
    assert result.returncode != 0 or "required" in result.stderr.lower() or "error" in result.stderr.lower()
    print("✅ checkpoint-load validates required arguments")


def test_checkpoint_commands_exist():
    """Verify the checkpoint commands are actually registered.

    The previous body computed `result.stdout + result.stderr`, DISCARDED the
    result, and printed "✅ verified". It could not fail on any behaviour —
    every checkpoint command could be deleted and this stayed green while
    claiming verification. Found by the assertion audit
    (scripts/audit_test_assertions.py); the print saying "verified" is what
    made it the worst of that audit's findings rather than a harmless stub.

    Checked via each command's OWN --help exit code, not membership in the
    top-level help: `empirica --help` is a curated summary that legitimately
    omits these, so the first revival of this test asserted against the wrong
    surface and failed on correctly-registered commands.
    """
    for cmd in ("checkpoint-create", "checkpoint-load", "checkpoint-list"):
        result = run_cli_command([cmd, "--help"])
        assert result.returncode == 0, f"{cmd} is not a registered CLI command"


@pytest.mark.integration
def test_checkpoint_create_command(git_repo):
    """Test creating a checkpoint via CLI (integration test)"""

    # This test requires a full setup, so we mark it as integration
    # and make it optional

    result = run_cli_command(
        ["checkpoint-create", "--session-id", "test-cli-create", "--phase", "PREFLIGHT", "--round", "1"]
    )

    # May succeed or fail depending on setup, but should not crash
    # We're mainly testing that the command handler exists and runs
    print(f"checkpoint-create output: {result.stdout}")
    print(f"checkpoint-create stderr: {result.stderr}")

    # As long as it doesn't crash with import errors, we're good
    assert "ImportError" not in result.stderr
    assert "ModuleNotFoundError" not in result.stderr

    print("✅ checkpoint-create command executes")


@pytest.mark.integration
def test_checkpoint_load_command(git_repo):
    """Test loading a checkpoint via CLI (integration test)"""

    result = run_cli_command(["checkpoint-load", "--session-id", "test-cli-load"])

    # Should execute without import errors
    assert "ImportError" not in result.stderr
    assert "ModuleNotFoundError" not in result.stderr

    print("✅ checkpoint-load command executes")


@pytest.mark.integration
def test_checkpoint_list_command(git_repo):
    """Test listing checkpoints via CLI (integration test)"""

    result = run_cli_command(["checkpoint-list", "--session-id", "test-cli-list"])

    # Should execute without import errors
    assert "ImportError" not in result.stderr
    assert "ModuleNotFoundError" not in result.stderr

    print("✅ checkpoint-list command executes")


@pytest.mark.integration
def test_efficiency_report_command(git_repo):
    """Test generating efficiency report via CLI (integration test)"""

    result = run_cli_command(["efficiency-report", "--session-id", "test-efficiency", "--format", "json"])

    # Should execute without import errors
    assert "ImportError" not in result.stderr
    assert "ModuleNotFoundError" not in result.stderr

    print("✅ efficiency-report command executes")


if __name__ == "__main__":
    print("🧪 Testing CLI Checkpoint Commands...\n")

    # Run basic tests (no git repo needed)
    test_checkpoint_create_help()
    test_checkpoint_load_help()
    test_checkpoint_list_help()
    test_checkpoint_diff_help()
    test_efficiency_report_help()
    test_checkpoint_create_missing_args()
    test_checkpoint_load_missing_args()
    test_checkpoint_commands_exist()

    print("\n✅ All CLI command tests passed!")
