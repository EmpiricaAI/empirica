"""Findings from the 1.17.18 pre-publish sweep, deprecations and docs area (goal c42e1294)."""

from __future__ import annotations

import pytest

from empirica.cli import cli_core
from empirica.core import deprecations
from empirica.core.docs.semantic_scan import _should_skip


def _notices(argv, capsys):
    parser = cli_core.create_argument_parser()
    parsed = parser.parse_args(argv)
    cli_core._print_deprecation_notice(parsed.command, argv, parser)
    return capsys.readouterr().err


def test_a_deprecated_flag_typed_through_a_command_alias_gets_its_notice(capsys):
    """`empirica post --changes x` set command='post', so the flag's notice keyed on postflight-submit never fired."""
    assert "--changes" in _notices(["post", "-", "--changes", "x"], capsys)


@pytest.mark.parametrize("argv", [["postflight-submit", "-", "--chang", "x"], ["delete-artifacts", "-", "--dry"]])
def test_an_abbreviated_deprecated_flag_gets_its_notice(argv, capsys):
    assert "deprecated" in _notices(argv, capsys)


def test_no_notice_for_a_live_flag_or_another_command(capsys):
    assert _notices(["postflight-submit", "-", "--reasoning", "x"], capsys) == ""
    assert deprecations.flag_notices("postflight-submit", ["--changes-only"], {"--changes", "--changes-only"}) == []


def test_the_archive_skip_matches_a_folder_named_exactly_archive():
    assert _should_skip("docs/_archive/x.md")
    assert not _should_skip("docs/my_archive/x.md")


def test_the_removed_names_guard_matches_whole_names():
    from tests.test_removed_names_stay_removed import _mentions

    assert _mentions("run agent-spawn now", "agent-spawn")
    assert not _mentions("a subagent-spawn hook", "agent-spawn")
    assert not _mentions("EMPIRICA_SESSION_ID_FILE", "EMPIRICA_SESSION_ID")
    assert _mentions("call mcp__empirica__bootstrap_context", "mcp__empirica__")  # a prefix name still matches
