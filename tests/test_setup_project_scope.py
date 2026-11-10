"""`setup-claude-code --scope project`: Empirica in one repository, nothing under ~/.claude.

People with an existing Claude Code setup want Empirica only in some projects (David, 2026-11-08). The user scope stays the
default; the project scope writes the hooks into the repo's .claude/settings.local.json (Claude Code's per-user, uncommitted
settings), runs them from the installed package, and loads the prompt through the repo's CLAUDE.md.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.cli.cli_core import create_argument_parser
from empirica.cli.command_handlers import setup_claude_code as scc


@pytest.fixture
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    monkeypatch.chdir(root)
    return root


def _args(*extra):
    return create_argument_parser().parse_args(["setup-claude-code", "--scope", "project", "--output", "json", *extra])


def test_the_parser_takes_scope_and_defaults_to_user():
    assert create_argument_parser().parse_args(["setup-claude-code"]).scope == "user"
    assert _args().scope == "project"


def test_project_scope_writes_hooks_into_the_repo_and_nothing_under_home(repo, tmp_path, capsys):
    rc = scc.setup_project_scope(_args(), repo)

    assert rc == 0
    settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
    commands = [h["command"] for entries in settings["hooks"].values() for e in entries for h in e["hooks"]]
    assert any("sentinel-gate.py" in c for c in commands)
    source = str(scc._get_plugin_source_dir())
    assert all(source in c for c in commands if "sentinel-gate.py" in c)  # run from the package, not a copy
    assert "enabledPlugins" not in settings
    assert not (tmp_path / "home" / ".claude").exists()


def test_the_prompt_loads_through_the_repo_s_claude_md(repo):
    scc.setup_project_scope(_args(), repo)

    assert (repo / ".empirica" / "empirica-system-prompt.md").exists()
    assert "@.empirica/empirica-system-prompt.md" in (repo / "CLAUDE.md").read_text()


def test_an_existing_claude_md_keeps_its_text_and_gains_one_line(repo):
    (repo / "CLAUDE.md").write_text("# mine\n\nOur rules.\n")

    scc.setup_project_scope(_args(), repo)
    scc.setup_project_scope(_args(), repo)  # idempotent

    text = (repo / "CLAUDE.md").read_text()
    assert text.count("@.empirica/empirica-system-prompt.md") == 1
    assert "# mine\n\nOur rules.\n" in text


def test_a_second_run_registers_each_hook_once(repo):
    scc.setup_project_scope(_args(), repo)
    first = (repo / ".claude" / "settings.local.json").read_text()
    scc.setup_project_scope(_args(), repo)

    assert (repo / ".claude" / "settings.local.json").read_text() == first


def test_outside_a_git_repo_it_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)

    assert scc.setup_project_scope(_args(), None) == 1
    assert "git" in capsys.readouterr().out


def test_a_new_claude_md_keeps_agents_md_loaded(repo):
    (repo / "AGENTS.md").write_text("# agents\n")

    scc.setup_project_scope(_args(), repo)

    assert "@AGENTS.md" in (repo / "CLAUDE.md").read_text()


def test_the_local_files_are_ignored_by_git(repo):
    scc.setup_project_scope(_args(), repo)

    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=repo, check=False).returncode == 0

    assert ignored(".claude/settings.local.json")
    assert ignored(".empirica/empirica-system-prompt.md")
    assert not ignored("CLAUDE.md")


# ── --uninstall --scope project ────────────────────────────────────────────────────────────────────────────────────────


def _seed_other_hook(repo):
    """A hook of the project's own, under a path that happens to contain 'empirica'."""
    settings_file = repo / ".claude" / "settings.local.json"
    settings = json.loads(settings_file.read_text())
    settings["hooks"].setdefault("PostToolUse", []).append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash /srv/empirica-tools/lint.sh"}]}
    )
    settings_file.write_text(json.dumps(settings))


def test_the_project_uninstall_plan_names_exactly_what_setup_wrote(repo):
    from empirica.cli.command_handlers.claude_code_uninstall import plan_project_uninstall

    scc.setup_project_scope(_args(), repo)
    plan = plan_project_uninstall(repo, str(scc._get_plugin_source_dir()))

    assert str(repo / ".empirica" / "empirica-system-prompt.md") in plan["remove_ours"]
    edits = {e["path"]: e for e in plan["edit_shared"]}
    assert str(repo / ".claude" / "settings.local.json") in edits
    assert any(
        "CLAUDE.md" in str(r.get("path", r)) for r in plan["report_only"]
    )  # the user's file: reported, not edited


def test_applying_it_removes_ours_and_keeps_the_project_s_own_hooks(repo):
    from empirica.cli.command_handlers.claude_code_uninstall import apply_uninstall, plan_project_uninstall

    scc.setup_project_scope(_args(), repo)
    _seed_other_hook(repo)
    source = str(scc._get_plugin_source_dir())

    apply_uninstall(the_plan=plan_project_uninstall(repo, source))

    settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
    commands = [h["command"] for entries in settings.get("hooks", {}).values() for e in entries for h in e["hooks"]]
    assert commands == ["bash /srv/empirica-tools/lint.sh"]
    assert "statusLine" not in settings
    assert not (repo / ".empirica" / "empirica-system-prompt.md").exists()
    assert "@.empirica/empirica-system-prompt.md" in (repo / "CLAUDE.md").read_text()  # left for the person


def test_uninstall_with_scope_project_is_a_plan_unless_applied(repo, capsys):
    scc.setup_project_scope(_args(), repo)
    before = (repo / ".claude" / "settings.local.json").read_text()
    capsys.readouterr()

    rc = scc.handle_setup_claude_code_command(
        create_argument_parser().parse_args(
            ["setup-claude-code", "--scope", "project", "--uninstall", "--output", "json"]
        )
    )

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert (repo / ".claude" / "settings.local.json").read_text() == before


def test_force_in_project_scope_replaces_its_own_hooks_and_status_line_and_keeps_the_project_s(repo):
    """--force clears Empirica's entries before registering them again; in project scope they run from the package's plugin
    directory, not ~/.claude/plugins/local/empirica, so that is what --force must match."""
    scc.setup_project_scope(_args(), repo)
    _seed_other_hook(repo)
    settings_file = repo / ".claude" / "settings.local.json"
    settings = json.loads(settings_file.read_text())
    for entries in settings["hooks"].values():  # an older install: the same hooks with a stale timeout
        for e in entries:
            for h in e["hooks"]:
                if "sentinel-gate.py" in h["command"]:
                    h["timeout"] = 1
    settings_file.write_text(json.dumps(settings))

    scc.setup_project_scope(_args("--force"), repo)

    after = json.loads(settings_file.read_text())
    gate = [h for es in after["hooks"].values() for e in es for h in e["hooks"] if "sentinel-gate.py" in h["command"]]
    assert gate and all(h["timeout"] == 10 for h in gate), "--force registers Empirica's hooks fresh"
    assert len(gate) == 2, "once each, not added again beside the stale copies"
    commands = [h["command"] for es in after["hooks"].values() for e in es for h in e["hooks"]]
    assert "bash /srv/empirica-tools/lint.sh" in commands, "the project's own hook stays"


# ── 1.14.23 sweep (finding fddc9b63) ─────────────────────────────────────────────────────────────────────────────────────


def test_hooks_from_a_moved_package_path_are_replaced_not_kept(repo):
    """A Python upgrade or a reinstall moves the package; the old entries must not linger beside, or instead of, the new."""
    settings_file = repo / ".claude" / "settings.local.json"
    settings_file.parent.mkdir(parents=True)
    old = "python3 /old/lib/python3.12/site-packages/empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"
    settings_file.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": old}]}]}}))

    scc.setup_project_scope(_args(), repo)

    commands = [
        h["command"] for es in json.loads(settings_file.read_text())["hooks"].values() for e in es for h in e["hooks"]
    ]
    assert old not in commands
    assert sum("sentinel-gate.py" in c for c in commands) == 2  # Edit|Write and Bash, from the current path


def test_the_project_uninstall_finds_entries_from_an_older_package_path(repo):
    from empirica.cli.command_handlers.claude_code_uninstall import plan_project_uninstall

    settings_file = repo / ".claude" / "settings.local.json"
    settings_file.parent.mkdir(parents=True)
    old = "python3 /old/site-packages/empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"
    settings_file.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"command": old}]}]}}))

    plan = plan_project_uninstall(repo, scc.PROJECT_PLUGIN_MARKER)

    assert any(e["path"] == str(settings_file) and "hooks.PreToolUse" in e["remove_keys"] for e in plan["edit_shared"])


@pytest.mark.parametrize("extra", [["--harness", "codex"], ["--apply"]])
def test_project_scope_keeps_the_harness_and_apply_refusals(repo, capsys, extra):
    args = create_argument_parser().parse_args(["setup-claude-code", "--scope", "project", "--output", "json", *extra])
    rc = scc.handle_setup_claude_code_command(args)
    assert rc != 0
    assert not (repo / ".claude" / "settings.local.json").exists()


def test_project_scope_is_refused_beside_a_machine_wide_install(repo, tmp_path, capsys):
    home_claude = tmp_path / "home" / ".claude"
    home_claude.mkdir(parents=True)
    gate = "python3 " + str(home_claude) + "/plugins/local/empirica/hooks/sentinel-gate.py"
    (home_claude / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"command": gate}]}]}}))

    rc = scc.setup_project_scope(_args(), repo)

    assert rc == 1 and "twice" in capsys.readouterr().out
    assert not (repo / ".claude" / "settings.local.json").exists()


def test_a_settings_file_changed_during_setup_is_reported_not_raised(repo, monkeypatch, capsys):
    def racing(*_a, **_k):
        raise scc.ConcurrentlyModified("changed while setup prepared its write")

    monkeypatch.setattr(scc, "_write_json_file", racing)
    rc = scc.setup_project_scope(_args(), repo)
    assert rc == 1 and "changed" in capsys.readouterr().out


def test_the_project_uninstall_leaves_the_person_s_plugin_and_server_entries(repo):
    """The project install writes no enabledPlugins, plugins or mcpServers, so any found there are the person's."""
    from empirica.cli.command_handlers.claude_code_uninstall import apply_uninstall, plan_project_uninstall

    scc.setup_project_scope(_args(), repo)
    settings_file = repo / ".claude" / "settings.local.json"
    settings = json.loads(settings_file.read_text())
    settings["enabledPlugins"] = {"empirica@local": True}
    settings["mcpServers"] = {"empirica": {"command": "their-own"}}
    settings_file.write_text(json.dumps(settings))

    apply_uninstall(the_plan=plan_project_uninstall(repo, str(scc._get_plugin_source_dir())))

    after = json.loads(settings_file.read_text())
    assert after["enabledPlugins"] == {"empirica@local": True}
    assert after["mcpServers"] == {"empirica": {"command": "their-own"}}


def test_the_project_uninstall_reports_the_mesh_allow_entry_and_leaves_it(repo):
    from empirica.cli.command_handlers.claude_code_uninstall import apply_uninstall, plan_project_uninstall

    scc.setup_project_scope(_args(), repo)
    plan = plan_project_uninstall(repo, str(scc._get_plugin_source_dir()))
    assert any("mcp__cortex__*" in str(r.get("why", "")) for r in plan["report_only"])
    apply_uninstall(the_plan=plan)
    allow = json.loads((repo / ".claude" / "settings.local.json").read_text())["permissions"]["allow"]
    assert "mcp__cortex__*" in allow
