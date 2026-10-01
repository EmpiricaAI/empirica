"""doctor finds a plugin whose default agent takes tools away from the main session.

2026-09-29: a plugin synced from a claude.ai account (built for Cowork) shipped
`settings.json` {"agent": "empirica-founder"}. Claude Code applied that agent to the
MAIN thread of every session it loaded in, with its `model: sonnet` and a `tools:`
allowlist of Read, Grep, Glob, Bash, WebSearch, WebFetch, Agent. Restarted sessions
had no Monitor, Edit, Write or ToolSearch, and nothing said why.
"""

from __future__ import annotations

import json
from pathlib import Path

from empirica.cli.command_handlers.doctor import PASS, SKIP, WARN, check_plugin_main_thread_agent

FOUNDER = "---\nname: empirica-founder\nmodel: sonnet\ntools:\n  - Read\n  - Grep\n  - Glob\n  - Bash\n  - WebSearch\n  - WebFetch\n  - Agent\n---\nbody\n"
FULL = "---\nname: full\ntools: Read, Edit, Write, Bash, Monitor, ToolSearch\n---\nbody\n"


def _plugin(root: Path, name: str, agent_md: str | None, agent: str | None = None) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name}))
    if agent_md is not None:
        (root / "agents").mkdir()
        (root / "agents" / f"{agent}.md").write_text(agent_md)
    if agent:
        (root / "settings.json").write_text(json.dumps({"agent": agent}))
    return root


def _home(tmp_path: Path, enabled: dict | None = None) -> Path:
    c = tmp_path / ".claude"
    (c / "plugins").mkdir(parents=True)
    (c / "settings.json").write_text(json.dumps({"enabledPlugins": enabled or {}}))
    return tmp_path


def _synced(home: Path, name: str, **kw) -> Path:
    return _plugin(home / ".claude" / "plugins" / "synced" / "org_user" / name, name, **kw)


def test_the_real_shape_is_flagged_with_the_tools_it_removes(tmp_path):
    """POSITIVE CONTROL: the empirica-admin layout, synced, no enabledPlugins entry."""
    home = _home(tmp_path)
    _synced(home, "empirica-admin", agent_md=FOUNDER, agent="empirica-founder")

    c = check_plugin_main_thread_agent(home)

    assert c.status == WARN
    assert c.data["plugins"][0]["lost"] == ["Edit", "Write", "Monitor", "ToolSearch"]
    assert "empirica-admin@synced" in c.detail and "model sonnet" in c.detail
    assert "1 enabled plugin(s) read, 1 set a default agent" in c.detail
    assert "claude plugin disable" in c.hint


def test_disabling_it_clears_the_warning(tmp_path):
    home = _home(tmp_path, {"empirica-admin@synced": False})
    _synced(home, "empirica-admin", agent_md=FOUNDER, agent="empirica-founder")

    c = check_plugin_main_thread_agent(home)

    assert c.status == PASS and "0 enabled plugin(s) read" in c.detail, (
        "a disabled plugin is not read, and that is said"
    )


def test_a_default_agent_that_keeps_the_tools_is_fine(tmp_path):
    home = _home(tmp_path)
    _synced(home, "ok", agent_md=FULL, agent="full")

    c = check_plugin_main_thread_agent(home)

    assert c.status == PASS and "1 set a default agent" in c.detail


def test_a_plugin_without_a_default_agent_is_fine(tmp_path):
    home = _home(tmp_path)
    _synced(home, "plain", agent_md=None)

    assert check_plugin_main_thread_agent(home).status == PASS


def test_an_installed_plugin_counts_only_when_enabled(tmp_path):
    home = _home(tmp_path, {"mine@local": True, "off@local": False})
    mine = _plugin(tmp_path / "mine", "mine", agent_md=FOUNDER, agent="empirica-founder")
    off = _plugin(tmp_path / "off", "off", agent_md=FOUNDER, agent="empirica-founder")
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "mine@local": [{"scope": "user", "installPath": str(mine)}],
                    "off@local": [{"scope": "user", "installPath": str(off)}],
                }
            }
        )
    )

    c = check_plugin_main_thread_agent(home)

    assert c.status == WARN
    assert [b["plugin"] for b in c.data["plugins"]] == ["mine@local"]


def test_a_default_agent_whose_file_is_missing_is_reported_not_passed(tmp_path):
    """Unknowable is not clean: the allowlist could not be read."""
    home = _home(tmp_path)
    p = _synced(home, "ghost", agent_md=None)
    (p / "settings.json").write_text(json.dumps({"agent": "nowhere"}))

    c = check_plugin_main_thread_agent(home)

    assert c.status == WARN and "not found" in c.detail


def test_no_plugins_directory_is_a_skip(tmp_path):
    assert check_plugin_main_thread_agent(tmp_path).status == SKIP


# ── broccoli: blind spots in the check ──────────────────────────────────────

DENYING = "---\nname: denier\ntools: Read, Edit, Write, Bash, Monitor, ToolSearch\ndisallowedTools: Monitor, Edit\n---\nbody\n"
INHERITING_DENIER = "---\nname: inheritor\ndisallowedTools:\n  - ToolSearch\n---\nbody\n"


def test_disallowed_tools_remove_tools_even_when_the_allowlist_is_full(tmp_path):
    """Only `tools:` was read, so an agent that denies Monitor passed clean."""
    home = _home(tmp_path)
    _synced(home, "denier", agent_md=DENYING, agent="denier")

    c = check_plugin_main_thread_agent(home)

    assert c.status == WARN and c.data["plugins"][0]["lost"] == ["Edit", "Monitor"]


def test_disallowed_tools_without_any_allowlist_still_count(tmp_path):
    home = _home(tmp_path)
    _synced(home, "inheritor", agent_md=INHERITING_DENIER, agent="inheritor")

    assert check_plugin_main_thread_agent(home).data["plugins"][0]["lost"] == ["ToolSearch"]


def _installed(home: Path, key: str, install_path: Path) -> None:
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {key: [{"scope": "project", "installPath": str(install_path)}]}})
    )


def test_a_plugin_enabled_only_in_the_project_is_seen_when_the_project_is_given(tmp_path):
    """enabledPlugins was read from the user settings only."""
    home = _home(tmp_path)
    root = _plugin(tmp_path / "p", "p", agent_md=FOUNDER, agent="empirica-founder")
    _installed(home, "p@local", root)
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.local.json").write_text(json.dumps({"enabledPlugins": {"p@local": True}}))

    assert check_plugin_main_thread_agent(home).status == PASS  # control: user settings alone do not enable it
    c = check_plugin_main_thread_agent(home, project)

    assert c.status == WARN and [b["plugin"] for b in c.data["plugins"]] == ["p@local"]


def test_an_enabled_plugin_whose_files_are_gone_is_counted_as_not_read(tmp_path):
    """A missing installPath used to be counted among the plugins read."""
    home = _home(tmp_path, {"gone@local": True})
    _installed(home, "gone@local", tmp_path / "no-such-dir")

    c = check_plugin_main_thread_agent(home)

    assert "0 enabled plugin(s) read" in c.detail and "1 enabled but not on disk" in c.detail
