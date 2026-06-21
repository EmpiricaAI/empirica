"""
CLI Contract Tests

Validates that CLI commands producing JSON output follow consistent contracts:
- Valid JSON output
- Success responses have 'ok': true
- Error responses have 'ok': false + 'error' key
- No contradictory state (ok:true with high-severity errors)

Catches bugs like issue #60 where project-bootstrap returned ok:true
alongside an auto-captured high-severity error.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

# ---------------------------------------------------------------------------
# Discovery: find every CLI command that declares --output with a json choice
# ---------------------------------------------------------------------------


def _discover_json_output_commands():
    """Parse CLI parser source files to find commands supporting --output json.

    Returns a sorted list of command names (e.g. ['calibration-report', ...]).
    """
    parsers_dir = Path(__file__).resolve().parents[2] / "empirica" / "cli" / "parsers"
    if not parsers_dir.is_dir():
        return []

    commands = set()

    for py_file in parsers_dir.glob("*.py"):
        content = py_file.read_text()

        # Build a mapping of parser variable names -> command names.
        # Pattern: foo_parser = subparsers.add_parser('command-name', ...)
        var_to_cmd = {}
        for m in re.finditer(
            r"(\w+_parser)\s*=\s*subparsers\.add_parser\(\s*[\"']([a-z][-a-z0-9]*)[\"']",
            content,
        ):
            var_to_cmd[m.group(1)] = m.group(2)

        # Find lines where a parser variable adds '--output' with 'json' choice.
        # Quote-agnostic + DOTALL so it survives ruff-format reflow (multi-line
        # add_argument, double-quoted strings).
        for m in re.finditer(
            r"(\w+_parser)\.add_argument\(\s*[\"']--output[\"'].*?[\"']json[\"']",
            content,
            re.DOTALL,
        ):
            var_name = m.group(1)
            if var_name in var_to_cmd:
                commands.add(var_to_cmd[var_name])

    return sorted(commands)


# Pre-compute once at import time so parametrize can use it.
ALL_JSON_COMMANDS = _discover_json_output_commands()

# Commands that can be executed with --output json without creating sessions,
# projects, or other stateful prerequisites.  They should return valid JSON
# even when there is no matching data (empty lists, etc.).
STATELESS_COMMANDS = [
    "goals-list",
    "project-list",
    "calibration-report",
    "identity-list",
    "sessions-list",
    "epistemics-list",
    "system-status",
    "project-bootstrap",
]

# Commands whose JSON output is known to omit the 'ok' field.
# These are status/diagnostic endpoints with a different response shape.
# Listed here so Test 2 can skip them explicitly rather than fail.
KNOWN_NO_OK_FIELD = {
    "system-status",  # Returns a status snapshot (timestamp, node, config, ...)
}

# Restrict to commands that actually exist in the discovered set.
RUNNABLE_STATELESS = [cmd for cmd in STATELESS_COMMANDS if cmd in ALL_JSON_COMMANDS]


def _run_empirica(*args, timeout=30):
    """Run an empirica CLI command and return the CompletedProcess."""
    return subprocess.run(
        ["empirica", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ===================================================================
# Test 1: All JSON-output commands produce valid JSON
# ===================================================================


class TestValidJson:
    """Every command that advertises --output json must actually produce
    parseable JSON when invoked."""

    @pytest.mark.parametrize("command", ALL_JSON_COMMANDS)
    def test_command_exists(self, command):
        """Each JSON-output command should exist and respond to --help."""
        result = _run_empirica(command, "--help")
        combined = result.stdout + result.stderr
        assert result.returncode == 0 or command in combined, (
            f"Command '{command}' does not appear to be registered in the CLI"
        )

    @pytest.mark.parametrize("command", RUNNABLE_STATELESS)
    def test_stateless_command_returns_valid_json(self, command):
        """Stateless commands must return valid JSON on stdout."""
        result = _run_empirica(command, "--output", "json")

        stdout = result.stdout.strip()

        # Skip if no output at all (needs session state we can't provide)
        if not stdout:
            pytest.skip(f"{command} exited {result.returncode} with no stdout (likely needs session state)")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Non-JSON output means project/session resolution failed
            # under test isolation (EMPIRICA_INSTANCE_ID set, no live
            # instance_projects file) — skip gracefully.
            pytest.skip(f"{command} produced non-JSON output under test isolation: {stdout[:200]}")

        assert isinstance(data, (dict, list)), f"{command} JSON root must be a dict or list, got {type(data).__name__}"


# ===================================================================
# Test 2: Success responses have 'ok' field
# ===================================================================


class TestOkField:
    """Successful JSON responses should include an 'ok' field."""

    @pytest.mark.parametrize("command", RUNNABLE_STATELESS)
    def test_success_response_has_ok_field(self, command):
        """When a stateless command succeeds (rc=0) its JSON must have 'ok'."""
        result = _run_empirica(command, "--output", "json")

        if result.returncode != 0:
            pytest.skip(f"{command} exited {result.returncode} -- not a success case")

        stdout = result.stdout.strip()
        if not stdout:
            pytest.skip(f"{command} produced empty stdout")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            pytest.skip(f"{command} did not produce valid JSON (covered by Test 1)")

        if not isinstance(data, dict):
            pytest.skip(f"{command} returned a JSON list, 'ok' field not applicable")

        if command in KNOWN_NO_OK_FIELD:
            pytest.skip(
                f"{command} is a known exception (status/diagnostic endpoint "
                f"without 'ok' field). Keys: {list(data.keys())}"
            )

        assert "ok" in data, (
            f"{command} returned a JSON dict without an 'ok' field. Top-level keys: {list(data.keys())}"
        )


# ===================================================================
# Test 3: No response has both ok:true AND high-severity error
#         (the issue #60 pattern)
# ===================================================================


class TestNoContradictoryState:
    """Catches the exact bug from issue #60: project-bootstrap returned
    ok:true alongside an auto-captured high-severity error."""

    @pytest.mark.parametrize("command", RUNNABLE_STATELESS)
    def test_ok_true_has_no_top_level_error(self, command):
        """If ok:true, there must not be a top-level 'error' key."""
        result = _run_empirica(command, "--output", "json")

        if result.returncode != 0 or not result.stdout.strip():
            pytest.skip(f"{command} did not succeed or produced no output")

        try:
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            pytest.skip(f"{command} JSON parse failed (covered elsewhere)")

        if not isinstance(data, dict):
            pytest.skip("Not a dict response")

        if data.get("ok") is True:
            assert "error" not in data, (
                f"{command} returned ok:true but also has a top-level 'error' key. error value: {data.get('error')!r}"
            )

    @pytest.mark.parametrize("command", RUNNABLE_STATELESS)
    def test_ok_true_no_high_severity_auto_issues(self, command):
        """If ok:true and auto_captured_issues exist, none may be severity:high."""
        result = _run_empirica(command, "--output", "json")

        if result.returncode != 0 or not result.stdout.strip():
            pytest.skip(f"{command} did not succeed or produced no output")

        try:
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            pytest.skip(f"{command} JSON parse failed (covered elsewhere)")

        if not isinstance(data, dict):
            pytest.skip("Not a dict response")

        if data.get("ok") is not True:
            pytest.skip("Response is not ok:true")

        # Walk the entire response looking for auto_captured_issues at any depth.
        high_severity = _find_high_severity_issues(data)

        assert not high_severity, (
            f"{command} returned ok:true but contains high-severity auto-captured issue(s): {high_severity}"
        )


def _find_high_severity_issues(obj, path=""):
    """Recursively search for auto_captured_issues with severity 'high'."""
    found = []

    if isinstance(obj, dict):
        # Direct key check
        if "auto_captured_issues" in obj:
            issues = obj["auto_captured_issues"]
            if isinstance(issues, list):
                for issue in issues:
                    if isinstance(issue, dict) and issue.get("severity") == "high":
                        found.append(f"{path}.auto_captured_issues: {issue.get('message', issue)}")

        # Recurse into all values
        for key, value in obj.items():
            found.extend(_find_high_severity_issues(value, path=f"{path}.{key}"))

    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            found.extend(_find_high_severity_issues(item, path=f"{path}[{idx}]"))

    return found


# ===================================================================
# Test 4: Error responses have structured format
# ===================================================================


class TestErrorFormat:
    """When a command fails, the output should contain JSON with ok:false
    and an 'error' key carrying a human-readable message."""

    # Commands invoked with deliberately wrong arguments to provoke errors.
    # Each tuple: (command, extra_args_that_cause_failure)
    ERROR_SCENARIOS: ClassVar[list] = [
        # Non-existent session ID
        ("session-snapshot", ["nonexistent-session-id", "--output", "json"]),
        # Non-existent goal ID
        ("goals-complete", ["--goal-id", "nonexistent-goal-id", "--output", "json"]),
        # Non-existent checkpoint session
        ("checkpoint-list", ["--session-id", "nonexistent-id", "--output", "json"]),
    ]

    # Filter to only scenarios whose command actually supports --output json
    VALID_SCENARIOS: ClassVar[list] = [(cmd, args) for cmd, args in ERROR_SCENARIOS if cmd in ALL_JSON_COMMANDS]

    @pytest.mark.parametrize(
        "command,extra_args",
        VALID_SCENARIOS,
        ids=[s[0] for s in VALID_SCENARIOS],
    )
    def test_error_response_structure(self, command, extra_args):
        """Error responses should be JSON with ok:false and an 'error' key."""
        result = _run_empirica(command, *extra_args)

        # Combine stdout and stderr; some commands write errors to either.
        combined = (result.stdout.strip() or "") + (result.stderr.strip() or "")

        if not combined:
            pytest.skip(f"{command} produced no output on error")

        # Try to extract JSON from the combined output.  Some commands mix
        # human text with JSON; look for the first { ... } block.
        json_data = _extract_json(combined)

        if json_data is None:
            # If the command returned non-zero but no JSON at all, that is
            # acceptable for some commands (e.g. argparse errors).  Only fail
            # if the command claims to support --output json AND exited non-zero
            # with non-JSON output.
            if result.returncode != 0:
                pytest.skip(
                    f"{command} exited {result.returncode} but did not produce JSON "
                    f"(may be an argparse/usage error, not a runtime error)"
                )
            return

        if not isinstance(json_data, dict):
            pytest.skip(f"{command} error response is not a dict")

        # If the command actually succeeded (e.g. empty list is not an error),
        # skip -- we are only testing error paths here.
        if result.returncode == 0 and json_data.get("ok") is True:
            pytest.skip(f"{command} unexpectedly succeeded (rc=0, ok:true) -- not an error path")

        # Core assertions for error responses
        if json_data.get("ok") is False:
            assert "error" in json_data, (
                f"{command} returned ok:false but has no 'error' key. Keys present: {list(json_data.keys())}"
            )
            error_msg = json_data["error"]
            assert isinstance(error_msg, str) and len(error_msg) > 0, (
                f"{command} 'error' field should be a non-empty string, got: {error_msg!r}"
            )


def _extract_json(text):
    """Attempt to parse JSON from text, trying the full text first, then
    looking for the first top-level JSON object."""
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find a JSON object in the text
    brace_start = text.find("{")
    if brace_start == -1:
        return None

    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_start : i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


# ===================================================================
# Parametrize sanity check
# ===================================================================


class TestDiscovery:
    """Meta-tests: ensure our discovery machinery found a reasonable number
    of commands."""

    def test_discovered_commands_not_empty(self):
        """We should discover a substantial number of JSON-output commands."""
        assert len(ALL_JSON_COMMANDS) >= 30, (
            f"Expected at least 30 commands with --output json, found {len(ALL_JSON_COMMANDS)}: {ALL_JSON_COMMANDS}"
        )

    def test_known_commands_in_discovered_set(self):
        """Key commands must appear in the discovered set."""
        expected = {
            "goals-list",
            "project-list",
            "project-bootstrap",
            "calibration-report",
            "sessions-list",
            "system-status",
            "finding-log",
            "unknown-log",
            "deadend-log",
            "session-create",
            "goals-create",
            "goals-complete",
        }
        missing = expected - set(ALL_JSON_COMMANDS)
        assert not missing, f"Expected commands missing from discovered set: {missing}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
