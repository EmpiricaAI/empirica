"""`goals-claim` must not move HEAD unless asked, and must say so when it does.

Reported by ecodex (prop_2gmong6onbdenarycy2xobktby, 2026-09-25): claiming an
in_progress goal only to link it checked out ``epistemic/reasoning/goal-<id>``,
printed "Branch mapping saved / Ready to start work!", and the next 7 commits
landed on that branch instead of the one being worked.
"""

from __future__ import annotations

import subprocess

import pytest


def _parse(*argv):
    from empirica.cli.cli_core import create_argument_parser

    return create_argument_parser().parse_args(["goals-claim", "--goal-id", "g1", *argv])


def test_branch_creation_is_off_by_default():
    assert _parse().create_branch is False


@pytest.mark.parametrize(("argv", "expected"), [(("--create-branch",), True), (("--no-branch",), False)])
def test_both_flags_still_parse(argv, expected):
    assert _parse(*argv).create_branch is expected


def test_without_the_flag_git_is_never_called(monkeypatch):
    from empirica.cli.command_handlers.goal_commands import _handle_goals_claim_command_helper

    def _no_git(*a, **k):
        raise AssertionError(f"git was called: {a}")

    monkeypatch.setattr(subprocess, "run", _no_git)
    result: dict = {}
    _handle_goals_claim_command_helper("ai", None, False, "abcdef12-0000", result, "s1")

    assert result["head_moved"] is False and result["branch_created"] is False


def test_with_the_flag_the_move_is_reported(tmp_path, monkeypatch):
    """Positive control: the same helper DOES move HEAD, and the result says so."""
    from empirica.cli.command_handlers.goal_commands import _handle_goals_claim_command_helper

    repo = tmp_path / "r"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    subprocess.run(["git", "init", "-q", "-b", "work"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x"], cwd=repo, check=True
    )
    monkeypatch.chdir(repo)
    import empirica.integrations.branch_mapping as bm

    monkeypatch.setattr(bm, "get_branch_mapping", lambda: type("M", (), {"add_mapping": lambda self, **k: None})())

    result: dict = {}
    _handle_goals_claim_command_helper("ai", None, True, "abcdef12-0000", result, "s1")

    head = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, check=True).stdout
    assert head.strip() == "epistemic/reasoning/goal-abcdef12"
    assert result["head_moved"] is True and result["branch_action"] == "created_new"


def test_a_failed_branch_is_reported_as_a_failure_not_as_ready(tmp_path, monkeypatch, capsys):
    """`--create-branch` outside a repository: the claim stands, the branch does not. It used to end
    "Ready to start work!" with head_moved absent and exit 0."""
    from empirica.cli.command_handlers.goal_commands import (
        _handle_goals_claim_command_helper,
        _print_goals_claim_human,
    )

    monkeypatch.chdir(tmp_path)  # not a git repository
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    result: dict = {"ok": True}

    _handle_goals_claim_command_helper("ai", None, True, "abcdef12-0000", result, "s1")
    _print_goals_claim_human(result, "abcdef12-0000", None)
    out = capsys.readouterr().out

    assert result["ok"] is False and result["head_moved"] is False and result["branch_created"] is False
    assert "not a git repository" in result["branch_error"]
    assert "Ready to start work" not in out and "HEAD did not move" in out
