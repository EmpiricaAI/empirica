"""No CLI verb rides in on another verb's Tier-1 prefix (gardening, unknown dd1157a5).

The Sentinel allows `empirica <prefix>...` before any CHECK by `cmd.startswith(prefix)` with no token boundary, so the prefix `empirica gl` also
matches a verb called `glue-apply`. Measured 2026-10-27 over the parser's 278 verbs: no collision today, but 15 prefixes name verbs that no longer
exist (`monitor`, `list-goals`, `query-mistakes`, ...), and a NEW mutating verb that happened to start with any of those words, or with a live
verb's name, would be allowed pre-CHECK with nothing failing. This pins the property the way it can be pinned: the day a verb collides, a test
says so, and the author either renames the verb or adds it to the read-shaped convention on purpose.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from empirica.cli.cli_core import create_argument_parser

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("sentinel_gate_tier1_collisions", _HOOK_DIR / "sentinel-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parser_verbs() -> set[str]:
    parser = create_argument_parser()
    for action in parser._actions:
        if getattr(action, "choices", None) and action.dest == "command":
            return set(action.choices)
    raise AssertionError("the parser exposes no subcommands")


def _collisions(verbs, prefixes, is_read_shaped) -> list[tuple[str, str]]:
    """(verb, prefix) pairs where a single-word Tier-1 prefix matches a DIFFERENT verb that is not read-shaped."""
    out = []
    for prefix in prefixes:
        parts = prefix.split()
        if len(parts) != 2 or parts[1].startswith("-"):
            continue  # a subcommand prefix ("mesh status") or a flag
        out.extend(
            (verb, prefix)
            for verb in sorted(verbs)
            if verb != parts[1] and f"empirica {verb}".startswith(prefix) and not is_read_shaped(f"empirica {verb}")
        )
    return out


def test_the_detector_finds_a_mutating_verb_that_shares_a_prefix():
    """Positive control: without it a green result could mean the loop walked nothing."""
    found = _collisions({"monitor", "monitor-restart"}, ("empirica monitor",), lambda _cmd: False)
    assert found == [("monitor-restart", "empirica monitor")]


def test_a_read_shaped_neighbour_is_not_a_collision():
    assert _collisions({"goals-list", "goals-list-all"}, ("empirica goals-list",), lambda _cmd: True) == []


def test_the_detector_walks_the_real_verbs_and_prefixes(gate):
    assert len(_parser_verbs()) > 200 and len(gate.EMPIRICA_TIER1_PREFIXES) > 50


def test_no_cli_verb_is_allowed_pre_check_by_another_verbs_prefix(gate):
    found = _collisions(_parser_verbs(), gate.EMPIRICA_TIER1_PREFIXES, gate.is_read_shaped_empirica_verb)
    assert found == [], (
        f"these verbs start with a Tier-1 read-only prefix and are not read-shaped, so the Sentinel allows them before any CHECK: {found}. "
        "Rename the verb, or tighten the prefix match."
    )
