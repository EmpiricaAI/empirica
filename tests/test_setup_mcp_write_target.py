"""`empirica setup` must register the MCP server where Claude Code reads it.

Claude Code keeps user-scope MCP servers in ``~/.claude.json``. Setup wrote only
``~/.claude/mcp.json``, so the registration it reported as configured had no
effect on this harness at all.

Proved on 2026-08-07 rather than inferred, with three escalating measurements:

1. ``claude mcp list`` resolved both diverging servers to their
   ``~/.claude.json`` values, not the ``~/.claude/mcp.json`` ones.
2. ``claude mcp get empirica`` reported "Scope: User config" with the
   ``~/.claude.json`` command *and* its env block.
3. A probe server written ONLY to ``~/.claude/mcp.json`` did not appear in
   ``claude mcp list`` at all. That is the negative: the legacy file is not
   read by Claude Code.

The legacy file is still written, with an identical entry — dropping it could
silently unregister the server for an unmeasured reader, and writing a
*different* value there is what produced the divergence `doctor` now warns
about. Identical in both is the only option that neither breaks a reader nor
manufactures a warning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from empirica.cli.command_handlers.setup_claude_code import _configure_mcp_server


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake HOME with a real empirica-mcp binary on PATH."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    binp = tmp_path / "bin"
    binp.mkdir()
    mcp = binp / "empirica-mcp"
    mcp.write_text("#!/bin/sh\n")
    mcp.chmod(0o755)
    monkeypatch.setenv("PATH", str(binp))
    # Priority 1 in the resolver is the CLI's own venv; keep it out of the way
    # so the PATH lookup is what resolves.
    monkeypatch.setattr("empirica.cli.command_handlers.setup_claude_code.sys.executable", str(binp / "python"))
    return h


def _servers(path: Path) -> dict:
    return json.loads(path.read_text())["mcpServers"]


def _run(home, force=False):
    return _configure_mcp_server(home / ".claude", home, force, "json")


# ─── The live config ───────────────────────────────────────────────────


def test_writes_the_config_claude_code_actually_loads(home):
    ok, cmd = _run(home)
    assert ok
    live = home / ".claude.json"
    assert live.exists(), "~/.claude.json was never written — Claude Code sees no empirica server"
    assert _servers(live)["empirica"]["command"] == cmd


def test_still_writes_the_legacy_config(home):
    """Dropping it could unregister the server for an unmeasured reader."""
    _run(home)
    assert (home / ".claude" / "mcp.json").exists()


def test_both_configs_carry_an_identical_entry(home):
    """A DIFFERING value across the two files is the divergence doctor warns
    about — setup must not be the thing that creates it."""
    _run(home)
    assert _servers(home / ".claude.json")["empirica"] == _servers(home / ".claude" / "mcp.json")["empirica"]


def test_existing_unrelated_servers_are_preserved(home):
    """~/.claude.json is Claude Code's own file and holds far more than ours."""
    live = home / ".claude.json"
    live.write_text(json.dumps({"mcpServers": {"other": {"command": "/bin/true"}}, "someOtherKey": 42}))
    _run(home)
    data = json.loads(live.read_text())
    assert "other" in data["mcpServers"], "clobbered a peer MCP server"
    assert data["someOtherKey"] == 42, "clobbered unrelated top-level Claude Code config"


# ─── Update semantics ──────────────────────────────────────────────────


def test_a_changed_binary_path_is_rewritten_in_both(home):
    live = home / ".claude.json"
    legacy = home / ".claude" / "mcp.json"
    stale = {"mcpServers": {"empirica": {"command": "/old/empirica-mcp"}}}
    live.write_text(json.dumps(stale))
    legacy.write_text(json.dumps(stale))
    _, cmd = _run(home)
    assert _servers(live)["empirica"]["command"] == cmd
    assert _servers(legacy)["empirica"]["command"] == cmd


def test_an_env_block_setup_never_wrote_is_preserved(home):
    """Setup owns the keys it writes and nothing else.

    The live config on a working box carries an `env` block
    (EMPIRICA_EPISTEMIC_MODE, EMPIRICA_PERSONALITY) that setup has never
    written. An earlier draft of this change replaced the entry wholesale,
    which would have deleted it on the next `empirica setup` — caught by
    reading the real config before running, not by a test.

    Setup cannot distinguish a broken env from a deliberate one, so it must not
    adjudicate. `doctor` tests whether the entry can launch and names the
    failure; that is where the judgement belongs.
    """
    _run(home)
    live = home / ".claude.json"
    data = json.loads(live.read_text())
    data["mcpServers"]["empirica"]["env"] = {"EMPIRICA_PERSONALITY": "balanced_architect"}
    live.write_text(json.dumps(data))

    _run(home)
    assert _servers(live)["empirica"]["env"] == {"EMPIRICA_PERSONALITY": "balanced_architect"}


def test_a_stale_command_is_updated_without_touching_env(home):
    """The two halves together: fix what setup owns, preserve what it does not."""
    live = home / ".claude.json"
    live.write_text(json.dumps({"mcpServers": {"empirica": {"command": "/old/empirica-mcp", "env": {"KEEP": "me"}}}}))
    _, cmd = _run(home)
    entry = _servers(live)["empirica"]
    assert entry["command"] == cmd
    assert entry["env"] == {"KEEP": "me"}


def test_a_correct_entry_is_left_alone(home):
    """Idempotent: no rewrite when both files already match."""
    _run(home)
    live = home / ".claude.json"
    before = live.read_text()
    _run(home)
    assert live.read_text() == before


def test_force_rewrites_even_when_current(home):
    _run(home)
    live = home / ".claude.json"
    data = json.loads(live.read_text())
    data["mcpServers"]["empirica"]["description"] = "hand-edited"
    live.write_text(json.dumps(data))
    _run(home, force=True)
    assert _servers(live)["empirica"]["description"] != "hand-edited"
