"""`empirica help` is a short list for people, like `claude --help`.

It printed 184 command names in categories. Now: a handful of everyday
commands with one line each (taken from each command's own parser help, so it
cannot drift), `help <area>` with descriptions, and `help all` for the overview.
"""

from __future__ import annotations

import types

from empirica.cli import cli_core


def _help(capsys, arg=None):
    cli_core._handle_help_command(types.SimpleNamespace(_help_category=arg))
    return capsys.readouterr().out


def test_the_short_list_names_commands_with_descriptions(capsys):
    out = _help(capsys)
    assert "Usage: empirica <command>" in out
    assert "doctor" in out and "Check Empirica install health" in out
    assert "empirica help all" in out


def test_every_listed_command_exists(capsys):
    """Positive control on the curated list: a renamed command must fail here."""
    helps = cli_core._command_helps()
    missing = [c for _h, cmds in cli_core._HUMAN_HELP for c in cmds if c not in helps]
    assert missing == []


def test_an_area_lists_descriptions(capsys):
    out = _help(capsys, "cockpit")
    assert "tui" in out and "Launch the interactive cockpit" in out


def test_all_keeps_the_overview(capsys):
    assert "All Empirica Commands" in _help(capsys, "all")


def test_an_unknown_area_says_so_and_shows_the_short_list(capsys):
    out = _help(capsys, "nope")
    assert "No help area 'nope'" in out and "Get started:" in out
