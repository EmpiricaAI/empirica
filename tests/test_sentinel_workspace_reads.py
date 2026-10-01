"""The Sentinel treats `empirica-workspace` reads as noetic, and keeps its writes gated.

empirica-workspace (prop_ibxf5u3nvbenxk5k2s7lvoqwcy, 2026-09-29): after a PREFLIGHT whose only claim
was `retrieved`, the gate refused `empirica-workspace contact list --limit 1000 --output json | jq`.
`empirica-workspace` is a separate binary, so the `empirica <verb>` tiers never matched it. The
fix is an EXACT (group, action) table, because the workspace CLI keeps its writes beside its reads
in the same group (`org list|show|create|update`).

The negative half is the point: a table that waves reads through is only safe if every write
sibling provably still gates.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate():
    sys.modules.pop("sentinel_gate", None)
    spec = importlib.util.spec_from_file_location("sentinel_gate", HOOKS / "sentinel-gate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HOOKS.parent / "lib"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


READS = [
    "empirica-workspace org list",
    "empirica-workspace org show o-nle --output json",
    "empirica-workspace contact list --limit 1000 --output json",
    "empirica-workspace contact show c-1",
    "empirica-workspace engagement list --status active",
    "empirica-workspace engagement show eng-2026-07-01-nle-crm-build",
    "empirica-workspace touchpoint list --engagement-id e1",
    "empirica-workspace revenue-event list",
    "empirica-workspace entity knowledge --entity o-acme",
    "empirica-workspace entity recall --entity o-acme",
    "empirica-workspace engagement materials e1",
    "empirica-workspace crm-sync preview",
    "empirica-workspace org --help",
]

WRITES = [
    "empirica-workspace org create --name Acme",
    "empirica-workspace org update o-acme --status active",
    "empirica-workspace contact create --name X --email x@y.z",
    "empirica-workspace contact update c-1 --status x",
    "empirica-workspace contact consent c-1 --event consented",
    "empirica-workspace engagement create --name e",
    "empirica-workspace engagement update e1 --status completed",
    "empirica-workspace engagement set-id e1 --domain x",
    "empirica-workspace engagement remint-ids",
    "empirica-workspace engagement add-task e1 --description x",
    "empirica-workspace engagement add-material e1 --path x",
    "empirica-workspace engagement rebuild-repair --yes",
    "empirica-workspace touchpoint add --engagement-id e1",
    "empirica-workspace revenue-event add --engagement-id e1 --amount 1",
    "empirica-workspace entity link --entity o-x --artifact-id a",
    "empirica-workspace entity unlink --entity o-x --artifact-id a",
    "empirica-workspace entity remember --entity o-x",
    "empirica-workspace crm-sync push",
    "empirica-workspace crm-sync pull",
    "empirica-workspace crm-sync plan",
    "empirica-workspace init",
    "empirica-workspace clients create --name x",
]


@pytest.mark.parametrize("cmd", READS)
def test_workspace_reads_flow_between_transactions(gate, cmd):
    assert gate.is_safe_empirica_command(cmd) is True
    assert gate.is_safe_bash_command({"command": cmd}) is True


@pytest.mark.parametrize("cmd", WRITES)
def test_workspace_writes_still_gate(gate, cmd):
    """The negative control for the whole table: not one write sibling may pass."""
    assert gate.is_safe_empirica_command(cmd) is False
    assert gate.is_safe_bash_command({"command": cmd}) is False


def test_the_reported_pipeline_flows(gate):
    cmd = "empirica-workspace contact list --limit 1000 --output json | jq '.contacts | length'"
    assert gate.is_safe_bash_command({"command": cmd}) is True


def test_a_read_cannot_launder_a_write_through_a_chain_or_a_second_statement(gate):
    assert (
        gate.is_safe_bash_command({"command": "empirica-workspace org list && empirica-workspace org create --name x"})
        is False
    )
    assert gate.is_safe_bash_command({"command": "empirica-workspace org list\nrm -rf /tmp/x"}) is False
    assert gate.is_safe_empirica_statement("empirica-workspace org list; rm -rf /tmp/x") is False


def test_a_read_word_later_in_the_line_does_not_bless_a_write(gate):
    """The action is decided by position, so a trailing `list` or `show` proves nothing."""
    assert gate.is_safe_empirica_command("empirica-workspace org create --name list") is False
    assert gate.is_safe_empirica_command("empirica-workspace crm-sync push show") is False


def test_a_different_binary_with_a_similar_name_is_not_the_workspace_cli(gate):
    assert gate.is_safe_empirica_command("empirica-workspaces org list") is False
    assert gate.is_safe_empirica_command("empirica-workspace-evil org list") is False


def test_the_empirica_verbs_are_unchanged(gate):
    """CONTROL: the ordinary tiers still answer as before."""
    assert gate.is_safe_empirica_command("empirica goals-list") is True
    assert gate.is_safe_empirica_command("empirica rebuild --qdrant-only") is False
