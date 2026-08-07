"""doctor must inspect the MCP config Claude Code LOADS, and test that it works.

Reported by empirica.philipp.empirica-mesh-support, reproduced here.

Two independent defects composed into an invisible one:

1. ``_find_mcp_config_paths()`` omitted ``~/.claude.json`` — where Claude Code
   stores user-scope MCP servers. doctor inspected ``~/.claude/mcp.json``.
2. ``check_mcp_config()`` tested only whether an entry existed BY NAME, never
   whether it could launch.

On the reporting box both files existed with an ``empirica`` entry. The one
doctor read was clean, so doctor reported PASS, while the one Claude Code
loaded pinned an ``env.PATH`` that could not resolve the CLI — every
``mcp__empirica__*`` call had been failing for weeks. Either defect alone is
survivable; together, doctor was structurally incapable of seeing the fault.
"""

from __future__ import annotations

import json
from pathlib import Path

from empirica.cli.command_handlers.doctor import (
    PASS,
    WARN,
    _find_mcp_config_paths,
    _mcp_entry_command_resolves,
    check_mcp_config,
)

# ─── The path list ─────────────────────────────────────────────────────


def test_live_claude_code_config_is_searched():
    """``~/.claude.json`` is the user-scope store; omitting it was the blind spot."""
    paths = _find_mcp_config_paths()
    assert Path.home() / ".claude.json" in paths


def test_live_config_is_searched_first():
    """It is the file that actually runs, so it should lead the list."""
    assert _find_mcp_config_paths()[0] == Path.home() / ".claude.json"


def test_legacy_path_is_still_searched():
    """Adding the live store must not stop surfacing the other one — the
    divergence between them is itself the signal."""
    assert Path.home() / ".claude" / "mcp.json" in _find_mcp_config_paths()


# ─── Functional resolution ─────────────────────────────────────────────


def test_entry_with_unresolvable_pinned_path_is_not_ok(tmp_path):
    entry = {"command": "empirica-mcp", "env": {"PATH": str(tmp_path)}}
    resolves, detail = _mcp_entry_command_resolves(entry)
    assert resolves is False
    assert "env.PATH" in (detail or "")


def test_entry_with_resolvable_pinned_path_is_ok(tmp_path):
    binary = tmp_path / "empirica-mcp"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    entry = {"command": "empirica-mcp", "env": {"PATH": str(tmp_path)}}
    assert _mcp_entry_command_resolves(entry)[0] is True


def test_entry_resolution_uses_the_pinned_path_not_ours(tmp_path, monkeypatch):
    """The whole point: resolving against doctor's PATH answers another question.

    The binary exists on the ambient PATH and NOT on the entry's pinned PATH —
    exactly the reported shape, where the CLI was installed but absent from the
    four directories the entry listed.
    """
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    binary = ambient / "empirica-mcp"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(ambient))

    pinned = tmp_path / "pinned"
    pinned.mkdir()
    entry = {"command": "empirica-mcp", "env": {"PATH": str(pinned)}}
    assert _mcp_entry_command_resolves(entry)[0] is False


def test_absolute_command_that_does_not_exist_is_not_ok(tmp_path):
    entry = {"command": str(tmp_path / "nope" / "empirica-mcp")}
    resolves, detail = _mcp_entry_command_resolves(entry)
    assert resolves is False
    assert "does not exist" in (detail or "")


def test_entry_without_pinned_path_is_not_judged():
    """No env.PATH means the entry inherits the client's — not ours to fail."""
    assert _mcp_entry_command_resolves({"command": "empirica-mcp"})[0] is True


# ─── End to end through check_mcp_config ───────────────────────────────


def _write(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_broken_live_entry_warns_even_when_the_other_config_is_clean(tmp_path, monkeypatch):
    """The reported failure, reproduced: a clean file must not mask a broken one."""
    live = tmp_path / ".claude.json"
    legacy = tmp_path / ".claude" / "mcp.json"
    good = tmp_path / "bin"
    good.mkdir()
    (good / "empirica-mcp").write_text("#!/bin/sh\n")
    (good / "empirica-mcp").chmod(0o755)

    _write(live, {"empirica": {"command": "empirica-mcp", "env": {"PATH": str(tmp_path / "nowhere")}}})
    _write(legacy, {"empirica": {"command": "empirica-mcp", "env": {"PATH": str(good)}}})

    monkeypatch.setattr(
        "empirica.cli.command_handlers.doctor._find_mcp_config_paths",
        lambda: [live, legacy],
    )
    result = check_mcp_config()
    assert result.status == WARN, "a clean legacy config masked a broken live one"
    assert result.data["broken"], "the broken entry was not surfaced"


def test_divergent_definitions_warn(tmp_path, monkeypatch):
    """Same server, two configs, two commands — you cannot tell which one runs."""
    live = tmp_path / ".claude.json"
    legacy = tmp_path / ".claude" / "mcp.json"
    _write(live, {"empirica": {"command": "/opt/a/empirica-mcp"}})
    _write(legacy, {"empirica": {"command": "/opt/b/empirica-mcp"}})

    monkeypatch.setattr(
        "empirica.cli.command_handlers.doctor._find_mcp_config_paths",
        lambda: [live, legacy],
    )
    result = check_mcp_config()
    assert result.status == WARN
    assert "empirica" in result.data["diverged"]


def test_consistent_working_config_still_passes(tmp_path, monkeypatch):
    """The guard must not turn every healthy box yellow."""
    live = tmp_path / ".claude.json"
    good = tmp_path / "bin"
    good.mkdir()
    (good / "empirica-mcp").write_text("#!/bin/sh\n")
    (good / "empirica-mcp").chmod(0o755)
    _write(live, {"empirica": {"command": "empirica-mcp", "env": {"PATH": str(good)}}})

    monkeypatch.setattr(
        "empirica.cli.command_handlers.doctor._find_mcp_config_paths",
        lambda: [live],
    )
    result = check_mcp_config()
    assert result.status == PASS
    assert result.data["broken"] == []
    assert result.data["diverged"] == []
