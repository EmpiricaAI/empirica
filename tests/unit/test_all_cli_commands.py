"""CLI smoke tests: each command in the table exists, accepts its arguments, and does the one thing its row claims.

These run the CLI of THIS source tree (`sys.executable -m empirica.cli` with PYTHONPATH set), in a throwaway working directory, with HOME and
the session database under `tmp_path` and a PATH that holds only the system binaries. The first version ran a bare `empirica` from PATH in
whatever directory pytest happened to be in, and that measured the box: 33 of 33 failed in a clean environment (`env -i`), four commands only
passed because the repo's own `.empirica` happened to be there, and the 5-second timeouts failed under load. The timeout here (120 s) is a hang
guard, not a performance assertion.

These are INTERFACE tests. They verify that a command exists and accepts its arguments; the quality of an epistemic assessment
(preflight / check / postflight) needs integration tests with real reasoning, so no dummy epistemic data appears here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from empirica.data.session_database import SessionDatabase

REPO_ROOT = Path(__file__).resolve().parents[2]
HANG_GUARD_SECONDS = 120


@pytest.fixture
def cli(tmp_path):
    """Run the CLI of this tree against an empty, throwaway project. Returns `run(*args) -> CompletedProcess` (text mode)."""
    db_path = tmp_path / "sessions.db"
    SessionDatabase(
        db_path=str(db_path)
    ).close()  # a real, empty database: "no database" is a failure, "no rows" is a valid listing
    workdir = tmp_path / "work"
    workdir.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(REPO_ROOT),
        "EMPIRICA_SESSION_DB": str(db_path),
        "EMPIRICA_NO_TELEMETRY": "1",
    }

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "empirica.cli", *args],
            capture_output=True,
            text=True,
            cwd=workdir,
            env=env,
            timeout=HANG_GUARD_SECONDS,
        )

    return run


# Every command whose row in the old table was a bare `--help` check, grouped as the table was.
HELP_COMMANDS = [
    # core workflow (the AI-first *-submit commands read a config file or stdin JSON)
    "check",
    "preflight-submit",
    "check-submit",
    "postflight-submit",
    # goals
    "goals-create",
    "goals-add-task",
    "goals-complete-task",
    "goals-progress",
    "goals-resume",
    # git checkpoints
    "checkpoint-create",
    "checkpoint-load",
    "checkpoint-list",
    "checkpoint-diff",
    # handoff, sessions, identity, investigation, performance
    "handoff-create",
    "sessions-show",
    "sessions-export",
    "identity-create",
    "identity-export",
    "investigate",
    "goal-analysis",
    "efficiency-report",
]


@pytest.fixture(scope="module")
def cli_parser():
    """The CLI's argument parser, built once: building it is what a spawned process pays for on every call (~1.4s)."""
    from empirica.cli.cli_core import create_argument_parser

    return create_argument_parser()


@pytest.mark.parametrize("command", HELP_COMMANDS)
def test_the_command_has_a_working_help_that_names_it(cli_parser, capsys, command):
    """Exit 0 alone passes on empty output; the usage line must name the command it documents.

    The prefix before the command differs by Python: 3.14 prints `empirica check [-h]`, 3.11 and 3.13 print the main parser's custom
    usage string first (`empirica [--version] [--verbose] <command> [args] check [-h]`), wrapped by terminal width. `<command> [-h]` is
    the part every version agrees on (the first version asserted `empirica <command>` and passed only on the developer's 3.14).

    In process on one parser: this was a subprocess per command (21 spawns, ~30s). The end-to-end entry point is still exercised by the
    subprocess tests below and by tests/integrity/test_cli_contracts.py.
    """
    with pytest.raises(SystemExit) as stopped:
        cli_parser.parse_args([command, "--help"])
    out = capsys.readouterr().out
    assert stopped.value.code == 0
    assert f"{command} [-h]" in " ".join(out.split()), (
        f"--help for {command} does not say what it documents:\n{out[:300]}"
    )


# Commands that run without arguments. Each is measured against the empty database from the `cli` fixture.
def test_goals_list_runs_against_an_empty_database(cli):
    result = cli("goals-list")
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_goals_discover_finds_nothing_in_a_directory_with_no_git_history(cli):
    result = cli("goals-discover")
    assert result.returncode == 0, result.stderr
    assert "No goals found" in result.stdout


def test_handoff_query_runs_against_an_empty_database(cli):
    result = cli("handoff-query")
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_sessions_list_runs_against_an_empty_database(cli):
    """An empty registry IS a valid listing (exit 0, zero rows); a missing database is not."""
    result = cli("sessions-list")
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_sessions_resume_runs_against_an_empty_database(cli):
    result = cli("sessions-resume")
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_identity_list_finds_no_identities_under_a_fresh_home(cli):
    result = cli("identity-list")
    assert result.returncode == 0, result.stderr
    assert "No identities found" in result.stdout


def test_config_shows_the_configuration(cli):
    result = cli("config")
    assert result.returncode == 0, result.stderr
    assert "Empirica Configuration" in result.stdout


# Negative control for the whole file: a database-backed command must FAIL when there is no database, so the passes above are not vacuous.
def test_a_database_backed_command_fails_when_there_is_no_database(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(REPO_ROOT), "EMPIRICA_NO_TELEMETRY": "1"}
    workdir = tmp_path / "bare"
    workdir.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "empirica.cli", "sessions-list"],
        capture_output=True,
        text=True,
        cwd=workdir,
        env=env,
        timeout=HANG_GUARD_SECONDS,
    )
    assert result.returncode != 0
    assert "Cannot determine sessions.db path" in result.stdout


# Removed verbs: each used to answer with a fabricated result and exit 0. They must now be refused BY THE PARSER, not merely fail somehow.
@pytest.mark.parametrize(
    ("verb", "why"),
    [
        (
            "identity-verify",
            "answered every session with ok:false 'not yet implemented' and exit 0; verification lives in checkpoint-verify",
        ),
        (
            "performance",
            "rendered a graded scorecard (Grade B, green/yellow dimensions) from constants in a self-declared stub",
        ),
        (
            "monitor",
            "printed a deprecation notice for adapter monitoring removed long before and accepted nine flags it never read",
        ),
    ],
)
def test_a_removed_verb_is_refused_by_the_parser(cli, verb, why):
    """`returncode != 0` also passes on a crash; the parser's 'invalid choice' is what pins the removal."""
    result = cli(verb)
    assert result.returncode == 2, (why, result.stdout, result.stderr)
    assert "invalid choice" in result.stderr and verb in result.stderr


def test_main_help_works_and_names_the_tool(cli):
    result = cli("--help")
    assert result.returncode == 0
    assert "usage: empirica" in result.stdout


def test_the_compact_help_lists_the_core_commands(cli):
    """The compact help shows the core commands (workflow, artifacts, goals, context); the rest are under `empirica help`."""
    compact = cli("--help").stdout
    for command in (
        "preflight-submit",
        "check",
        "postflight-submit",
        "goals-create",
        "finding-log",
        "project-bootstrap",
    ):
        assert command in compact, f"{command} missing from the compact help"
    assert "checkpoint" in cli("help").stdout, "non-core commands are listed by `empirica help`"
