"""NOETIC_MCP_CORTEX is not read-only, and its header used to say it was.

The set is what someone auditing the noetic firewall reads. It carried the label
*"Cortex MCP tools (all read-only search/investigate)"* above `cortex_finding_log`,
`cortex_goal_create`, `cortex_log_artifacts`, `ingest_file`, `ingest_batch` and the
bus dispatch family — every one of which mutates cortex-side state.

The individual entries were honest; the header was not, and **a reader who believes
the label stops looking.** Worse, it erased that the memberships are *decisions*:
admitting the epistemic-workflow writes is a deliberate trade (gating the act of
recording what you learned would cost more than the work it measures), and a blanket
"read-only" makes that read like an observation nobody had to make.

These tests pin the property that matters — **the set cannot silently acquire a
praxic tool** — rather than the wording, which is free to improve.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_HOOK = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "sentinel-gate.py"
_spec = importlib.util.spec_from_file_location("sentinel_gate_label_mod", _HOOK)
assert _spec is not None and _spec.loader is not None
sg = importlib.util.module_from_spec(_spec)
sys.modules["sentinel_gate_label_mod"] = sg
_spec.loader.exec_module(sg)

SOURCE = _HOOK.read_text()

#: Entries in the set that write cortex-side state. Admitted deliberately; listed
#: here so the header can never again describe the whole set as read-only.
KNOWN_WRITES = {
    "mcp__cortex__cortex_finding_log",
    "mcp__cortex__cortex_decision_log",
    "mcp__cortex__cortex_unknown_log",
    "mcp__cortex__cortex_goal_create",
    "mcp__cortex__cortex_log_artifacts",
    "mcp__cortex__ingest_file",
    "mcp__cortex__ingest_batch",
    "mcp__cortex__cortex_bus_dispatch",
    "mcp__cortex__cortex_collab",
}


def test_the_set_really_does_contain_writes():
    """POSITIVE CONTROL, and the reason the old label was false. If this ever fails
    because the writes moved out, the honesty tests below would be guarding nothing."""
    present = KNOWN_WRITES & sg.NOETIC_MCP_CORTEX

    assert present, "no known writes in the set — the premise of this file has changed"
    assert len(present) >= 5, f"expected the epistemic-workflow writes, found {sorted(present)}"


def test_the_header_acknowledges_that_the_set_contains_writes():
    """THE regression, asserted POSITIVELY.

    The obvious form — grep the header for the false phrase and require its absence —
    fails on prose that *quotes the phrase in order to correct it*, which is exactly
    what the fixed header does. That is the self-referential-grep trap: an assertion
    about a string cannot tell a claim from a retraction of the same claim.

    So the property is what the header must SAY, not what it must avoid: an auditor
    reading it has to learn that mutating tools live here.
    """
    header = SOURCE.split("NOETIC_MCP_CORTEX = {")[0].rsplit("NOETIC_MCP_CHROME", 1)[-1].lower()

    assert any(word in header for word in ("mutate", "writes", "write")), (
        "the cortex set contains writes admitted by policy; its header must say so"
    )
    assert "decision" in header, "and must say membership is a decision, not an observation"


def test_the_praxic_mesh_tools_are_absent():
    """`cortex_propose` and `cortex_publish` are the ECO-gated praxic primitives. The
    tool name IS the noetic/praxic boundary in the mesh, so admitting either here
    would erase the boundary at the gate that enforces it."""
    for op in ("cortex_propose", "cortex_publish"):
        assert f"mcp__cortex__{op}" not in sg.NOETIC_MCP_CORTEX


def test_every_entry_carries_its_own_justification():
    """THE class, not the instance. A bare entry under a blanket label is how a praxic
    tool joins a noetic set without anyone deciding to admit it — which is exactly what
    the false header made easy. Each line must say what it is."""
    block = SOURCE.split("NOETIC_MCP_CORTEX = {", 1)[1].split("\n}", 1)[0]

    undocumented = [
        line.strip() for line in block.splitlines() if re.match(r'^\s*"mcp__cortex__', line) and "#" not in line
    ]

    assert not undocumented, f"entries added with no justification comment: {undocumented}"
