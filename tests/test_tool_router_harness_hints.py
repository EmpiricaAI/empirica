"""tool-router's hints name what exists in the harness hosting them.

ecodex leaves the Empirica MCP server off by default (prop_qr2m4fleanh65cjjj634zqsvom), so a hint saying
"use `mcp__empirica__investigate`" points at a tool that is not there. Under EMPIRICA_HARNESS=codex
the hints name the CLI verbs; Claude Code's text is unchanged.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"


@pytest.fixture
def router(monkeypatch):
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("tool_router_harness_hints", HOOKS / "tool-router.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    # Force every branch to fire: this test is about the wording, not about when each hint applies.
    monkeypatch.setattr(mod, "is_investigation_task", lambda _t: True)
    monkeypatch.setattr(mod, "is_blindspot_relevant", lambda *_a: True)
    monkeypatch.setattr(mod, "is_epistemic_task", lambda _t: True)
    return mod


def _all_hints(router) -> str:
    vec = {"know": 0.5}
    lines = [
        *router._investigation_routing_advice("explore", False),
        *router._blindspot_advice("explore", "investigate", vec),
        *router._mode_based_advice("load_context", vec, "", False),
        *router._mode_based_advice("investigate", vec, "", False),
        *router._mode_based_advice("cautious_implementation", vec, "try a workaround", False),
        *router._epistemic_workflow_advice("preflight"),
    ]
    assert len(lines) == 6, "every hint branch must have fired, or the assertions below prove nothing"
    return "\n".join(lines)


def test_claude_code_hints_are_unchanged(router, monkeypatch):
    monkeypatch.delenv("EMPIRICA_HARNESS", raising=False)

    text = _all_hints(router)

    for tool in ("investigate", "blindspot_scan", "project_bootstrap", "deadend_log"):
        assert f"`mcp__empirica__{tool}`" in text
    assert "Empirica MCP tools (preflight/check/postflight)" in text


def test_codex_hints_name_cli_verbs_and_no_mcp_tool(router, monkeypatch):
    monkeypatch.setenv("EMPIRICA_HARNESS", "codex")

    text = _all_hints(router)

    assert "mcp__empirica__" not in text and "Empirica MCP tools" not in text
    for verb in (
        "empirica investigate",
        "empirica blindspot-scan",
        "empirica project-bootstrap",
        "empirica deadend-log",
    ):
        assert f"`{verb}`" in text
    assert "preflight-submit/check-submit/postflight-submit" in text


def test_every_cli_verb_a_hint_names_exists_in_the_parser():
    """A hint that names a verb the CLI lacks is the defect this change removes, reintroduced."""
    from empirica.cli.cli_core import create_argument_parser

    sub = next(a for a in create_argument_parser()._actions if getattr(a, "choices", None))
    verbs = set(sub.choices)

    for verb in ("investigate", "blindspot-scan", "project-bootstrap", "deadend-log"):
        assert verb in verbs
