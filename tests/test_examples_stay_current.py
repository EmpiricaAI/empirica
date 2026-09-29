"""The example agents must keep working against the CLI they document.

examples/ went months untouched (last commit 2026-06-23) and nothing noticed, because
nothing ran it. This is the cheapest guard that would have: every `empirica <verb>`
and `--flag` the examples mention must exist in the real parser, and none may tell
users to install into the plugin's own agents/ directory, which `setup-claude-code`
and plugin sync rebuild (the copied agent is backed up to empirica.bak and removed).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _options(parser) -> set[str]:
    """Every option of a parser and of every sub-action beneath it (`cockpit launch --profile`)."""
    out = {o for a in parser._actions for o in a.option_strings}
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for child in a.choices.values():
                out |= _options(child)
    return out


def _verbs_and_flags() -> dict[str, set[str]]:
    from empirica.cli.cli_core import create_argument_parser

    sub = create_argument_parser()._subparsers._group_actions[0]
    assert isinstance(sub, argparse._SubParsersAction)
    return {name: _options(p) for name, p in sub.choices.items()}


def _commands(text: str):
    for line in text.replace("\\\n", " ").splitlines():
        m = re.search(r"\bempirica\s+([a-z][a-z0-9-]+)(.*)", line)
        if m:
            yield m.group(1), m.group(2)


def _md_files():
    files = sorted(EXAMPLES.rglob("*.md"))
    assert files, "positive control: the enumerator found no example files, so a pass would be vacuous"
    return files


def test_the_checker_can_fail():
    """Positive control on the parser table the other tests trust."""
    table = _verbs_and_flags()
    assert "--finding" in table["finding-log"]
    assert "--no-such-flag" not in table["finding-log"]


@pytest.mark.parametrize("md", _md_files(), ids=lambda p: str(p.relative_to(EXAMPLES)))
def test_every_verb_and_flag_an_example_mentions_exists(md):
    table = _verbs_and_flags()
    problems = []
    for verb, rest in _commands(md.read_text()):
        if verb == "install":  # prose: "empirica install ..." never a command
            continue
        if verb not in table:
            problems.append(f"unknown verb: {verb}")
            continue
        problems += [
            f"{verb}: unknown flag {flag}"
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]+)", rest)
            if flag not in table[verb]
        ]
    assert not problems, f"{md.relative_to(EXAMPLES)} documents commands that do not exist: {problems}"


def test_no_example_installs_into_the_plugin_agents_directory():
    """A `cp ... plugins/local/empirica/agents/` line is an instruction; a `# Not ...` line is the warning."""
    offenders = []
    for md in _md_files():
        for n, line in enumerate(md.read_text().splitlines(), 1):
            if (
                "plugins/local/empirica/agents" in line
                and not re.match(r"\s*(#|`|[^cm]*that folder)", line)
                and "cp " in line
            ):
                offenders.append(f"{md.relative_to(EXAMPLES)}:{n}: {line.strip()}")
    assert not offenders, offenders
