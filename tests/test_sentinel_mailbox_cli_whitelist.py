"""Sentinel whitelist coverage for the `empirica mailbox` CLI family.

Regression lock for ecodex prop_b77tsh6o: #255 shipped `empirica mailbox
poll/show/reply/archive` but never added them to sentinel-gate.py's tiered CLI
whitelist. Consequence: `is_safe_empirica_command("empirica mailbox poll …")`
was False, so in `_handle_no_preflight` (mesh-woken IDLE practitioner, no open
transaction) the command was denied "No open transaction" — breaking the exact
wake→poll→react last mile the mailbox CLI exists to close.

Fix: reads (poll/show) → Tier 1 (any phase); state-changing workflow verbs
(reply/archive) → Tier 2. Both satisfy is_safe_empirica_command → all four flow
pre-transaction, preserving the read-vs-mutation split.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _load_sentinel_gate():
    if "sentinel_gate" in sys.modules:
        del sys.modules["sentinel_gate"]
    spec = importlib.util.spec_from_file_location("sentinel_gate", PLUGIN_HOOKS / "sentinel-gate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PLUGIN_HOOKS.parent / "lib"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_sentinel_gate()


# All four verbs must classify as safe empirica commands (flow pre-transaction).
@pytest.mark.parametrize(
    "cmd",
    [
        "empirica mailbox poll --ai-id empirica.david.empirica --output json",
        "empirica mailbox poll --outbox --status completed,changed",
        "empirica mailbox show prop_abc123 --output json",
        "empirica mailbox reply --parent-id prop_x --summary 'done' --result shipped",
        "empirica mailbox archive prop_x --reason cleanup",
    ],
)
def test_mailbox_verbs_are_safe_empirica_commands(gate, cmd):
    assert gate.is_safe_empirica_command(cmd) is True
    assert gate.is_safe_bash_command({"command": cmd}) is True


# Read-vs-mutation split: poll/show are Tier 1, reply/archive are Tier 2.
def test_reads_are_tier1(gate):
    assert "empirica mailbox poll" in gate.EMPIRICA_TIER1_PREFIXES
    assert "empirica mailbox show" in gate.EMPIRICA_TIER1_PREFIXES


def test_mutations_are_tier2(gate):
    assert "empirica mailbox reply" in gate.EMPIRICA_TIER2_PREFIXES
    assert "empirica mailbox archive" in gate.EMPIRICA_TIER2_PREFIXES


# Load-bearing case: the wake-first-action poll — segment-safe inside a
# `cd <dir> && …` chain too (a woken practitioner's first shell line).
def test_wake_first_poll_in_cd_chain_is_safe(gate):
    cmd = "cd /home/u/proj && empirica mailbox poll --ai-id empirica.david.empirica --output json"
    assert gate.is_safe_bash_command({"command": cmd}) is True
