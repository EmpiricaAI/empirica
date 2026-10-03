"""Mesh and credential-state reads the Sentinel refused before CHECK, and the writes beside them.

ecodex-lab (prop_d24tvfzlhfawzewirnxrtt6jsi) had `empirica auth status --output json` refused with
"No CHECK" between PREFLIGHT and CHECK, and the harness `monitor` tool refused with `{action: "list"}`.
A probe of the classifier found two more reads in the same state: `empirica mailbox sers` and
`empirica mesh tail`. Each row below pairs the read that must flow with the write next to it that must
not, because widening a prefix list is how a write gets through.

The gate is loaded by path (hyphenated name) and driven through the same functions `main()` calls.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("sentinel_gate_mesh_reads", HOOKS / "sentinel-gate.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _safe(gate, command: str) -> bool:
    return bool(gate.is_safe_bash_command({"command": command}))


READS = [
    "empirica auth status",
    "empirica auth status --output json",
    "empirica mailbox sers",
    "empirica mailbox sers --output json",
    "empirica mailbox sers prop_abc --output json",
    "empirica mesh tail",
    "empirica mesh tail --instance empirica",
]

WRITES_BESIDE_THEM = [
    "empirica auth token",
    "empirica auth token --headers",
    "empirica auth login",
    "empirica auth logout",
    "empirica auth connectors --apply",
    "empirica auth connectors --name empirica-crm --apply",
    "empirica auth connectors --app",  # argparse accepts the abbreviation
    "empirica mesh restart",
    "empirica mesh on",
    "empirica mesh off",
    "empirica mesh migrate-topics",
]


@pytest.mark.parametrize("command", READS)
def test_the_mesh_read_flows_before_check(gate, command):
    assert _safe(gate, command)


@pytest.mark.parametrize("command", WRITES_BESIDE_THEM)
def test_the_write_beside_it_is_still_gated(gate, command):
    assert not _safe(gate, command)


@pytest.mark.parametrize(
    "command",
    [
        "empirica auth status && rm -rf /tmp/x",
        "empirica auth status; empirica auth token",
        "empirica mailbox sers > ~/.bashrc",
        "empirica mesh tail | tee /tmp/x",
    ],
)
def test_a_listed_read_does_not_carry_a_second_statement_or_a_redirect(gate, command):
    assert not _safe(gate, command)


def test_a_different_binary_with_the_same_prefix_is_not_blessed(gate):
    assert not _safe(gate, "empirica-auth status")
    assert not _safe(gate, "empiricafoo auth status")


# ── the monitor tool ────────────────────────────────────────────────────────


def _noetic(gate, tool: str, tool_input) -> bool:
    return gate._noetic_firewall_check(tool, tool_input, {}) is not None


def test_monitor_list_is_a_read(gate):
    assert _noetic(gate, "monitor", {"action": "list"})


@pytest.mark.parametrize(
    "tool_input",
    [
        {"action": "arm", "command": "tail -f x"},
        {"action": "kill", "id": "1"},
        {"action": "List"},
        {"action": ["list"]},
        {"action": ""},
        {},
        None,
        "list",
    ],
)
def test_monitor_anything_but_list_stays_gated(gate, tool_input):
    assert not _noetic(gate, "monitor", tool_input)


def test_claude_codes_own_monitor_tool_is_not_matched(gate):
    """It carries no `action`: arming a watch changes state, and this classifier must not touch it."""
    assert not _noetic(gate, "Monitor", {"command": "tail -F x", "description": "d", "timeout_ms": 1000})
    assert not _noetic(gate, "Monitor", {"action": "list"})
