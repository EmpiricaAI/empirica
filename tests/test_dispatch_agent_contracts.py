"""Every flag and parameter this skill prints must actually exist.

dispatch-agent has now shipped two phantom invocations:

  * `deadend-log --list` / `finding-log --list` — no such flag; fixed in e8a5cf7d5,
    and the skill now documents the absence so it does not come back
  * `"run_in_background": true` on the Agent tool — no such parameter, removed in
    the Claude-5 rewrite

Both are the same defect: documented, plausible, copied, and they fail only in the
caller. An AI following either got an error with no way to tell whether the skill
was wrong or its own invocation was — so it debugs itself instead of the doc.

The guard reads flags out of the SKILL and checks them against the live argparse
definitions, so a flag that is renamed or dropped fails here rather than in
someone's terminal.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SKILL = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "skills" / "dispatch-agent" / "SKILL.md"


def _skill() -> str:
    return _SKILL.read_text()


def _cli_invocations() -> list[tuple[str, list[str]]]:
    """(subcommand, flags) for every `empirica ...` line in a bash block."""
    out = []
    for line in re.findall(r"^empirica\s+(.+)$", _skill(), re.M):
        parts = line.split()
        if not parts:
            continue
        out.append((parts[0], [p for p in parts[1:] if p.startswith("--")]))
    return out


def test_the_skill_prints_at_least_one_cli_invocation():
    """Guards the guard: if the parse finds nothing, every test below passes
    vacuously and reports safety it never checked."""
    assert _cli_invocations(), "no `empirica ...` lines parsed — this guard would be inert"


def _live_flags(subcommand: str) -> set[str]:
    """Flags the REAL parser accepts for a subcommand.

    Built from `create_argument_parser()` rather than shelling out to `--help`:
    the parser is what actually parses, so this cannot drift from it, and it does
    not depend on the package being installed on PATH in CI.
    """
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    subs = next(a for a in parser._subparsers._group_actions if hasattr(a, "choices"))  # type: ignore[union-attr]

    assert subcommand in subs.choices, f"`empirica {subcommand}` is not a real subcommand"

    flags: set[str] = set()
    for action in subs.choices[subcommand]._actions:
        flags.update(action.option_strings)
    return flags


@pytest.mark.parametrize("subcommand,flags", _cli_invocations())
def test_every_printed_flag_exists_on_its_subcommand(subcommand, flags):
    live = _live_flags(subcommand)

    missing = sorted(f for f in flags if f not in live)
    assert not missing, f"dispatch-agent prints {missing} on `{subcommand}`, which does not accept them"


def test_no_list_flag_creeps_back_onto_the_log_verbs():
    """The original phantom. `*-log` verbs WRITE; retrieval is semantic."""
    assert not re.search(r"-log\s+--list", _skill()), "`--list` is back on a *-log verb"


def test_no_phantom_agent_parameters():
    """The Agent tool's real parameters. `run_in_background` was printed here for
    a long time and does not exist on this tool.
    """
    agent_block = re.search(r"Agent\(\{(.*?)\}\)", _skill(), re.S)
    assert agent_block, "the Agent dispatch example is gone — that IS the skill"

    keys = set(re.findall(r'"(\w+)":', agent_block.group(1)))
    real = {"description", "prompt", "subagent_type", "model", "isolation"}

    phantom = sorted(keys - real)
    assert not phantom, f"Agent parameters that do not exist: {phantom}"


def test_the_false_blank_subagent_premise_is_gone():
    """The skill opened with "subagents arrive blank". `fork` inherits the
    parent's full conversation context, so the premise was false and it argued
    for enrichment in the one case where enrichment is redundant.
    """
    text = _skill()

    assert "arrive blank" not in text
    assert "fork" in text, "fork must be named — it is when NOT to use this skill"


def test_numeric_thresholds_do_not_substitute_for_judgment():
    """`similarity > 0.5`, `top 5 by relevance`, `no more than 3-4 files` — each
    read as precision while encoding an arbitrary cut. A fixed cutoff drops the
    one dead-end that matters and admits four findings that don't.
    """
    text = _skill()

    assert not re.search(r"similarity\s*>\s*0\.\d", text)
    assert not re.search(r"top \d+ by relevance", text)


def test_the_trigger_surface_is_intact():
    """Amendment 3: front-matter is what makes the skill load, exempt from the trim."""
    desc = re.search(r"^description:\s*(.+)$", _skill(), re.M)
    assert desc, "no description front-matter — this skill can never trigger"

    body = desc.group(1).lower()
    for cue in ("dispatch", "agent", "context"):
        assert cue in body, f"trigger cue '{cue}' removed"
