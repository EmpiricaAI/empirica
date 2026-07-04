"""_build_weave_guidance — schema-injection at the CHECK gate.

Gated Artifact-Graph map, work-stream 2 (goal 43471346). The CHECK→proceed
response injects the log-artifacts node/relation vocabulary so weaving is cheap
(no guessing the shape / recurring "unknown relation" errors). The relation set
must stay in lockstep with graph_commands' canonical VALID_RELATIONS.
"""

from __future__ import annotations

from empirica.cli.command_handlers._workflow_shared import _build_weave_guidance


def test_weave_guidance_carries_the_schema():
    g = _build_weave_guidance()
    assert "finding" in g["node_types"]
    assert "decision" in g["node_types"]
    assert "evidence" in g["relations"]
    assert "grounded_by" in g["relations"]
    assert "nodes" in g["shape"]
    assert "edges" in g["shape"]
    assert g["node_required_fields"]["decision"] == ["choice", "rationale"]


def test_relations_stay_in_lockstep_with_graph_commands():
    from empirica.cli.command_handlers.graph_commands import VALID_RELATIONS

    g = _build_weave_guidance()
    assert set(g["relations"]) == set(VALID_RELATIONS)


def test_node_types_stay_in_lockstep_with_graph_commands():
    from empirica.cli.command_handlers.graph_commands import NODE_REQUIRED_FIELDS

    g = _build_weave_guidance()
    assert set(g["node_types"]) == set(NODE_REQUIRED_FIELDS.keys())
