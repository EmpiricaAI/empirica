"""`empirica investigate <query>` retrieves; a path target is refused, not analyzed.

The verb had zero working paths for its whole life: file and directory targets
imported two analyzer modules that were never shipped (an error dict printed
under a "✅ Investigation complete" banner, exit 0), `--type comprehensive`
raised on a field the parser never set, and a concept target returned a
hardcoded mock. The system prompt and noetic-batch both describe `investigate`
as retrieval, which is what it is now: an in-process alias of
`project-search --task`.
"""

from __future__ import annotations

import argparse
import json

import pytest

from empirica.cli.command_handlers import investigation_commands as ic
from empirica.cli.parsers.investigation_parsers import add_investigation_parsers


def _parse(argv):
    parser = argparse.ArgumentParser()
    add_investigation_parsers(parser.add_subparsers(dest="command"))
    return parser.parse_args(argv)


def test_a_query_is_delegated_to_project_search(monkeypatch):
    seen = {}

    def fake_search(ns):
        seen.update(vars(ns))
        return None

    monkeypatch.setattr("empirica.cli.command_handlers.project_search.handle_project_search_command", fake_search)
    args = _parse(["investigate", "how does the sentinel gate loops", "--limit", "3", "--global", "--output", "json"])
    assert ic.handle_investigate_command(args) is None
    assert seen["task"] == "how does the sentinel gate loops"
    assert seen["limit"] == 3
    assert seen["global_search"] is True
    assert seen["output"] == "json"
    assert seen["project_id"] is None  # resolved from the active project, like project-search itself


def test_an_existing_path_is_refused_with_exit_1_and_no_success_banner(tmp_path, monkeypatch, capsys):
    target = tmp_path / "module.py"
    target.write_text("x = 1\n")
    monkeypatch.setattr(
        "empirica.cli.command_handlers.project_search.handle_project_search_command",
        lambda _ns: pytest.fail("a path target must not be searched"),
    )
    rc = ic.handle_investigate_command(_parse(["investigate", str(target), "--output", "json"]))
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "does not analyze" in payload["error"]
    assert "✅" not in out


def test_the_dead_types_are_gone_from_the_parser():
    """No `--type file|directory|concept|comprehensive`, no `--session-id`
    that loaded a bootstrap anchor and discarded it: an accepted flag that
    nothing reads is an advertised no-op."""
    with pytest.raises(SystemExit):
        _parse(["investigate", "q", "--type", "comprehensive"])
    with pytest.raises(SystemExit):
        _parse(["investigate", "q", "--session-id", "abc"])
    assert not hasattr(ic, "handle_analyze_command")
    assert not hasattr(ic, "_investigate_file")
