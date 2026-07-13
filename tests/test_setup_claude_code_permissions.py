"""setup-claude-code pre-authorizes the mesh MCP servers in settings.json.

Without a `permissions.allow` entry for the cortex + empirica MCP servers, a
mesh-active practice hits Claude Code's per-tool "Do you want to proceed?"
prompt on every cortex_*/empirica_* call — the friction David hit in
empirica-web where cortex_collab + cortex_propose both prompted. The real
gates (Sentinel for empirica praxic, cortex ECO for propose) stay intact, so
the CC prompt is redundant for these two trusted servers.

These tests assert the allow-list write is present, idempotent, and
non-destructive to existing user config.
"""

from __future__ import annotations

from empirica.cli.command_handlers.setup_claude_code import (
    MESH_MCP_ALLOW,
    _configure_permissions,
)


def test_adds_mesh_servers_to_empty_settings():
    settings: dict = {}
    _configure_permissions(settings, "json")
    allow = settings["permissions"]["allow"]
    assert "mcp__cortex__*" in allow
    assert "mcp__empirica__*" in allow
    assert set(MESH_MCP_ALLOW) <= set(allow)


def test_idempotent_no_duplicates():
    settings: dict = {}
    _configure_permissions(settings, "json")
    _configure_permissions(settings, "json")  # second run must not duplicate
    allow = settings["permissions"]["allow"]
    for entry in MESH_MCP_ALLOW:
        assert allow.count(entry) == 1


def test_preserves_existing_allow_entries():
    # A user who accumulated entries via 'Yes and don't ask again' must keep them.
    settings = {"permissions": {"allow": ["Bash(git status:*)", "mcp__cortex__*"]}}
    _configure_permissions(settings, "json")
    allow = settings["permissions"]["allow"]
    assert "Bash(git status:*)" in allow  # untouched
    assert allow.count("mcp__cortex__*") == 1  # not re-added
    assert "mcp__empirica__*" in allow  # the missing one added


def test_leaves_deny_and_ask_untouched():
    settings = {"permissions": {"allow": [], "deny": ["Bash(rm:*)"], "ask": ["WebFetch"]}}
    _configure_permissions(settings, "json")
    assert settings["permissions"]["deny"] == ["Bash(rm:*)"]
    assert settings["permissions"]["ask"] == ["WebFetch"]


def test_malformed_permissions_not_clobbered():
    # If a user somehow has a non-dict permissions value, don't crash or clobber.
    settings = {"permissions": "not-a-dict"}
    _configure_permissions(settings, "json")
    assert settings["permissions"] == "not-a-dict"


def test_malformed_allow_not_clobbered():
    settings = {"permissions": {"allow": "not-a-list"}}
    _configure_permissions(settings, "json")
    assert settings["permissions"]["allow"] == "not-a-list"
