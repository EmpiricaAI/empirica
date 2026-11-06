"""The help for cockpits and project types says what a person needs before they open the guide.

David, 2026-11-06: more help about the `--profile` flag and the other cockpit options, and project types explained where they are set.

Cockpit: `empirica cockpit --help` was one folded paragraph that never mentioned profiles, and three of the five actions described
`--profile` as a path with no word on naming, checking or what a surface is. Each action now says what it does, with one example.

Project types: the 16 names were listed and their meanings lived only in the taxonomy. The meanings are in the types module; the help,
the refusal of an unknown value and the taxonomy table all read them from there.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from empirica.cli.cli_core import create_argument_parser
from empirica.core import project_types as pt

ROOT = Path(__file__).parent.parent


def _sub(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and name in choices:
            return choices[name]
    raise AssertionError(f"no subcommand {name}")


@pytest.fixture(scope="module")
def parser():
    return create_argument_parser()


@pytest.fixture(scope="module")
def cockpit(parser):
    return _sub(parser, "cockpit")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# ── cockpit ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_cockpit_help_keeps_its_layout(cockpit):
    """The state-file table and the examples are lines, not one folded paragraph."""
    text = cockpit.format_help()
    assert re.search(r"^\s+empirica cockpit launch --profile \S+", text, flags=re.M)
    assert re.search(r"^\s+config\.yaml\s", text, flags=re.M)


@pytest.mark.parametrize(
    "fact",
    [
        "--profile NAME",  # the flag itself, at the top level where people look first
        "~/.empirica/cockpit/config-NAME.yaml",  # where a profile lives and how its name maps to a file
        "session_name",  # each profile needs its own
        "cockpit status",  # the check for a hand-edited profile
        "over SSH",  # the tmux surface works on a machine with no display
        "alacritty",
        "ghostty",
        "refresh",
        "EMPIRICA_INSTANCE_ID",
        "instance_id:",
        "resume:",
        "docs/guides/COCKPIT.md",
    ],
)
def test_the_cockpit_help_states_what_the_guide_states(cockpit, fact):
    assert fact in _flat(cockpit.format_help()), fact


@pytest.mark.parametrize("action", ["launch", "refresh", "status", "detach", "kill"])
def test_every_action_says_what_it_does_and_shows_an_example(cockpit, action):
    sub = _sub(cockpit, action)
    text = sub.format_help()
    assert sub.description and len(sub.description) > 60, action
    assert re.search(rf"^\s+empirica cockpit {action}\b", text, flags=re.M), f"{action}: no example line"


@pytest.mark.parametrize("action", ["launch", "refresh", "status", "kill"])
def test_the_profile_flag_reads_the_same_on_every_action_that_takes_it(cockpit, action):
    sub = _sub(cockpit, action)
    helps = {a.help for a in sub._actions if "--profile" in a.option_strings}
    assert len(helps) == 1
    text = _flat(helps.pop())
    assert "config-NAME.yaml" in text and "unknown name" in text


def test_status_help_says_it_checks_a_profile_and_changes_nothing(cockpit):
    text = _flat(_sub(cockpit, "status").format_help())
    assert "exits 1" in text and "touches nothing" in text


def test_launch_help_says_which_surface_works_over_ssh(cockpit):
    text = _flat(_sub(cockpit, "launch").format_help())
    assert "over SSH" in text and "display" in text


# ── project types ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_every_type_has_a_meaning_and_nothing_else_does():
    assert set(pt.TYPE_MEANINGS) == set(pt.PROJECT_TYPES)
    assert all(meaning and not meaning.endswith(".") for meaning in pt.TYPE_MEANINGS.values())


def test_the_taxonomy_table_gives_the_same_meanings():
    doc = (ROOT / "docs" / "reference" / "TAXONOMY.md").read_text()
    section = doc[doc.index("**Project types.**") : doc.index("**How the project type is used.**")]
    listed = dict(re.findall(r"^\| `([a-z-]+)` \| ([^|]+?) \|", section, flags=re.M))
    assert listed == pt.TYPE_MEANINGS


@pytest.mark.parametrize("verb", ["project-init", "project-update"])
def test_the_type_help_gives_every_type_with_its_meaning(parser, verb):
    text = _sub(parser, verb).format_help()
    for name, meaning in pt.TYPE_MEANINGS.items():
        assert re.search(rf"^\s+{re.escape(name)}\s+{re.escape(meaning)}$", text, flags=re.M), name


@pytest.mark.parametrize("verb", ["project-init", "project-update"])
def test_an_unknown_type_is_refused_with_the_same_list(parser, verb, capsys):
    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args([verb, "--type", "sofware"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "sofware" in err
    for name, meaning in pt.TYPE_MEANINGS.items():
        assert name in err and meaning in err, name


@pytest.mark.parametrize("verb", ["project-init", "project-update"])
def test_a_legacy_type_is_refused_and_says_it_is_legacy(parser, verb, capsys):
    with pytest.raises(SystemExit):
        parser.parse_args([verb, "--type", "product"])
    assert "legacy" in capsys.readouterr().err


@pytest.mark.parametrize("verb", ["project-init", "project-update"])
def test_a_listed_type_is_accepted(parser, verb):
    assert parser.parse_args([verb, "--type", "web-app"]).type == "web-app"
