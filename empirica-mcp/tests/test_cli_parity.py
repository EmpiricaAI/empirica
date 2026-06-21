"""Parity guard: every MCP tool's CLI mapping must match the real CLI.

The Empirica MCP server is a thin wrapper that shells out to the `empirica`
CLI (needed for Desktop/Chat AIs that can't run bash). Its `TOOL_REGISTRY`
maps each tool to a `cli` subcommand + `params` (mcp-arg → --flag). That map
is hand-maintained and silently drifts as the CLI evolves: a renamed/removed
flag still listed in the registry is accepted by the MCP schema but REJECTED
by argparse at call time — a silent capability loss (e.g. `--description` was
added to every *-log command but never surfaced via MCP; `goals_create`
exposed 3 of 16 flags).

This guard introspects the REAL argparse parser (`create_argument_parser`) and
asserts, for every registry entry:

  A. its `cli` subcommand exists, and
  B. every mapped `--flag` is a real option on that subcommand.

It is the MCP analogue of the SQL schema-reference guard: catch the drift class
deterministically instead of by 77-day-stale manual re-verification.

NOT checked here: coverage gaps (CLI commands with no MCP tool) — that's a
curation decision, not a correctness bug.
"""

from __future__ import annotations

import argparse

import pytest
from empirica_mcp.server import TOOL_REGISTRY

from empirica.cli.cli_core import create_argument_parser


def _subcommand_choices() -> dict[str, argparse.ArgumentParser]:
    parser = create_argument_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("no subparsers found on the empirica argument parser")


def _flags_of(subparser: argparse.ArgumentParser) -> set[str]:
    """All option strings (--flag / -f) accepted by a subparser."""
    flags: set[str] = set()
    for action in subparser._actions:
        flags.update(action.option_strings)
    return flags


_CHOICES = _subcommand_choices()


# Allow-list for known, deliberate divergences (ratchet). Keep empty; add a
# (tool, flag) tuple ONLY with a comment justifying why the flag legitimately
# has no CLI counterpart.
_KNOWN_FLAG_DIVERGENCES: frozenset[tuple[str, str]] = frozenset()


def _registry_items():
    return sorted(TOOL_REGISTRY.items())


@pytest.mark.parametrize("tool_name,entry", _registry_items())
def test_mcp_tool_cli_exists(tool_name, entry):
    """A — every registry entry's cli subcommand exists."""
    cli = entry["cli"]
    first = cli.split()[0]  # multi-token clis like "listener on" → top subcommand
    assert first in _CHOICES, (
        f"MCP tool '{tool_name}' targets `empirica {cli}` but '{first}' is not a "
        f"CLI subcommand. The command was renamed or removed — update or drop "
        f"the registry entry."
    )


@pytest.mark.parametrize("tool_name,entry", _registry_items())
def test_mcp_tool_flags_exist_in_cli(tool_name, entry):
    """B — every mapped --flag is a real option on the CLI subcommand."""
    cli = entry["cli"]
    tokens = cli.split()
    # Multi-token clis (e.g. "listener on") resolve flags on a nested subparser
    # whose introspection is brittle; the param-drift bug class lives in the
    # single-verb logging/goal tools, so we flag-check those and assert only
    # subcommand existence for multi-token ones (covered by test A).
    if len(tokens) != 1:
        pytest.skip(f"multi-token cli '{cli}' — flag-checked at the CLI layer")
    sub = _CHOICES.get(tokens[0])
    if sub is None:
        pytest.skip("subcommand existence is asserted by test_mcp_tool_cli_exists")

    valid = _flags_of(sub)
    mapped = entry.get("params", {})
    missing = [
        flag for flag in mapped.values() if flag not in valid and (tool_name, flag) not in _KNOWN_FLAG_DIVERGENCES
    ]
    assert not missing, (
        f"MCP tool '{tool_name}' (→ `empirica {cli}`) maps flag(s) {missing} that "
        f"the CLI no longer accepts. argparse will reject them at call time — a "
        f"silent capability loss. Fix the registry `params` to match the CLI, or "
        f"add (tool, flag) to _KNOWN_FLAG_DIVERGENCES with justification."
    )


# Capability floor: flags a tool MUST expose (not just "may"). The registry
# intentionally omits many CLI flags, but these are core capabilities whose
# silent omission was the actual divergence bug — Desktop/Chat AIs writing
# title-only artifacts and skeleton goals. This locks the fix so the gap can't
# silently reopen when the registry is next edited.
_REQUIRED_FLAGS: dict[str, set[str]] = {
    "finding_log": {"--description"},
    "unknown_log": {"--description"},
    "deadend_log": {"--description"},
    "assumption_log": {"--description"},
    "decision_log": {"--description", "--source"},
    "mistake_log": {"--description"},
    "goals_create": {"--description", "--scope-breadth", "--status"},
}


@pytest.mark.parametrize("tool_name", sorted(_REQUIRED_FLAGS))
def test_mcp_tool_exposes_required_capability_flags(tool_name):
    """Curated capability floor — core flags must stay exposed via MCP."""
    entry = TOOL_REGISTRY.get(tool_name)
    assert entry is not None, f"expected tool '{tool_name}' in TOOL_REGISTRY"
    exposed = set(entry.get("params", {}).values())
    missing = sorted(_REQUIRED_FLAGS[tool_name] - exposed)
    assert not missing, (
        f"MCP tool '{tool_name}' must expose {missing} but doesn't. These are "
        f"core capabilities (rich --description body, goal scope/status) — without "
        f"them Desktop/Chat AIs silently lose them. Add to the registry `params`."
    )
