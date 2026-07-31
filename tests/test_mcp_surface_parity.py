"""The MCP surface must expose what the CLI can do, and say so.

Two failure modes, both live before this file existed:

**Capability.** `update_artifacts` was absent from TOOL_REGISTRY while its three
siblings — log / resolve / delete — were all present. An MCP-only practitioner
(Desktop, codex, ecodex) could create artifacts and resolve them but could not
correct one. That is the same narrow-correction-surface shape found repeatedly in
this codebase: the write path ships, the fix path does not.

**Discoverability.** `claims` and `resolution_kind` both flow through `stdin_json`
untouched, so they always *worked* over MCP — and the strings appeared zero times in
the entire server, so nobody could learn they existed. A mechanism reachable only by
someone who already knows it is there is not reachable.

These tests read TOOL_REGISTRY rather than exercising the dispatcher, because the
gap is a registry-coverage question: the server shells the CLI, so anything listed
works and anything unlisted is simply invisible.
"""

from __future__ import annotations

import empirica_mcp.server as server
import pytest

REGISTRY = server.TOOL_REGISTRY

# Verbs whose absence would leave a surface able to create but not correct.
BATCH_GRAPH_VERBS = ["log_artifacts", "resolve_artifacts", "delete_artifacts", "update_artifacts"]


@pytest.mark.parametrize("tool", BATCH_GRAPH_VERBS)
def test_every_batch_graph_verb_is_exposed(tool):
    """POSITIVE CONTROL — update_artifacts is the one that was missing."""
    assert tool in REGISTRY, f"{tool} is absent from the MCP surface; MCP-only practitioners cannot reach it"


@pytest.mark.parametrize("tool", BATCH_GRAPH_VERBS)
def test_each_batch_verb_maps_to_a_real_cli_verb(tool):
    """NEGATIVE CONTROL — presence in the registry is worthless if the `cli` string
    does not name a verb the CLI dispatches. A registry entry pointing at a
    misspelled verb would pass the test above and fail at every call."""
    from pathlib import Path

    from empirica.cli import cli_core

    cli_verb = REGISTRY[tool]["cli"]
    source = Path(cli_core.__file__).read_text(encoding="utf-8")

    assert f'"{cli_verb}"' in source, f"{tool} maps to `{cli_verb}`, which cli_core does not dispatch"


def test_the_claims_mechanism_is_discoverable_from_the_workflow_tools():
    """Claims ride stdin_json and always worked. The gap was that nothing said so."""
    for tool in ("submit_preflight_assessment", "submit_check_assessment", "submit_postflight_assessment"):
        assert "claims" in REGISTRY[tool]["desc"], f"{tool} does not mention the claims payload"


def test_preflight_explains_that_grounded_claims_certify():
    """The load-bearing half: read/ran certify and skip CHECK, retrieved/assumed do
    not. A description that mentions `claims` without that asymmetry teaches the
    feature as a logging nicety rather than a gate path."""
    desc = REGISTRY["submit_preflight_assessment"]["desc"]

    assert "read" in desc and "ran" in desc
    assert "retrieved" in desc and "assumed" in desc


def test_resolution_kind_is_discoverable_where_it_is_used():
    desc = REGISTRY["resolve_artifacts"]["desc"]

    assert "resolution_kind" in desc
    for kind in ("stale", "superseded", "retracted", "mistyped"):
        assert kind in desc, f"{kind} missing from the resolve_artifacts vocabulary"


def test_update_artifacts_states_that_bodies_are_not_correctable():
    """The constraint is the point. Someone reading this tool should learn that the
    correction surface is metadata-only BEFORE trying to rewrite a finding's text and
    concluding the tool is broken."""
    desc = REGISTRY["update_artifacts"]["desc"]

    assert "never the artifact body" in desc
