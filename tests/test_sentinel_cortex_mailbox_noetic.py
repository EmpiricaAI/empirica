"""sentinel-gate.py — cortex mailbox reads are noetic + namespace-aggregation normalization.

Regression guard for the ecodex/cortex Sentinel over-gating fix (thread
prop_3zjsf2cs → prop_iefo2tdx). cortex mailbox READS
(inbox_poll/outbox_poll/get_proposal/archive_proposal) + the ack
(complete_proposal) must be classified noetic (present in NOETIC_MCP_CORTEX),
and a bare `mcp__cortex` namespace (codex/ecodex aggregation) must normalize to
the full op name so the same classification applies — "option 2".
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HOOK = (
    Path(__file__).parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "sentinel-gate.py"
)
_spec = importlib.util.spec_from_file_location("sentinel_gate_mod", _HOOK)
assert _spec is not None and _spec.loader is not None
sg = importlib.util.module_from_spec(_spec)
sys.modules["sentinel_gate_mod"] = sg
_spec.loader.exec_module(sg)


def test_cortex_mailbox_reads_and_ack_are_noetic():
    for op in (
        "cortex_inbox_poll",
        "cortex_outbox_poll",
        "cortex_get_proposal",
        "cortex_archive_proposal",
        "cortex_complete_proposal",
    ):
        assert f"mcp__cortex__{op}" in sg.NOETIC_MCP_CORTEX


def test_praxic_cortex_tools_stay_out_of_noetic():
    # These are real state mutations / ECO-gated — must NOT be noetic.
    for op in ("cortex_propose", "cortex_publish"):
        assert f"mcp__cortex__{op}" not in sg.NOETIC_MCP_CORTEX


def test_bare_namespace_normalizes_to_full_op():
    norm = sg._normalize_aggregated_cortex_tool
    assert norm("mcp__cortex", {"op": "cortex_inbox_poll"}) == "mcp__cortex__cortex_inbox_poll"
    assert norm("mcp__cortex", {"operation": "cortex_get_proposal"}) == "mcp__cortex__cortex_get_proposal"
    assert norm("mcp__cortex", {"name": "cortex_outbox_poll"}) == "mcp__cortex__cortex_outbox_poll"
    assert norm("mcp__cortex__", {"tool": "cortex_archive_proposal"}) == "mcp__cortex__cortex_archive_proposal"


def test_pass_through_full_names_unknown_and_non_cortex():
    norm = sg._normalize_aggregated_cortex_tool
    # already-full name unchanged
    assert norm("mcp__cortex__cortex_inbox_poll", {}) == "mcp__cortex__cortex_inbox_poll"
    # bare namespace with no resolvable op → unchanged (fail-safe → stays gated)
    assert norm("mcp__cortex", {}) == "mcp__cortex"
    # non-cortex tool untouched
    assert norm("Bash", {"command": "ls"}) == "Bash"
