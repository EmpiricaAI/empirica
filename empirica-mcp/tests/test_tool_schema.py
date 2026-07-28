"""Tests for empirica-mcp's MCP Tool schema builder.

`_build_tool_schema()` translates each TOOL_REGISTRY entry into the
JSON-schema shape MCP clients use to autocomplete tool calls. The
mapping logic depends on three module-level sets (_NUMERIC_PARAMS,
_BOOLEAN_PARAMS, _ENUM_PARAMS) and a few special-case branches for
stdin_json + submit_* tools.
"""

from __future__ import annotations

import mcp.types as types
from empirica_mcp.server import _build_tool_schema


def _props(entry: dict, name: str = "test") -> dict:
    """Helper: extract just the properties block from a built schema."""
    tool = _build_tool_schema(name, entry)
    return _schema(tool)["properties"]


# ─── Basic shape ────────────────────────────────────────────────────────


def test_returns_mcp_tool():
    """Result is a real mcp.types.Tool object."""
    entry = {"cli": "x", "desc": "test tool", "params": {}, "required": []}
    tool = _build_tool_schema("x", entry)
    assert isinstance(tool, types.Tool)
    assert tool.name == "x"
    assert tool.description == "test tool"
    assert _schema(tool)["type"] == "object"


def test_required_passes_through():
    """The `required` list in the entry maps to JSON schema `required`."""
    entry = {"cli": "x", "desc": "", "params": {"a": "--a", "b": "--b"}, "required": ["a"]}
    tool = _build_tool_schema("x", entry)
    assert _schema(tool)["required"] == ["a"]


# ─── Param type classification ──────────────────────────────────────────


def test_numeric_param_gets_number_type():
    """Params in _NUMERIC_PARAMS classify as JSON number."""
    entry = {"cli": "x", "desc": "", "params": {"impact": "--impact"}, "required": []}
    props = _props(entry)
    assert props["impact"]["type"] == "number"


def test_boolean_param_gets_boolean_type():
    """Params in _BOOLEAN_PARAMS classify as JSON boolean."""
    entry = {"cli": "x", "desc": "", "params": {"grounded": "--grounded"}, "required": []}
    props = _props(entry)
    assert props["grounded"]["type"] == "boolean"


def test_enum_param_emits_enum_list():
    """Params in _ENUM_PARAMS get an `enum` array of valid values."""
    entry = {"cli": "x", "desc": "", "params": {"severity": "--severity"}, "required": []}
    props = _props(entry)
    assert props["severity"]["type"] == "string"
    assert set(props["severity"]["enum"]) == {"low", "medium", "high", "critical"}


def test_unrecognized_param_defaults_to_string():
    """Params not in any known set default to JSON string."""
    entry = {"cli": "x", "desc": "", "params": {"random_thing": "--random"}, "required": []}
    props = _props(entry)
    assert props["random_thing"]["type"] == "string"


# ─── List params ────────────────────────────────────────────────────────


def test_list_params_produce_array_schema():
    """Params in list_params become arrays of strings."""
    entry = {
        "cli": "x",
        "desc": "",
        "params": {"source": "--source"},
        "list_params": ["source"],
        "required": [],
    }
    props = _props(entry)
    assert props["source"]["type"] == "array"
    assert props["source"]["items"] == {"type": "string"}


def test_list_param_wins_over_numeric_classification():
    """If a param is BOTH in list_params and would otherwise be numeric,
    the array shape wins."""
    entry = {
        "cli": "x",
        "desc": "",
        "params": {"limit": "--limit"},  # `limit` is in _NUMERIC_PARAMS
        "list_params": ["limit"],
        "required": [],
    }
    props = _props(entry)
    assert props["limit"]["type"] == "array"


# ─── Positional arg ─────────────────────────────────────────────────────


def test_positional_is_a_string_property():
    """An entry with `positional` adds that name as a string property."""
    entry = {
        "cli": "investigate",
        "desc": "",
        "positional": "query",
        "params": {},
        "required": ["query"],
    }
    props = _props(entry, name="investigate")
    assert "query" in props
    assert props["query"]["type"] == "string"
    assert "positional" in props["query"]["description"]


# ─── stdin_json + submit_* special cases ────────────────────────────────


def test_stdin_json_submit_adds_session_id_and_vectors():
    """submit_* tools that use stdin_json get session_id + vectors + reasoning."""
    entry = {"cli": "submit-preflight", "desc": "", "stdin_json": True, "params": {}, "required": []}
    props = _props(entry, name="submit_preflight_assessment")
    assert "session_id" in props
    assert props["session_id"]["type"] == "string"
    assert "vectors" in props
    assert props["vectors"]["type"] == "object"
    assert "reasoning" in props


def test_submit_preflight_adds_task_context_and_work_type():
    """submit_preflight_assessment gets the PREFLIGHT-specific fields."""
    entry = {"cli": "submit-preflight", "desc": "", "stdin_json": True, "params": {}, "required": []}
    props = _props(entry, name="submit_preflight_assessment")
    assert "task_context" in props
    assert "work_type" in props
    assert "work_context" in props


def test_submit_check_adds_decision_enum():
    """submit_check_assessment gets the decision enum (proceed|investigate)."""
    entry = {"cli": "submit-check", "desc": "", "stdin_json": True, "params": {}, "required": []}
    props = _props(entry, name="submit_check_assessment")
    assert "decision" in props
    assert set(props["decision"]["enum"]) == {"proceed", "investigate"}


def test_non_submit_stdin_json_skips_session_extras():
    """stdin_json tools NOT named submit_* don't get the session_id/vectors block."""
    entry = {"cli": "log-graph", "desc": "", "stdin_json": True, "params": {}, "required": []}
    props = _props(entry, name="log_artifact_graph")
    assert "session_id" not in props
    assert "vectors" not in props


def _schema(tool):
    """A Tool's input schema, on either SDK major.

    MCP 2.0 renamed protocol fields from camelCase to snake_case for ATTRIBUTE
    access (`Tool.inputSchema` -> `Tool.input_schema`) while the constructor still
    accepts both. Tests assert on attributes, so they need this; server code that
    only CONSTRUCTS Tools does not.
    """
    return getattr(tool, "input_schema", None) or tool.inputSchema
