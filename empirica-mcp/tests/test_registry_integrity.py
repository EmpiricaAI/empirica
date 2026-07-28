"""Integrity tests for TOOL_REGISTRY.

The registry is hand-written and read by code that doesn't validate its
shape at import time. These tests catch typos and structural drift
(missing keys, dangling list_params, unknown positionals, etc.) so
broken entries fail fast at CI time instead of when a user invokes a
tool months later.
"""

from __future__ import annotations

import pytest
from empirica_mcp.server import (
    _ENUM_PARAMS,
    TOOL_REGISTRY,
    _build_tool_schema,
)

# Every entry MUST have these keys.
REQUIRED_KEYS = {"cli", "desc", "params", "required"}


@pytest.mark.parametrize("name", sorted(TOOL_REGISTRY.keys()))
def test_entry_has_required_keys(name):
    """Each registry entry has cli + desc + params + required."""
    entry = TOOL_REGISTRY[name]
    missing = REQUIRED_KEYS - entry.keys()
    assert not missing, f"{name} missing keys: {missing}"


@pytest.mark.parametrize("name", sorted(TOOL_REGISTRY.keys()))
def test_required_list_is_subset_of_params(name):
    """For flag-shaped entries, `required` names must also be in `params`
    (or be the positional). stdin_json entries are exempt — they pipe a
    free-form JSON payload whose required keys live in the entry's `desc`,
    not in any structural schema."""
    entry = TOOL_REGISTRY[name]
    if entry.get("stdin_json"):
        return  # required-vs-params doesn't apply to free-form JSON tools
    valid_names = set(entry.get("params", {}).keys())
    if entry.get("positional"):
        valid_names.add(entry["positional"])
    for req in entry.get("required", []):
        assert req in valid_names, f"{name}: required field '{req}' is neither in params nor positional"


@pytest.mark.parametrize("name", sorted(TOOL_REGISTRY.keys()))
def test_list_params_subset_of_params(name):
    """list_params entries must be present in params."""
    entry = TOOL_REGISTRY[name]
    params = set(entry.get("params", {}).keys())
    for lp in entry.get("list_params", []):
        assert lp in params, f"{name}: list_params contains '{lp}' but it's not in params"


@pytest.mark.parametrize("name", sorted(TOOL_REGISTRY.keys()))
def test_positional_not_also_in_params(name):
    """Positional and params shouldn't overlap — they're processed differently."""
    entry = TOOL_REGISTRY[name]
    pos = entry.get("positional")
    if pos:
        assert pos not in entry.get("params", {}), f"{name}: '{pos}' appears as BOTH positional and a flag — pick one"


@pytest.mark.parametrize("name", sorted(TOOL_REGISTRY.keys()))
def test_schema_builds_without_error(name):
    """_build_tool_schema must succeed for every registry entry."""
    tool = _build_tool_schema(name, TOOL_REGISTRY[name])
    assert tool.name == name
    assert _schema(tool)["type"] == "object"


def test_enum_param_keys_are_strings():
    """_ENUM_PARAMS maps param-name → list of strings (no nested types)."""
    for param, values in _ENUM_PARAMS.items():
        assert isinstance(param, str)
        assert isinstance(values, list)
        assert all(isinstance(v, str) for v in values), f"_ENUM_PARAMS['{param}'] contains non-string values"


def test_registry_has_workflow_phase_tools():
    """Spot-check: the three core workflow phase tools are present.

    If these disappear or get renamed, the rest of the empirica plugin
    surface (Claude Code hooks, system prompt examples) breaks silently."""
    expected = {
        "submit_preflight_assessment",
        "submit_check_assessment",
        "submit_postflight_assessment",
    }
    missing = expected - TOOL_REGISTRY.keys()
    assert not missing, f"Missing core workflow tools: {missing}"


def test_registry_size_floor():
    """Registry should have a meaningful number of tools (not empty, not 1).

    Catches accidental truncation. Loose lower bound — adjust upward as
    the surface grows. Failing on this means something stripped the
    registry."""
    assert len(TOOL_REGISTRY) >= 30, f"TOOL_REGISTRY has only {len(TOOL_REGISTRY)} entries — likely truncated"


def _schema(tool):
    """A Tool's input schema, on either SDK major.

    MCP 2.0 renamed protocol fields from camelCase to snake_case for ATTRIBUTE
    access (`Tool.inputSchema` -> `Tool.input_schema`) while the constructor still
    accepts both. Tests assert on attributes, so they need this; server code that
    only CONSTRUCTS Tools does not.
    """
    return getattr(tool, "input_schema", None) or tool.inputSchema
