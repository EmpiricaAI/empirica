"""Uninstall removes OURS, edits THEIRS surgically, and never touches the user's own file.

Setup writes to eleven locations, six inside files Claude Code owns. A delete has
no merge to soften it — install could fold its entry into whatever it found;
uninstall removes — so every one of these tests is really about restraint rather
than removal.

Three categories, and collapsing them is how an uninstaller eats someone's config:

    remove_ours    the plugin dir, our prompt file       -> delete outright
    edit_shared    settings.json, ~/.claude.json, …      -> strip OUR keys only
    report_only    the @include line in THEIR CLAUDE.md  -> never touched
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.command_handlers.claude_code_uninstall import (
    INCLUDE_LINE,
    apply_uninstall,
    plan_uninstall,
)
from empirica.cli.command_handlers.setup_claude_code import _read_json_with_stamp


@pytest.fixture
def home(tmp_path):
    c = tmp_path / ".claude"
    (c / "plugins" / "local" / "empirica").mkdir(parents=True)
    (c / "plugins" / "local" / "empirica" / "hooks.py").write_text("x")
    (c / "empirica-system-prompt.md").write_text("prompt")
    (c / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"empirica@local": True, "someone-else@local": True},
                "hooks": {"UserPromptSubmit": [{"hooks": [{"command": "empirica hook"}]}]},
                "statusLine": {"command": "empirica statusline"},
                "permissions": {"allow": ["Bash(ls)"]},
                "theirOwnSetting": 42,
            }
        )
    )
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"empirica": {"command": "empirica-mcp"}, "other": {"command": "x"}},
                "projects": {"a": {"history": [1, 2, 3]}},
                "costs": {"total": 9.5},
            }
        )
    )
    (c / "CLAUDE.md").write_text(f"# My notes\n\nsome context\n{INCLUDE_LINE}\n\nmore of my own text\n")
    return tmp_path


def test_plan_separates_ours_from_theirs_from_the_users_own(home):
    p = plan_uninstall(home)

    assert any("plugins/local/empirica" in x for x in p["remove_ours"])
    assert any("empirica-system-prompt.md" in x for x in p["remove_ours"])

    edited = {e["path"]: e["remove_keys"] for e in p["edit_shared"]}
    settings = next(v for k, v in edited.items() if k.endswith("settings.json"))
    assert "enabledPlugins.empirica@local" in settings
    assert "statusLine" in settings
    assert "hooks.UserPromptSubmit" in settings

    assert p["report_only"], "the user's CLAUDE.md must be reported"
    assert p["report_only"][0]["lines"] == [4]


def test_apply_strips_ONLY_our_keys_from_a_shared_file(home):
    apply_uninstall(home)

    settings = json.loads((home / ".claude" / "settings.json").read_text())
    assert "empirica@local" not in settings.get("enabledPlugins", {})
    assert settings["enabledPlugins"]["someone-else@local"] is True, "another plugin must survive"
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}, "permissions are not ours to remove"
    assert settings["theirOwnSetting"] == 42
    assert "statusLine" not in settings

    live = json.loads((home / ".claude.json").read_text())
    assert "empirica" not in live["mcpServers"]
    assert live["mcpServers"]["other"] == {"command": "x"}, "another MCP server must survive"
    assert live["projects"] == {"a": {"history": [1, 2, 3]}}, "Claude Code's state must survive"
    assert live["costs"] == {"total": 9.5}


def test_the_users_own_CLAUDE_md_is_NEVER_modified(home):
    """The one edit we cannot safely reverse. If they have reorganised around
    that line, pattern-matching 'ours' and cutting it damages what they wrote."""
    before = (home / ".claude" / "CLAUDE.md").read_text()

    receipt = apply_uninstall(home)

    assert (home / ".claude" / "CLAUDE.md").read_text() == before, "we appended a line; we do not take it back"
    assert receipt["left_for_you"], "and it must be reported, not silently skipped"
    assert INCLUDE_LINE in receipt["left_for_you"][0]["content"]


def test_our_own_files_ARE_removed(home):
    """Positive control for the restraint tests above: an uninstall that removed
    nothing would pass every 'must survive' assertion."""
    apply_uninstall(home)
    assert not (home / ".claude" / "plugins" / "local" / "empirica").exists()
    assert not (home / ".claude" / "empirica-system-prompt.md").exists()


def test_a_shared_file_changed_under_us_is_REFUSED_not_clobbered(home, monkeypatch):
    """A delete racing Claude Code's write has no recoverable half."""
    from empirica.cli.command_handlers import claude_code_uninstall as mod

    real = _read_json_with_stamp

    def racy(path, default):
        data, _stamp = real(path, default)
        if path.name == ".claude.json":
            # Something writes between our read and our write.
            path.write_text(json.dumps({"projects": {"a": 1, "b": 2}, "mcpServers": {"empirica": {}}}))
            return data, (0, 0)  # a stamp that can no longer match
        return data, _stamp

    monkeypatch.setattr(mod, "_read_json_with_stamp", racy)
    receipt = apply_uninstall(home)

    assert any(".claude.json" in r for r in receipt["refused"])
    survived = json.loads((home / ".claude.json").read_text())
    assert survived["projects"] == {"a": 1, "b": 2}, "the racing write must survive our delete"


def test_an_UNREADABLE_shared_file_is_refused_not_rewritten(home):
    """Exists-but-unparseable while DELETING is the worst case: we would write a
    file whose contents we never read, minus keys we never saw."""
    (home / ".claude.json").write_text('{"projects": {"a": 1}, "cost')

    the_plan = plan_uninstall(home)
    assert any(".claude.json" in x for x in the_plan["unreadable"]), (
        "an unparseable shared file must be UNREADABLE, not classified as having no keys of ours"
    )

    receipt = apply_uninstall(home)
    assert any("unreadable" in r for r in receipt["refused"])
    assert (home / ".claude.json").read_text().startswith('{"projects"'), "left exactly as found"


def test_a_clean_machine_plans_NOTHING(tmp_path, monkeypatch):
    """Second control: on a home with no empirica install the plan is empty
    rather than erroring or inventing work.

    `monkeypatch.chdir` is load-bearing — the listener resolver reads
    `Path.cwd()/.empirica/project.yaml`, so run from the empirica repo this test
    would find a real ai_id and fail. A test that reads the working directory is
    measuring the box, not the code.
    """
    (tmp_path / ".claude").mkdir()
    monkeypatch.chdir(tmp_path)
    p = plan_uninstall(tmp_path)
    assert p["totals"] == {"remove": 0, "edit": 0, "report_only": 0, "unreadable": 0, "services": 0}


def test_a_users_OWN_hook_under_a_shared_event_survives(home):
    """The restraint failure one level deeper than the dotted-key form expresses.

    An event key holds a LIST, and a user can register their own hooks under the
    same event as ours. Popping `hooks.PostToolUse` wholesale deletes theirs with
    ours. Found on a real home carrying 12 hook events.
    """
    settings_path = home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["UserPromptSubmit"].append({"hooks": [{"command": "my-own-tool --check"}]})
    settings["hooks"]["PostToolUse"] = [{"hooks": [{"command": "their-linter"}]}]
    settings_path.write_text(json.dumps(settings))

    apply_uninstall(home)

    after = json.loads(settings_path.read_text())["hooks"]
    remaining = json.dumps(after)
    assert "empirica" not in remaining, "our hook entries must go"
    assert "my-own-tool --check" in remaining, "a user hook sharing OUR event must survive"
    assert after["PostToolUse"] == [{"hooks": [{"command": "their-linter"}]}], "an event we never touched is untouched"


def test_an_event_holding_ONLY_our_hooks_is_removed_entirely(home):
    """Positive control for the filter: it must still remove, not merely preserve."""
    apply_uninstall(home)
    after = json.loads((home / ".claude" / "settings.json").read_text())
    assert "UserPromptSubmit" not in after.get("hooks", {}), "an event with only our entries goes"


def test_installed_plugins_is_keyed_name_AT_marketplace(home):
    """`plugins` is keyed `empirica@local`, not `empirica`.

    An exact-key check found nothing and left the registry entry behind — while
    the SAME FILE's `enabledPlugins` used a substring match and worked. Two key
    shapes in one file, one of them handled. A real home also carries the legacy
    `empirica-integration@local`.
    """
    reg = home / ".claude" / "plugins" / "installed_plugins.json"
    reg.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "empirica@local": [{"scope": "user"}],
                    "empirica-integration@local": [{"scope": "user"}],
                    "someone-else@local": [{"scope": "user"}],
                },
            }
        )
    )

    keys = next(e["remove_keys"] for e in plan_uninstall(home)["edit_shared"] if e["path"] == str(reg))
    assert "plugins.empirica@local" in keys
    assert "plugins.empirica-integration@local" in keys, "the legacy key must go too"

    apply_uninstall(home)
    after = json.loads(reg.read_text())["plugins"]
    assert "empirica@local" not in after and "empirica-integration@local" not in after
    assert "someone-else@local" in after, "another plugin's registration must survive"


def test_a_SHARED_marketplace_name_is_never_removed(home):
    """`known_marketplaces` is keyed by marketplace — ours lives under `local`,
    which other plugins share. Removing `local` would unregister them all."""
    km = home / ".claude" / "plugins" / "known_marketplaces.json"
    km.write_text(json.dumps({"local": {"source": "x"}, "claude-plugins-official": {"source": "y"}}))

    plan = plan_uninstall(home)
    touched = [e["path"] for e in plan["edit_shared"]]
    assert str(km) not in touched, "'local' is not ours to remove — other plugins live there"

    apply_uninstall(home)
    assert json.loads(km.read_text()) == {"local": {"source": "x"}, "claude-plugins-official": {"source": "y"}}


def test_the_listener_SERVICE_is_planned_and_torn_down(home, tmp_path, monkeypatch):
    """The gap that mattered most: enumerated in the survey, then not implemented.

    An uninstalled empirica with a live systemd/launchd unit still polling the
    mesh is worse than either a clean uninstall or no uninstall at all — the
    plugin is gone, so nothing surfaces that the daemon is still running.
    """
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("ai_id: empirica\n")
    monkeypatch.chdir(proj)

    plan = plan_uninstall(home)
    assert plan["services"] == [{"kind": "listener", "ai_id": "empirica"}]

    called = {}
    import empirica.core.loop_scheduler.persistent_listener as pl

    monkeypatch.setattr(pl, "uninstall_listener_for", lambda a: called.setdefault("ai_id", a) or True)
    receipt = apply_uninstall(home, plan)
    assert called["ai_id"] == "empirica"
    assert any("listener service" in r for r in receipt["removed"])


def test_no_project_means_no_service_in_the_plan(home, tmp_path, monkeypatch):
    """Positive control: the service entry must depend on there BEING one, not
    appear unconditionally."""
    monkeypatch.chdir(tmp_path)
    assert plan_uninstall(home)["services"] == []


def test_the_plugin_BACKUP_dir_is_reported_not_deleted(home):
    """`<plugin_dir>.bak` holds the USER's modified copies of our files, saved by
    every `setup --force`, and nothing prunes it — 536K / 20 files on one box.

    Reported for the same reason as CLAUDE.md: the contents are their edits. We
    put them there precisely because they were worth keeping, so deleting them
    during uninstall discards the thing the backup existed to save.
    """
    bak = home / ".claude" / "plugins" / "local" / "empirica.bak"
    (bak / "hooks").mkdir(parents=True)
    (bak / "hooks" / "patched.py").write_text("their local patch")

    plan = plan_uninstall(home)
    reported = [r["path"] for r in plan["report_only"]]
    assert str(bak) in reported, "the backup must be surfaced, not silently left"
    assert str(bak) not in plan["remove_ours"], "and never scheduled for deletion"

    apply_uninstall(home, plan)
    assert (bak / "hooks" / "patched.py").read_text() == "their local patch", "their edits survive uninstall"
